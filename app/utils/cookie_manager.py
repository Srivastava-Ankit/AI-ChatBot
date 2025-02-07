import json
import logging
import os
from cryptography.fernet import Fernet
from app.db.redis_client import get_redis_client

from app.dg_component.cookie_utils import create_cookie_dict, create_cookie_object
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn

log = get_logger(__name__)


class CookieStoreManager:
    def __init__(self, redis_client):
        self.redis_client = redis_client
        self.encryption_key = os.environ.get('COOKIE_ENCRYPTION_KEY').encode()
        self.fernet = Fernet(self.encryption_key)

    def store_cookies(self, session_id, cookies, host=None, expiry=60 * 60):
        """
        Stores the cookies in Redis after encrypting them.

        Args:
            session_id (str): The session ID to associate with the cookies.
            cookies (Cookies): The cookies to store.
            host (str, optional): The host for which the cookies are valid.
            expiry (int, optional): The expiration time for the cookies in seconds. Defaults to 1 hour.
        """
        try:
            cookie_obj = create_cookie_object(cookies, host)
            cookie_dict = create_cookie_dict(cookie_obj)
            cookie_json = json.dumps(cookie_dict)
            encrypted_cookies = self.fernet.encrypt(cookie_json.encode())
            self.redis_client.set(f"cookies_{session_id}", encrypted_cookies)
            if expiry:
                self.redis_client.expire(name=f"cookies_{session_id}", time=expiry)

            log_info(log, f"Cookies stored successfully for session_id: {session_id}")
        except Exception as e:
            log_error(log, f"Failed to store cookies for session_id: {session_id}. Error: {e}")

    def retrieve_cookies(self, session_id, host=None):
        """
        Retrieves the cookies from Redis and decrypts them.

        Args:
            session_id (str): The session ID associated with the cookies.
            host (str, optional): The host for which the cookies are valid.

        Returns:
            Cookies: The decrypted cookies object, or None if retrieval fails.
        """
        try:
            encrypted_cookies = self.redis_client.get(f"cookies_{session_id}")
            if encrypted_cookies is None:
                log_warn(log, f"No cookies found for session_id: {session_id}")
                return None
            decrypted_cookies = self.fernet.decrypt(encrypted_cookies)
            cookie_dict = json.loads(decrypted_cookies.decode())
            cookie_obj = create_cookie_object(cookie_dict, host)
            log_info(log, f"Cookies retrieved successfully for session_id: {session_id}")
            return cookie_obj
        except Exception as e:
            log_error(log, f"Failed to retrieve cookies for session_id: {session_id}. Error: {e}")
            return None


cookie_manager = CookieStoreManager(redis_client=get_redis_client())