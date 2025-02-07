from typing import Any
import httpx
from httpx import Cookies
from http.cookiejar import CookieJar
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
import traceback
from ddtrace import tracer

log = get_logger(__name__)

@tracer.wrap(name="dd_trace.create_cookie_object", service="degreed-coach-builder")
def create_cookie_object(cookies: Any, host: Any) -> httpx.Cookies:
    """
    Creates an httpx.Cookies object from a dictionary of cookies.

    Args:
        cookies (dict): A dictionary where the key is the cookie name and the value is another dictionary
                        containing 'value' and optionally 'domain'.

    Returns:
        Cookies: An httpx.Cookies object populated with the provided cookies.

    Example usage:
        cookies_dict = {
            'session_id': {'value': 'abc123', 'domain': 'example.com'},
            'user_id': {'value': 'user456'}
        }
        cookies_obj = create_cookie_object(cookies_dict)
    """
    try:
        cookies_obj = httpx.Cookies()
        if isinstance(cookies, dict):
            for name, value in cookies.items():
                if isinstance(value, dict):
                    cookies_obj.set(name=name, value=value['value'], domain=value.get('domain', host))
                else:
                    cookies_obj.set(name=name, value=value, domain=host)
        elif isinstance(cookies, httpx.Cookies):
            return cookies
        else:
            for cookie in cookies.split(';'):
                name, value = cookie.strip().split('=', 1)
                cookies_obj.set(name=name, value=value, domain=host)

        return cookies_obj
    except Exception as e:
        log_error(log, f"Error in create_cookie_object: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.create_cookie_dict", service="degreed-coach-builder")
def create_cookie_dict(cookies: Cookies) -> dict:
    """
    Creates a dictionary from an httpx.Cookies object.

    Args:
        cookies (Cookies): An httpx.Cookies object.

    Returns:
        dict: A dictionary where the key is the cookie name and the value is another dictionary
              containing 'value' and optionally 'domain'.

    Example usage:
        cookies_obj = httpx.Cookies()
        cookies_obj.set('session_id', 'abc123', domain='example.com')
        cookies_dict = create_cookie_dict(cookies_obj)
    """
    try:
        cookies_dict = {}
        if isinstance(cookies.jar, CookieJar):
            for cookie in cookies.jar:
                cookie_data = {'value': cookie.value}
                if cookie.is_expired():
                    continue
                if cookie.domain:
                    cookie_data['domain'] = cookie.domain.strip('.')
                cookies_dict[cookie.name] = cookie_data
        return cookies_dict
    except Exception as e:
        log_error(log, f"Error in create_cookie_dict: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.create_cookie_dict",service="degreed-coach-builder")
def create_cookie_dict_(cookies: Cookies) -> dict:
    """
    Creates a dictionary from an httpx.Cookies object.

    Args:
        cookies (Cookies): An httpx.Cookies object.

    Returns:
        dict: A dictionary where the key is the cookie name and the value is another dictionary
              containing 'value' and optionally 'domain'.

    Example usage:
        cookies_obj = httpx.Cookies()
        cookies_obj.set('session_id', 'abc123', domain='example.com')
        cookies_dict = create_cookie_dict(cookies_obj)
    """
    try:
        cookies_dict = {}
        if isinstance(cookies.jar, CookieJar):
            for cookie in cookies.jar:
                cookie_data = {'value': cookie.value}
                if cookie.is_expired():
                    continue
                if cookie.domain:
                    cookie_data['domain'] = cookie.domain.strip('.')
                cookies_dict[cookie.name] = cookie_data
        return cookies_dict
    except Exception as e:
        log_error(log, f"Error in create_cookie_dict: {e}")
        log_debug(log, traceback.format_exc())
        raise
