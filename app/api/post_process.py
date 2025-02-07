import json
from urllib.parse import urlparse
import uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import httpx
from app.config import BASE_URL, session
from app.db.redis_manager import RedisManager
from app.dg_component.login import login_
from app.dg_component.login_utils import validate_auth_cookies
import traceback
from ddtrace import tracer

from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.post_process.extract_info_v3 import ExtractInfoV3
from app.request_and_response.request import ConversationInfoExtract
from app.utils.cookie_manager import cookie_manager
from starlette.requests import Request

router = APIRouter()
log = get_logger(__name__)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.extract_conversation_info",service="degreed-coach-builder")
@router.post("/extract_conversation_info", tags=["Post Process"])
async def extract_conversation_info(request: Request, input_data: ConversationInfoExtract):
    """
    Extract conversation information from the chat data.

    Args:
        input_data (ConversationInfoExtract): The input data containing user, coach, and conversation details.

    Returns:
        JSONResponse: The extracted conversation information.
    """
    try:
        session_id = str(uuid.uuid4())
        data = {
            "session_id": session_id,
            "cookies": input_data.cookies
        }

        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header:
            token = auth_header.split(" ")[1]
        host = "https://" + input_data.host
        redis_manager.store_base_url(sid=session_id, base_url=host)
        if not host:
            log_error(log, "BASE_URL is not set in environment variables")
            raise HTTPException(status_code=500, detail="Server configuration error")
        parsed_url = urlparse(host)
        host = parsed_url.netloc

    # Perform login for development testing
    # await login_(session_id, username="degassistant28325", password="F8?oi3dsfiBz") 

        # Handle cookies if provided
        if data["cookies"]:

            cookie_manager.store_cookies(session_id=session_id, cookies=data["cookies"], host=host)

        elif token:
            data["token"] = token

        # Store session data in Redis
        redis_manager.add_object(f"user_session_{data['session_id']}", json.dumps(data))

        # Validate cookies if no token is provided
        if not token:
            valid = await validate_auth_cookies(session_id)
            if not valid:
                log_error(log, "HTTP Status Code : 401, Error: Invalid Cookies!!!")
                raise HTTPException(status_code=401, detail="Invalid Cookies")


        log_info(log, f"Extracting conversation information for user {input_data.userProfileKey} and coach {input_data.coachId}")
        input_data_dict = input_data.dict()
        messages = input_data_dict["messages"]
        session_ids = list(set(message.get("sessionId") for message in messages))
        additional_data = {}
        for sid in session_ids:
            call_data = redis_manager.retrieve_call_id_data(sid)
            if call_data.get('pathway'):
                additional_data["pathway"] = call_data.get('pathway')
            if call_data.get('skill'):
                additional_data["skill"] = call_data.get('skill')
            if "pathway" and "skill" in additional_data:
                break

        # Sort messages by messageId and filter required keys
        sorted_messages = sorted(messages, key=lambda x: x["messageId"])
        filtered_messages = [
            {
                "senderType": msg["senderType"],
                "messageText": msg["messageText"],
                "messageTimestamp": msg["messageTimestamp"]
            }
            for msg in sorted_messages
        ]

        inferenced_data = input_data_dict["inferences"]
        structured_inferenced_data = {}
        for infer_data in inferenced_data:
            structured_inferenced_data[infer_data["inferenceType"]] = infer_data["inferredData"]

        structured_inferenced_data["messages"] = filtered_messages
        structured_inferenced_data["startedAt"] = input_data.startedAt
        structured_inferenced_data["endedAt"] = input_data.endedAt

        extract_info = ExtractInfoV3(user_id=input_data.userProfileKey, 
                                     coach_id=input_data.coachId, 
                                     chat_datas=structured_inferenced_data,
                                     coach_data=input_data.coach,
                                     sid=session_id,
                                     additional_info=additional_data)
        await extract_info.initialize()

        extracted_data = await extract_info.trigger_post_process()
        input_data_dict["inferences"] = [{"inferenceType": k, "inferredData": json.dumps(v)} for k, v in extracted_data.items()]
        input_data_dict["conversationSummary"] = extracted_data.get("ConversationOneLiner", {}).get("conversation_one_liner", "No Summary Available")
        input_data_dict["summaries"] = [{"summaryText": extracted_data.get("ConversationSummary", {}).get("conversation_summary", "No Summary Available")}]
        input_data_dict["recommendations"] = extracted_data["recommendations"] if "recommendations" in extracted_data else []

        # Check for duplicates in recommendations
        seen = set()
        unique_recommendations = []
        for recommendation in input_data_dict["recommendations"]:
            if not recommendation:
                log_warn(log, f"Invalid recommendation found: {recommendation}")
                continue
            identifier = (recommendation.get("RecommendedItemId"), recommendation.get("RecommendedItemType"))
            if identifier not in seen:
                seen.add(identifier)
                unique_recommendations.append(recommendation)
            else:
                log_warn(log, f"Duplicate recommendation found: {recommendation}")

        input_data_dict["recommendations"] = unique_recommendations
        log_info(log, f"Recommending {len(input_data_dict['recommendations'])} items to user {input_data.userProfileKey}")
        for key in ["coach", "host", "cookies"]:
            input_data_dict.pop(key, None)

        keys_to_pop = ["messages", "startedAt", "ConversationSummary", "ConversationOneLiner", "endedAt", "recommendations"]
        input_data_dict["inferences"] = [inference for inference in input_data_dict["inferences"] if inference["inferenceType"] not in keys_to_pop]

        # Extract conversation information here
        log_info(log, f"Conversation information extracted for user {input_data.userProfileKey} and coach {input_data.coachId}")
        return JSONResponse(content=input_data_dict, status_code=200)
    except Exception as e:
        log_error(log, f"An error occurred: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal Server Error")

@tracer.wrap(name="dd_trace.extract_partial_conversation_info",service="degreed-coach-builder")
@router.post("/extract_partial_conversation_info", tags=["Post Process"])
async def extract_partial_conversation_info(request: Request, input_data: ConversationInfoExtract):
    """
    Extract conversation information from the chat data.

    Args:
        input_data (ConversationInfoExtract): The input data containing user, coach, and conversation details.

    Returns:
        JSONResponse: The extracted conversation information.
    """
    try:
        session_id = str(uuid.uuid4())
        data = {
            "session_id": session_id,
            "cookies": input_data.cookies
        }

        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header:
            token = auth_header.split(" ")[1]
        host = "https://" + input_data.host
        redis_manager.store_base_url(sid=session_id, base_url=host)
        if not host:
            log_error(log, "BASE_URL is not set in environment variables")
            raise HTTPException(status_code=500, detail="Server configuration error")
        parsed_url = urlparse(host)
        host = parsed_url.netloc

    # Perform login for development testing
    # await login_(session_id, username="degassistant28325", password="F8?oi3dsfiBz") 

        # Handle cookies if provided
        if data["cookies"]:

            cookie_manager.store_cookies(session_id=session_id, cookies=data["cookies"], host=host)

        elif token:
            data["token"] = token

        redis_manager.add_object(f"user_session_{data['session_id']}", json.dumps(data))
        # Validate cookies if no token is provided
        if not token:
            valid = await validate_auth_cookies(session_id)
            if not valid:
                log_error(log, "HTTP Status Code : 401, Error: Invalid Cookies!!!")
                raise HTTPException(status_code=401, detail="Invalid Cookies")

        log_info(log, f"Extracting conversation information for user {input_data.userProfileKey} and coach {input_data.coachId}")
        input_data_dict = input_data.dict()
        messages = input_data_dict["messages"]
        session_ids = list(set(message.get("sessionId") for message in messages))
        additional_data = {}
        for sid in session_ids:
            call_data = redis_manager.retrieve_call_id_data(sid)
            if call_data.get('pathway'):
                additional_data["pathway"] = call_data.get('pathway')
            if call_data.get('skill'):
                additional_data["skill"] = call_data.get('skill')
            if "pathway" and "skill" in additional_data:
                break

        # Sort messages by messageId and filter required keys
        sorted_messages = sorted(messages, key=lambda x: x["messageId"])
        filtered_messages = [
            {
                "senderType": msg["senderType"],
                "messageText": msg["messageText"],
                "messageTimestamp": msg["messageTimestamp"]
            }
            for msg in sorted_messages
        ]

        inferenced_data = input_data_dict["inferences"]
        structured_inferenced_data = {}
        for infer_data in inferenced_data:
            structured_inferenced_data[infer_data["inferenceType"]] = infer_data["inferredData"]

        structured_inferenced_data["messages"] = filtered_messages
        structured_inferenced_data["startedAt"] = input_data.startedAt
        structured_inferenced_data["endedAt"] = input_data.endedAt

        extract_info = ExtractInfoV3(user_id=input_data.userProfileKey, 
                                     coach_id=input_data.coachId, 
                                     chat_datas=structured_inferenced_data,
                                     coach_data=input_data.coach,
                                     sid=session_id,
                                     additional_info=additional_data)
        await extract_info.initialize()

        extracted_data = await extract_info.trigger_partial_post_process()
        input_data_dict["inferences"] = [{"inferenceType": k, "inferredData": json.dumps(v)} for k, v in extracted_data.items()]
        input_data_dict["conversationSummary"] = extracted_data.get("ConversationOneLiner", {}).get("conversation_one_liner", "No Summary Available")
        input_data_dict["summaries"] = [{"summaryText": extracted_data.get("ConversationSummary", {}).get("conversation_summary", "No Summary Available")}]
        input_data_dict["recommendations"] = extracted_data["recommendations"] if "recommendations" in extracted_data else []

        # Check for duplicates in recommendations
        seen = set()
        unique_recommendations = []
        for recommendation in input_data_dict["recommendations"]:
            if not recommendation:
                log_warn(log, f"Invalid recommendation found: {recommendation}")
                continue
            identifier = (recommendation.get("RecommendedItemId"), recommendation.get("RecommendedItemType"))
            if identifier not in seen:
                seen.add(identifier)
                unique_recommendations.append(recommendation)
            else:
                log_warn(log, f"Duplicate recommendation found: {recommendation}")

        input_data_dict["recommendations"] = unique_recommendations
        log_info(log, f"Recommending {len(input_data_dict['recommendations'])} items to user {input_data.userProfileKey}")
        for key in ["coach", "host", "cookies"]:
            input_data_dict.pop(key, None)

        keys_to_pop = ["messages", "startedAt", "ConversationSummary", "ConversationOneLiner", "endedAt", "recommendations"]
        input_data_dict["inferences"] = [inference for inference in input_data_dict["inferences"] if inference["inferenceType"] not in keys_to_pop]

        # Extract conversation information here
        log_info(log, f"Conversation information extracted for user {input_data.userProfileKey} and coach {input_data.coachId}")
        return JSONResponse(content=input_data_dict, status_code=200)
    except Exception as e:
        log_error(log, f"An error occurred: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal Server Error")
