from ddtrace import tracer
import requests
import time
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
import traceback

log = get_logger(__name__)

class RoleToSkill:
    def __init__(self):
        """
        Initialize the RoleToSkill class by obtaining an access token.
        """
        self.token, self.expiry_time = self.get_access_token()

    @tracer.wrap(name="dd_trace.get_access_token",service="degreed-coach-builder")
    def get_access_token(self):
        """
        Retrieve an access token from the authentication server.

        Returns:
            tuple: A tuple containing the access token and its expiry time.

        Raises:
            Exception: If the access token retrieval fails.
        """
        try:
            auth_url = 'https://staging.degreed.com/oauth/token'
            client_id = 'will'
            client_secret = 'will'
            data = {
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': 'users:read users:write content:read content:write completions:read completions:write required_learning:read required_learning:write groups:read groups:write pathways:read recommendations:read views:read search_terms:read skill_plans:read skill:read flex_ed:read logins:read providers:read user_skills:read skill_ratings:read todays_learning:read xapi:read xapi:write xapi:all user_skills:write skill_ratings:write bundles:read bundles:write shared_items:read shared_items:write accomplishments:read accomplishments:write'
            }
            response = requests.post(auth_url, data=data)
            response.raise_for_status()
            token_data = response.json()
            return token_data['access_token'], time.time() + token_data['expires_in'] - 150  # Subtract 5 minutes for buffer
        except requests.RequestException as e:
            log_error(log, f"Failed to retrieve access token: {e}")
            log_debug(log, traceback.format_exc())
            return None, None
        except Exception as e:
            log_error(log, f"An unexpected error occurred: {e}")
            log_debug(log, traceback.format_exc())
            return None, None

    @tracer.wrap(name="dd_trace.get_skills",service="degreed-coach-builder")
    def get_skills(self, role_name):
        """
        Retrieve skills associated with a given role name.

        Args:
            role_name (str): The name of the role to retrieve skills for.

        Returns:
            dict: A dictionary containing the skills for the given role name.

        Raises:
            Exception: If the request to retrieve skills fails.
        """
        try:
            url = "https://dev.ds.degreed.com/skills/fastapi/role-to-skills"
            headers = {'Authorization': f'Bearer {self.token}'}
            data = {'role_name': role_name, 'metadata': 'all'}
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            log_error(log, f"Failed to retrieve skills for role {role_name}: {e}")
            log_debug(log, traceback.format_exc())
            return {}
        except Exception as e:
            log_error(log, f"An unexpected error occurred while retrieving skills for role {role_name}: {e}")
            log_debug(log, traceback.format_exc())
            return {}
