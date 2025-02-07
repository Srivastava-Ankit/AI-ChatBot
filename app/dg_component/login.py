import json
import os
from fastapi import HTTPException
import httpx
import traceback

from app.db.redis_manager import RedisManager
from app.dg_component.login_utils import make_post_rest_request
from app.config import session
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.utils.cookie_manager import cookie_manager
log = get_logger(__name__)
redis_manager = RedisManager()

user = {
    "credentials": {
        "key1": {
            "email": "user@example.com",
            "username": "degassistant28325",
            "password": "F8?oi3dsfiBz"
        }
    }
}

async def login_(sid: str, username: str, password: str) -> httpx.Cookies:
    """
    Handle user login and session management.

    Args:
        sid (str): The session ID for the user.
        username (str): The username for login.
        password (str): The password for login.

    Returns:
        httpx.Cookies: A Cookies object containing the session cookies.

    Raises:
        HTTPException: If the login request fails or the token is not found.
    """
    try:

        login_url = 'http://localhost:8080/login?use_email=false&identity_cookie_exists=true'
        
        # Prepare headers and credentials for the login request
        headers = {
            'Content-Type': 'application/json'
        }
        credentials = json.dumps({
            "credentials": {
                "dev": {
                    "email": "",
                    "username": username,
                    "password": password
                }
            }
        })
        
        # Make the login request
        login_response = await make_post_rest_request(url=login_url, data=credentials, headers=headers)
        
        # Extract the access token from the login response
        token = json.loads(login_response.text)['access_token']
        
        # Retrieve the session cookies from Redis
        cookies = redis_manager.get_object(f"user_session_{token}")
        cookies_dict = json.loads(cookies)
        
        # Convert the cookies dictionary to httpx.Cookies object
        cookies = httpx.Cookies()
        if cookies_dict:
            for name, value in cookies_dict.items():
                cookies.set(name=name, value=value.get('value'), domain=value.get('domain'))
        
            # Store the cookies in the session
            cookie_manager.store_cookies(session_id=sid, cookies=cookies)
            # session[sid] = cookies
        return cookies
    except Exception as e:
        log_error(log, f"Error in login_: {e}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal Server Error")
