from typing import Any, Dict, List, Union
import traceback
from ddtrace import tracer
import inflection
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn

log = get_logger(__name__)

@tracer.wrap(name="dd_trace.normalize_keys",service="degreed-coach-builder")
def normalize_keys(d: Dict[str, Any], case: str = 'lower') -> Dict[str, Any]:
    """
    Normalize the keys of a dictionary to the specified case.
    
    :param d: Dictionary whose keys need to be normalized
    :param case: 'lower' to convert keys to lowercase, 'upper' to convert keys to uppercase
    :return: New dictionary with normalized keys
    """
    try:
        if isinstance(d, dict):
            return {k.lower() if case == 'lower' else k.upper(): normalize_keys(v, case) for k, v in d.items()}
        elif isinstance(d, list):
            return [normalize_keys(v, case) for v in d]
        return d
    except Exception as e:
        log_error(log, f"Error normalizing keys: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.get_value_by_path",service="degreed-coach-builder")
def get_value_by_path(data: Dict[str, Any], path: List[Union[str, int]]) -> Any:
    """
    Get the value from a nested dictionary by path, checking for keys case-insensitively.
    
    :param data: Dictionary to search
    :param path: List of keys representing the path to search
    :return: Value corresponding to the key path, or None if the key path is not found
    """
    try:
        current_level = data
        
        for key in path:
            if isinstance(current_level, dict):
                # For dictionaries, search for the key case-insensitively
                matching_key = next((k for k in current_level.keys() if k.lower() == key.lower()), None) if isinstance(key, str) else key
                if matching_key is not None:
                    current_level = current_level[matching_key]
                else:
                    return None
            elif isinstance(current_level, list) and isinstance(key, int) and 0 <= key < len(current_level):
                # For lists, use the index directly
                current_level = current_level[key]
            else:
                return None
        
        return current_level
    except Exception as e:
        log_error(log, f"Error getting value by path: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.create_headers",service="degreed-coach-builder")
def create_headers(sid: str) -> dict:
    """
    Create headers for the request.

    :param sid: Session ID to be included in the headers
    :return: Dictionary containing the headers
    """
    try:
        return {
            'Content-Type': 'application/json',
            "sid": sid
        }
    except Exception as e:
        log_error(log, f"Error creating headers: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.convert_keys_to_camel_case",service="degreed-coach-builder")
def convert_keys_to_camel_case(object):
    """Recursively converts all keys in a dictionary to camelCase."""
    if isinstance(object, dict):
        return {inflection.camelize(k, uppercase_first_letter=False): convert_keys_to_camel_case(v) for k, v in object.items()}
    elif isinstance(object, list):
        return [convert_keys_to_camel_case(i) for i in object]
    else:
        return object
