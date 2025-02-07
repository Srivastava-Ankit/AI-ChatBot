import asyncio
import json
from typing import Dict
import httpx
from httpx import Timeout
from app.db.redis_manager import RedisManager
from app.config import BASE_URL, session
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
import traceback
from ddtrace import tracer
from app.utils.cookie_manager import cookie_manager

log = get_logger(__name__)
timeout = Timeout(300.0, read=None)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.get_client_session",service="degreed-coach-builder")
async def get_client_session(headers: dict):
    """
    Retrieve the client session based on the provided headers.

    Args:
        headers (dict): The headers containing session or authorization information.

    Returns:
        httpx.AsyncClient: The client session with appropriate cookies or headers set.
    """
    try:
        header = headers.get('sid')
        token = None
        data = None
        if header:
            session_id = headers.get('sid')
            data = redis_manager.get_object(f"user_session_{session_id}")
            data = json.loads(data) if data else None
            if data and data.get('token'):
                token = data["token"]
                log_info(log, f"Token found for session_id: {session_id}")

            client_session = httpx.AsyncClient(verify=False, timeout=timeout, limits=httpx.Limits(max_keepalive_connections=500, max_connections=1000))

            # cookies = session[session_id] if session.get(session_id) else None
            cookies = cookie_manager.retrieve_cookies(session_id)

            if not cookies and not token:
                log_info(log, f"Creating Client session without Cookies for session_id: {session_id}")

            if cookies:
                log_info(log, f"Creating Client session with Cookies for session_id: {session_id}")
                client_session.cookies = cookies
                return client_session
            elif token:
                log_info(log, f"Creating Client session with Token for session_id: {session_id}")
                token = data["token"]
                client_session.cookies = None
                client_session.headers.update({'Authorization': f'Bearer {token}'})
                return client_session
        else:
            token = headers.get('authorization').split(' ')[1]
            log_info(log, f"Returning Token for session_id: {session_id}")
        return data[token] if data.get(token) else None
    except Exception as e:
        log_debug(log, f"Traceback: {traceback.format_exc()}")
        log_error(log, f"Error in get_client_session: {e}\n Traceback: {traceback.format_exc()}")
        raise e

@tracer.wrap(name="dd_trace.extract_token_and_add_crsf",service="degreed-coach-builder")
async def extract_token_and_add_crsf(headers: Dict[str, str]):
    """
    Extract the token and add CSRF headers to the client session.

    Args:
        headers (Dict[str, str]): The headers containing session or authorization information.

    Returns:
        Tuple[httpx.AsyncClient, str]: The client session with updated headers and the token.
    """
    try:
        client_session = await get_client_session(headers)
        session_id = headers.get('sid')

        if not session_id:
            raise ValueError("Session ID is missing in headers.")

        user_data = redis_manager.get_object(f"user_session_{session_id}")
        data = json.loads(user_data) if user_data else None
        token = data.get("token") if data else None

        if not client_session:
            raise Exception("Client session could not be retrieved.")

        if not token:
            csrf_cookie = None
            csrf_vnext_cookie = None

            for cookie in client_session.cookies.jar:
                if not csrf_cookie and cookie.name.startswith('antiforgery-request.v4'):
                    csrf_cookie = cookie.value
                if not csrf_vnext_cookie and cookie.name.startswith('antiforgery-request-vnext.v4'):
                    csrf_vnext_cookie = cookie.value

                if csrf_cookie and csrf_vnext_cookie:
                    break

            custom_headers = {}
            if csrf_cookie:
                custom_headers["X-Xsrf-Token"] = csrf_cookie
            if csrf_vnext_cookie:
                custom_headers["x-xsrf-token-vnext"] = csrf_vnext_cookie

            client_session.headers.update(custom_headers)

        existing_headers = client_session.headers
        existing_headers.update({"Content-Type": "application/json"})

        if token:
            client_session.cookies = None
            existing_headers.update({'Authorization': f'Bearer {token}'})

        # asyncio.create_task(cookie_validation(client_session, session_id))
        return client_session, token
    except Exception as e:
        log_debug(log, f"Traceback: {traceback.format_exc()}")
        log_error(log, f"Error in extract_token_and_add_crsf: {e}")
        raise e

@tracer.wrap(name="dd_trace.cookie_validation",service="degreed-coach-builder")
async def cookie_validation(client_session, session_id):
    """
    Validates the cookies by making a request to the authenticated user endpoint.

    This method sends a GET request to the authenticated user endpoint and checks the response status code.
    If the status code is in the 40X range, it logs the status code and the response message.

    Args:
        client_session (ClientSession): The client session to use for the request.

    Returns:
        bool: True if the cookies are valid, False otherwise.
    """
    try:
        base_url = redis_manager.retrieve_base_url(sid=session_id)

        response = await client_session.get(f"{base_url}/api/account/getauthenticateduser")
        
        if 400 <= response.status_code < 500:
            log_warn(log, f"Cookie validation failed with status code: {response.status_code}, message: {response.text} for session_id: {session_id}")
            log_debug(log, f"Request headers: {client_session.headers} Request cookies: {client_session.cookies}")    
            return False
        
        return response.status_code == 200
    except Exception as e:
        log_debug(log, f"Traceback: {traceback.format_exc()}")
        log_error(log, f"Error in cookie_validation: {e}")
        return False
