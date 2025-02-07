import os
from typing import Any, Dict, List
from fastapi.responses import JSONResponse
from httpx import AsyncClient, Response
from fastapi import HTTPException
from app.config import BASE_URL
import traceback
from ddtrace import tracer

from app.dg_component.mentor.request_builder import MentorSearchRequest
from app.dg_component.client_session import extract_token_and_add_crsf
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.utils.default import convert_keys_to_camel_case, create_headers
from app.db.redis_manager import RedisManager
from app.utils.api_utils import is_mobile


log = get_logger(__name__)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.find_mentor",service="degreed-coach-builder")
async def find_mentor(sid: str, mentor_request: MentorSearchRequest) -> Response:
    """
    Find learning resources based on the search request.

    :param sid: Session ID for authorization.
    :param mentor_request: The mentor request object containing mentor parameters.
    :return: The response from the learning resources API.
    :raises HTTPException: If the client session is invalid or the request fails.
    """
    try:
        headers = create_headers(sid)
        ismobile = await is_mobile(sid)
        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")
        
        base_url = redis_manager.retrieve_base_url(sid=sid)

        response = await simulate_request_method(client_session, mentor_request, base_url, ismobile)
        return convert_keys_to_camel_case(response.json())
    except Exception as e:
        log_error(log, f"Error in find_mentor: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.simulate_request_method",service="degreed-coach-builder")
async def simulate_request_method(client_session, mentor_request: MentorSearchRequest, base_url: str, is_mobile: bool) -> Response:
    """
    Simulate the request to find learning resources.

    :param mentor_request: The mentor request object containing search parameters.
    :param client_session: The HTTP client session.
    :return: The response from the learning resources API.
    :raises HTTPException: If the request fails after maximum retries.
    """
    url = f"{base_url}/api/Users/FindUsers"
    params = {
        "terms": mentor_request.terms,
        "count": mentor_request.count,
        "facets": [create_set_values(mentor_request.filters)],
        "skip": mentor_request.skip,
        "organizationId": mentor_request.organization_id,
        "sort": mentor_request.sort,
        "sortDescending": mentor_request.sort_descending,
        "dg-casing": "camel"
    }

    if is_mobile:
        url = f"{base_url}/api/mobile/v2/search/users" ## :: TODO Test the URL
        params = {
            "terms": mentor_request.terms,
            "take": mentor_request.count,
            "facets": [create_set_values(mentor_request.filters)],
            "skip": mentor_request.skip,
            "organizationId": mentor_request.organization_id,
        }

    max_retries = 5
    attempts = 0

    while attempts < max_retries:
        try:
            
            response = await client_session.get(url, params=params)
            log_info(log, f"HTTP Request: GET {response.url} \"{response.status_code} {response.reason_phrase}\"")
            if response.status_code == 200:
                return response
            else:
                attempts += 1
                if attempts == max_retries:
                    log_error(log, f"HTTP Request failed after {max_retries} attempts: {response.text}")
                    raise HTTPException(status_code=response.status_code, detail=response.text)
        except Exception as e:
            log_error(log, f"Error in simulate_request_method(find_mentor): {str(e)}")
            log_debug(log, traceback.format_exc())
            if attempts == max_retries:
                raise HTTPException(status_code=500, detail="Internal Server Error")
            attempts += 1

    return JSONResponse(status_code=500, content={"message": "Internal Server Error"})

@tracer.wrap(name="dd_trace.create_set_values",service="degreed-coach-builder")
def create_set_values(filter_values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Create set values for the search filters.

    :param filter_values: Dictionary of filter values.
    :return: List of filter dictionaries.
    :raises Exception: If an unsupported filter is encountered.
    """
    filters_list = []

    for filter_name, filter_value in filter_values.items():
        if filter_name == "type":
            filter_id = 'Type'
            filter_name = 'Type'
            filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
        elif filter_name == 'jobrole':
            filter_id = 'JobRole'
            filter_name = 'JobRole'
            filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
        elif filter_name == 'location':
            filter_id = 'Location'
            filter_name = 'Location'
            filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
        elif filter_name == 'mentors':
            filter_id = 'Mentors'
            filter_name = 'Mentors'
            filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
        elif filter_name == 'activelearners':
            filter_id = 'ActiveLearners'
            filter_name = 'ActiveLearners'
            filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
        else:
            raise Exception(f"Filter Not Supported: {filter_name}")

        if filter_values_list:
            filter_dict = {
                'id': filter_id,
                'name': filter_name,
                'values': filter_values_list,
            }
            filters_list.append(filter_dict)

    return filters_list
