import asyncio
from typing import Any, Optional
import aiohttp
import traceback

from fastapi import HTTPException
from httpx import Response
from pydantic import BaseModel, Field
from app.config import BASE_URL
from ddtrace import tracer

from app.dg_component.client_session import extract_token_and_add_crsf
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.utils.default import convert_keys_to_camel_case, create_headers
from app.utils.role_to_skill import RoleToSkill
from app.db.redis_manager import RedisManager
from app.utils.api_utils import is_mobile

log = get_logger(__name__)
redis_manager = RedisManager()

class GetUserInput(BaseModel):
    userKey: Any

class UserTagsParams(BaseModel):
    includeRatings: bool = True
    focusedOnly: bool = False


@tracer.wrap(name="dd_trace.get_user",service="degreed-coach-builder")
async def get_user(headers: dict, is_mobile: bool):
    """
    Retrieves user details based on the provided user key.

    Args:
        headers (dict): A dictionary of HTTP headers used for authentication and session management.

    Returns:
        dict: The server response containing the user's details.

    Raises:
        HTTPException: If the authorization fails or the server response is not successful.

    Example usage:
        response = await get_user(headers)
    """
    try:
        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")

        base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))
        if is_mobile:
            response = await client_session.get(f"{base_url}/api/mobile/user") ## :: TODO Test the URL
        else:
            response = await client_session.get(f"{base_url}/api/User/GetUser")

        if response.status_code not in (200, 302):
            raise HTTPException(status_code=response.status_code, detail=response.text)

        return convert_keys_to_camel_case(response.json())
    except Exception as e:
        log_error(log, f"Error in get_user: {e}")
        log_debug(log, traceback.format_exc())
        raise


@tracer.wrap(name="dd_trace.get_user_tags",service="degreed-coach-builder")
async def get_user_tags(headers: dict, params: UserTagsParams, is_mobile: bool):
    """
    Retrieves user tags based on the provided user key.

    Args:
        headers (dict): A dictionary of HTTP headers used for authentication and session management.
        params (UserTagsParams): An object containing parameters for the request.

    Returns:
        dict: The server response containing the user's tags.

    Raises:
        HTTPException: If the authorization fails or the server response is not successful.

    Example usage:
        response = await get_user_tags(headers, params)
    """
    try:
        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")

        params = {"includeRatings": params.includeRatings, "focusedOnly": params.focusedOnly, "dg-casing": "camel"}
        base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))


        if is_mobile:
            response = await client_session.get(f"{base_url}/api/mobile/user/interests", params=params) ## :: TODO Test the URL
        else:
            response = await client_session.get(f"{base_url}/api/User/GetUserInterests", params=params)

        if response.status_code not in (200, 302):
            raise HTTPException(status_code=response.status_code, detail=response.text)

        return convert_keys_to_camel_case(response.json())
    except Exception as e:
        log_error(log, f"Error in get_user_tags: {e}")
        log_debug(log, traceback.format_exc())
        raise


@tracer.wrap(name="dd_trace.get_user_data",service="degreed-coach-builder")
async def get_user_data(sid: str, tag_preferences: UserTagsParams):
    """
    Retrieves user data based on the provided user key.

    Args:
        sid (str): The session ID for the user.
        tag_preferences (UserTagsParams): An object containing parameters for the request.

    Returns:
        dict: A dictionary containing the user's data and tags.

    Raises:
        HTTPException: If the authorization fails or the server response is not successful.

    Example usage:
        response = await get_user_data(sid, tag_preferences)
    """
    try:
        headers = create_headers(sid)
        # role_to_skill = RoleToSkill()
        ismobile = await is_mobile(sid)

        async with aiohttp.ClientSession() as session:
            tasks = [
                get_user(headers, ismobile),
                get_user_tags(headers, tag_preferences, ismobile)
            ]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            user_response, tags_response = responses

            if isinstance(user_response, Exception):
                raise HTTPException(status_code=500, detail=str(user_response))
            if isinstance(tags_response, Exception):
                raise HTTPException(status_code=500, detail=str(tags_response))
            # Convert keys in tags_response to lowercase
            tags_response = [{k.lower(): v for k, v in skill.items()} for skill in tags_response]
            # To check: Ratings is not available for dew mobile users
            skills = {skill["name"]: next((rating["level"] for rating in skill["ratings"] if rating["type"].lower() == "self"), 0) for skill in tags_response}
            # Convert user_response keys to lowercase
            user_response = {k.lower(): v for k, v in user_response.items()}
            
            response = {
                "name": user_response.get("name", ""),
                "role": user_response.get("jobrole", ""),
                "skills": skills,
                # "inferred_skill": role_to_skill.get_skills(user_response.get("jobrole", "")),
                "knowledge": {}
            }

            if "location" in user_response:
                response["city"] = user_response["location"]
            return response
    except Exception as e:
        log_error(log, f"Error in get_user_data: {e}")
        log_debug(log, traceback.format_exc())
        raise


@tracer.wrap(name="dd_trace.get_user_profile_key",service="degreed-coach-builder")
async def get_user_profile_key(sid: str):
    """
    Retrieves the user profile key based on the provided session ID.

    Args:
        sid (str): The session ID for the user.

    Returns:
        Optional[str]: The user profile key if available, otherwise None.

    Raises:
        HTTPException: If the authorization fails or the server response is not successful.

    Example usage:
        profile_key = await get_user_profile_key(sid)
    """
    try:
        ismobile = await is_mobile(sid)
        headers = create_headers(sid)

        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")

        base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))

        if ismobile:
            response = await client_session.get(f"{base_url}/api/mobile/user") # :: TODO Test the URL
        else:
            response = await client_session.get(f"{base_url}/api/User/GetUser")

        if response.status_code not in (200, 302):
            raise HTTPException(status_code=response.status_code, detail=response.text)

        return convert_keys_to_camel_case(response.json()).get("userProfileKey", None)
    except Exception as e:
        log_error(log, f"Error in get_user_profile_key: {e}")
        log_debug(log, traceback.format_exc())
        raise


@tracer.wrap(name="dd_trace.get_user_org",service="degreed-coach-builder")
async def get_user_org(sid: str):
    """
    Retrieves the user organization ID based on the provided session ID.

    Args:
        sid (str): The session ID for the user.

    Returns:
        Optional[str]: The organization ID if available, otherwise None.

    Raises:
        HTTPException: If the authorization fails or the server response is not successful.

    Example usage:
        org_id = await get_user_org(sid)
    """
    try:
        ismobile = await is_mobile(sid)
        headers = create_headers(sid)

        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")

        base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))
        if ismobile:
            response = await client_session.get(f"{base_url}/api/mobile/user") # :: TODO Test the URL
        else:
            response = await client_session.get(f"{base_url}/api/User/GetUser")

        if response.status_code not in (200, 302):
            raise HTTPException(status_code=response.status_code, detail=response.text)
        if ismobile:
            return convert_keys_to_camel_case(response.json()).get("OrgID", None)
        else:
            return convert_keys_to_camel_case(response.json()).get("organizationId", None)
    except Exception as e:
        log_error(log, f"Error in get_user_org: {e}")
        log_debug(log, traceback.format_exc())
        raise
