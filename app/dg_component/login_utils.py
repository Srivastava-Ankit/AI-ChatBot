import asyncio
import os
import httpx
from fastapi import HTTPException
from httpx import Timeout
from app.config import BASE_URL
import traceback
from ddtrace import tracer

from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.dg_component.client_session import extract_token_and_add_crsf
from app.db.redis_manager import RedisManager
from app.utils.api_utils import is_mobile

log = get_logger(__name__)
timeout = Timeout(120.0, read=None)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.validate_auth_cookies",service="degreed-coach-builder")
async def validate_auth_cookies(session_id: str) -> bool:
    """
    Validates the authentication cookies using the provided session ID.

    Args:
        session_id (str): The session ID for the user.

    Returns:
        bool: True if the authentication cookies are valid.

    Raises:
        HTTPException: If the authorization fails or the user information cannot be retrieved.
    """
    try:
        headers = {
            'Content-Type': 'application/json',
            'sid': session_id
        }
        response = await get_authenticated_user(headers)
        if response.status_code not in (200, 302, 201):
            log_error(log, f"HTTP Status Code: {response.status_code} Error: {response.text}")
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return True
    except Exception as e:
        log_error(log, f"Error in validate_auth_cookies: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.make_post_rest_request",service="degreed-coach-builder")
async def make_post_rest_request(url: str, data: dict, headers: dict, retries: int = 3, delay: int = 1, retry: bool = True) -> httpx.Response:
    """
    Makes a POST request to the specified URL with the provided data and headers.

    Args:
        url (str): The URL to send the POST request to.
        data (dict): The data to include in the POST request.
        headers (dict): The headers to include in the POST request.
        retries (int, optional): The number of retry attempts. Defaults to 3.
        delay (int, optional): The delay between retry attempts in seconds. Defaults to 1.
        retry (bool, optional): Whether to retry the request on failure. Defaults to True.

    Returns:
        httpx.Response: The response from the POST request.

    Raises:
        HTTPException: If the request fails after the specified number of retries.
    """
    try:
        if not retry:
            async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
                log_info(log, f"HTTP POST Request: URL: {url}")
                response = await client.post(url, data=data, headers=headers, timeout=timeout)
                return response
        else:
            for attempt in range(retries):
                try:
                    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
                        log_info(log, f"HTTP POST Request: URL: {url}")
                        response = await client.post(url, data=data, headers=headers, timeout=timeout)
                        response.raise_for_status()  # Raises an exception for 4XX/5XX errors
                        return response
                except httpx.HTTPError as e:
                    log_error(log, f"Request failed: {e}. Attempt {attempt + 1} of {retries}. Retrying in {delay} seconds...")
                    log_debug(log, traceback.format_exc())
                    if attempt < retries - 1:
                        await asyncio.sleep(delay)
                    else:
                        raise
                except Exception as e:
                    log_error(log, f"An error occurred: {e}.")
                    log_debug(log, traceback.format_exc())
                    raise
    except Exception as e:
        log_error(log, f"Error in make_post_rest_request: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.get_authenticated_user",service="degreed-coach-builder")
async def get_authenticated_user(headers: dict) -> httpx.Response:
    """
    Retrieves the authenticated user's information.

    Args:
        headers (dict): A dictionary of HTTP headers received with the request.

    Returns:
        httpx.Response: The response containing the authenticated user's information.

    Raises:
        HTTPException: If the authorization fails or the user information cannot be retrieved.

    Example usage:
        Assuming you have a function to get headers from a request
        response = await get_authenticated_user(request_headers)
        if response.status_code == 200:
            user_info = response.json()
            # Process user_info as needed
    """
    try:
        ismobile = await is_mobile(headers.get('sid'))
        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")

        base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))

        if ismobile:
            response = await client_session.get(f"{base_url}/api/mobile/user")
        else:
            response = await client_session.get(f"{base_url}/api/Account/GetAuthenticatedUser")
            
        response.raise_for_status()

        return response
    except Exception as e:
        log_error(log, f"Error in get_authenticated_user: {e}")
        log_debug(log, traceback.format_exc())
        raise
