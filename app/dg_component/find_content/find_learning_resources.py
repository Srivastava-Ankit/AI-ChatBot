import os
from typing import Any, Dict, List
from fastapi.responses import JSONResponse
from httpx import AsyncClient, Response
from fastapi import HTTPException
from app.config import BASE_URL
import traceback
from ddtrace import tracer  

from app.dg_component.find_content.request_builder import ContentSearchRequest
from app.dg_component.client_session import extract_token_and_add_crsf
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.utils.default import convert_keys_to_camel_case, create_headers
from app.db.redis_manager import RedisManager
from app.utils.api_utils import is_mobile


log = get_logger(__name__)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.find_learning_resources",service="degreed-coach-builder")
async def find_learning_resources(sid: str, search_request: ContentSearchRequest) -> Response:
    """
    Find learning resources based on the search request.

    :param sid: Session ID for authorization.
    :param search_request: The search request object containing search parameters.
    :return: The response from the learning resources API.
    :raises HTTPException: If the client session is invalid or the request fails.
    """
    try:
        headers = create_headers(sid)
        
        isMobile = await is_mobile(sid)

        client_session, token = await extract_token_and_add_crsf(headers)
        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")
        
        base_url = redis_manager.retrieve_base_url(sid=sid)
        response = await simulate_request_method(client_session, search_request, base_url, isMobile)
        return convert_keys_to_camel_case(response.json())
    except Exception as e:
        log_error(log, f"Error in find_learning_resources: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.simulate_request_method",service="degreed-coach-builder")
async def simulate_request_method(client_session, search_request: ContentSearchRequest, base_url: str, isMobile: bool) -> Response:
    """
    Simulate the request to find learning resources.

    :param search_request: The search request object containing search parameters.
    :param client_session: The HTTP client session.
    :return: The response from the learning resources API.
    :raises HTTPException: If the request fails after maximum retries.
    """
    try:

        url = f"{base_url}/api/search/findlearningresources"        
        if isMobile:
            url = f"{base_url}/api/mobile/v2/search/resources"

        params = {
            "terms": search_request.terms,
            "count": search_request.count,
            "inputsOnly": search_request.inputsOnly,
            "facets": [create_set_values(search_request.filters)],
            "includesProviders": search_request.includesProviders,
            "boostRecent": search_request.boostRecent,
            "boostPopular": search_request.boostPopular,
            "useResourceImages": search_request.useResourceImages,
            "persistFilter": search_request.persistFilter,
            "skip": search_request.skip,
            "dg-casing": "camel",
        }
        max_retries = 5
        attempts = 0

        while attempts < max_retries:
            response = await client_session.get(url, params=params)
            log_info(log, f"HTTP Request: GET {response.url} \"{response.status_code} {response.reason_phrase}\"")
            if response.status_code == 200:
                return response
            else:
                attempts += 1
                if attempts == max_retries:
                    log_error(log, f"HTTP Request failed after {max_retries} attempts: {response.text}")
                    raise HTTPException(status_code=response.status_code, detail=response.text)
                continue
        return JSONResponse(status_code=500, content={"message": "Internal Server Error"})
    except Exception as e:
        log_error(log, f"Error in simulate_request_method(find_content): {str(e)}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.create_set_values",service="degreed-coach-builder")
def create_set_values(filter_values: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Create set values for the search filters.

    :param filter_values: Dictionary of filter values.
    :return: List of filter dictionaries.
    :raises Exception: If an unsupported filter is encountered.
    """
    try:
        filters_list = []

        for filter_name, filter_value in filter_values.items():
            if filter_name == 'Duration':
                filter_id = 'Duration'
                filter_name = 'Duration'
                filter_values_list = create_duration_values(filter_value)
            elif filter_name == 'orgId':
                filter_id = 'Internal'
                filter_name = 'Internal'
                filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
            elif filter_name == 'endorsed':
                filter_id = 'Endorsed'
                filter_name = 'Endorsed'
                filter_values_list = [filter_value] if not isinstance(filter_value, list) else filter_value
            elif filter_name == 'type':
                filter_id = 'Type'
                filter_name = 'Type'
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
                if filter_dict.get("id") == "Type" and filter_dict.get("values") == ["book"]:
                    filters_list.append({"id": "PublishDate", "name": "PublishDate", "values": ["LessThanOneYear"]})

        return filters_list
    except Exception as e:
        log_error(log, f"Error in create_set_values: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.create_duration_values",service="degreed-coach-builder")
def create_duration_values(filter_value: Any) -> List[str]:
    """
    Create duration values for the duration filter.

    :param filter_value: The filter value(s) for duration.
    :return: List of duration values.
    """
    try:
        duration_filter_mapping = {
            "<5m": "LessThan5",
            "<10m": "LessThan10",
            "<30m": "LessThan30",
            "<1h": "LessThan1Hour",
            "<4h": "LessThan4Hours",
            "<1d": "LessThan1Day",
            ">1d": "GreaterThan1Day",
        }
        filter_values = []
        if filter_value and isinstance(filter_value, list):
            for filter in filter_value:
                value = duration_filter_mapping.get(filter)
                filter_values.append(value)
        else:
            value = duration_filter_mapping.get(filter_value)
            filter_values.append(value)
        return filter_values
    except Exception as e:
        log_error(log, f"Error in create_duration_values: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise