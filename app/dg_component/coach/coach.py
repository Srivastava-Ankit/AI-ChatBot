
import traceback
from fastapi import HTTPException
import httpx
from app.config import BASE_URL
from app.dg_component.client_session import extract_token_and_add_crsf
from app.utils.default import convert_keys_to_camel_case, create_headers
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from ddtrace import tracer

from app.db.redis_manager import RedisManager
from app.utils.api_utils import is_mobile  

log = get_logger(__name__)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.get_coach",service="degreed-coach-builder")
async def get_coach(sid: str, coach_id: str):
    """
    Fetch coach details by coach_id.

    :param sid: Session ID for authorization.
    :param coach_id: ID of the coach to fetch.
    :return: Response object containing coach details.
    :raises HTTPException: If authorization is invalid or request fails.
    """
    headers = create_headers(sid)
    client_session, token = await extract_token_and_add_crsf(headers)

    if not client_session:
        raise HTTPException(status_code=403, detail="Invalid Authorization")

    try:
        # Make the GET request to fetch coach details
        base_url = redis_manager.retrieve_base_url(sid=sid)
        ismobile = await is_mobile(sid)
        if ismobile:
            response = await client_session.get(f"{base_url}/api/mobile/coaches/{coach_id}")
        else:
            response = await client_session.get(f"{base_url}/api/Coach/Get/{coach_id}")

        response.raise_for_status()
        return convert_keys_to_camel_case(response.json())
    except httpx.HTTPStatusError as e:
        # Log the error and traceback
        log_error(log, f"HTTP error occurred: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        # Log any other exceptions and traceback
        log_error(log, f"An unexpected error occurred: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal Server Error")
