import datetime
import json
import os
import uuid
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from urllib.parse import urlparse
import httpx
import traceback
from ddtrace import tracer
import pytz
from app.db.redis_manager import RedisManager
from app.dg_component.login import login_
from app.config import BASE_URL
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.dg_component.login_utils import validate_auth_cookies
from app.config import session
from app.utils.cookie_manager import cookie_manager
from app.llm.prompt_preprocessor import PromptPreprocessor
from pydantic import BaseModel, validator
from typing import Optional, Dict
from livekit.api import ListRoomsRequest, ListParticipantsRequest, LiveKitAPI

log = get_logger(__name__)

router = APIRouter()

def livekit_to_dict(room):
    from google.protobuf.json_format import MessageToDict
    return MessageToDict(room)

class RegisterCallRequest(BaseModel):
    coachId: int
    userProfileKey: int
    timeZone: str
    conversationId: Optional[int]
    skill: Optional[Dict]
    pathwayDetails: Optional[Dict]
    cookies: Optional[Dict]
    host: str
    voice: Optional[str] = "alloy"

    @validator('voice')
    def validate_voice(cls, value):
        allowed_voices = ["alloy", "echo", "shimmer"]
        if value not in allowed_voices:
            raise ValueError(f"Voice must be one of {allowed_voices}")
        return value


@tracer.wrap(name="dd_trace.handle_realtime_register_call",service="degreed-coach-builder")
@router.post("/register-realtime-call", tags=["Realtime"])
async def handle_realtime_register_call(request: Request, call_request: RegisterCallRequest):
    """
    Register a call on your server

    This endpoint is used to register a call on your server. This will return a call_id which will be used to start the call.

    Request Body:
    - coachId: str
    - userId: str
    - sample_rate: int
    - time_zone: str
    - skill: dict

    Response:
    - call_id: str

    """
    log_info(log, "Registering call on your server")

    try:
        session_id = str(uuid.uuid4())
        
        redis_manager = RedisManager()
        session_data = {
            "coach_id": call_request.coachId,
            "user_id": call_request.userProfileKey,
            "time_zone": call_request.timeZone,
            "conversation_id": call_request.conversationId,
            "skill": call_request.skill or {},
            "pathway": call_request.pathwayDetails or {},
        }
        redis_manager.store_call_id_data(session_id, session_data)        
        # Connect
        data = {
            "session_id": session_id,
            "cookies": call_request.cookies,
            "host": call_request.host,
        }

        # Extract the authorization token from the headers
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header:
            token = auth_header.split(" ")[1]
        host = "https://" + data.get("host")
        redis_manager.store_base_url(sid=session_id, base_url=host)

        if not host:
            log_error(log, "BASE_URL is not set in environment variables")
            raise HTTPException(status_code=500, detail="Server configuration error")
        parsed_url = urlparse(host)
        host = parsed_url.netloc


        # Handle cookies if provided
        cookies = httpx.Cookies()
        if data["cookies"]:
            cookie_manager.store_cookies(session_id=session_id, cookies=data["cookies"], host=host)
        # elif not data["cookies"]:
        #     # Perform login for development testing
        #     cookies = await login_(session_id, username="degassistant28325", password="F8?oi3dsfiBz") 

        #     cookie_manager.store_cookies(session_id=session_id, cookies=cookies, host=host)

        elif token:
            data["token"] = token

        # Store session data in Redis
        redis_manager.add_object(f"user_session_{data['session_id']}", json.dumps(data))

        # Validate cookies if no token is provided
        if not token:
            try:
                valid = await validate_auth_cookies(data.get("session_id"))
            except HTTPException as e:
                log_error(log, f"Cookie validation failed: {e.detail}")
                raise e
            if not valid:
                log_error(log, "HTTP Status Code : 401, Error: Invalid Cookies!!!")
                raise HTTPException(status_code=401, detail="Invalid Cookies")


        preprocessor = PromptPreprocessor(
            user_id=call_request.userProfileKey, 
            conversation_id=call_request.conversationId,
            call_id=session_id,
            coach_id=call_request.coachId,
            time_zone=pytz.timezone(call_request.timeZone),
        )

        await preprocessor.initialize()
        prompt = await preprocessor.prepare_prompt()

        instructions = next((message["content"] for message in prompt if message["role"] == "system"), "")

        instructions_data = {
            "conversation_id": call_request.conversationId,
            "coach_id": call_request.coachId,
            "instructions": instructions,
            "user_profile_key": call_request.userProfileKey,
        }
        redis_manager.store_instructions(session_id, instructions_data)

        import os
        from livekit import api
        meta_data = {
        "session_id": session_id,
        "modalities": "Audio + Text",
        "voice": call_request.voice,
        "temperature": 0.9,
        "max_output_tokens": 2048,
        "turn_detection": json.dumps({
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 200,
            "prefix_padding_ms": 300,
            }),
        }
        def getToken(meta_data):
            token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET")) \
                .with_metadata(json.dumps(meta_data)) \
                .with_ttl(datetime.timedelta(hours=1)) \
                .with_identity(f"human-{call_request.userProfileKey}-{str(uuid.uuid4())}") \
                .with_grants(api.VideoGrants(
                    room_join=True,
                    room=f"room-{call_request.conversationId}-{str(uuid.uuid4())}",
                )
                )
            return token.to_jwt()
        access_token = getToken(meta_data)
        socket_url = os.getenv("LIVEKIT_URL")

        # Log success and return response
        log_info(log, "Connect Successful!")
        log_info(log, f"Call registered successfully with call_id: {session_id}")
    except httpx.HTTPStatusError as http_err:
        log_error(log, f"HTTP error occurred: {http_err}")
        log_debug(log, traceback.format_exc())
        return JSONResponse(status_code=http_err.response.status_code, content={"message": "An error occurred while processing your request."})
    except Exception as err:
        log_error(log, f"Error registering call: {err}")
        log_debug(log, traceback.format_exc())
        return JSONResponse(status_code=500, content={"message": "An error occurred while processing your request."})
    return JSONResponse(status_code=200, content={"callId": session_id, "accessToken": access_token, "socketUrl": socket_url})


@router.get("/list-rooms", tags=["Realtime"])
async def list_rooms():
    """
    Endpoint to list all active rooms.

    Returns:
        JSONResponse: A JSON response containing the list of active rooms.
    """
    try:
        lkapi = LiveKitAPI(url=os.getenv("LIVEKIT_URL"), api_key=os.getenv("LIVEKIT_API_KEY"), api_secret=os.getenv("LIVEKIT_API_SECRET"))
        # Fetch the list of rooms
        rooms = await lkapi.room.list_rooms(ListRoomsRequest())

        # Log the successful retrieval of rooms
        log_info(log, "Successfully retrieved list of rooms.")
        
        # Convert rooms to a serializable format
        rooms_list = [livekit_to_dict(room) for room in rooms.rooms]
        # Fetch participants for each room and add to the room dictionary
        for room in rooms_list:
            try:
                participants_response = await lkapi.room.list_participants(ListParticipantsRequest(room=room["name"]))
                room["participants"] = [livekit_to_dict(participant) for participant in participants_response.participants]
            except Exception as err:
                log_error(log, f"Error listing participants for room {room['name']}: {err}")
                log_debug(log, traceback.format_exc())
                room["participants"] = []
        # Return the list of rooms
        return JSONResponse(status_code=200, content={"rooms": rooms_list})
    except Exception as err:
        log_error(log, f"Error listing rooms: {err}")
        log_debug(log, traceback.format_exc())
        return JSONResponse(status_code=500, content={"message": "An error occurred while listing rooms."})
    finally:
        await lkapi.aclose()



@router.get("/list-participants/{room_name}", tags=["Realtime"])
async def list_participants(room_name: str):
    """
    Endpoint to list all participants in a given room.

    Args:
        room_name (str): The name of the room.

    Returns:
        JSONResponse: A JSON response containing the list of participants.
    """
    try:
        lkapi = LiveKitAPI(url=os.getenv("LIVEKIT_URL"), api_key=os.getenv("LIVEKIT_API_KEY"), api_secret=os.getenv("LIVEKIT_API_SECRET"))
        # Fetch the list of participants
        participants_response = await lkapi.room.list_participants(ListParticipantsRequest(room=room_name))

        # Log the successful retrieval of participants
        log_info(log, f"Successfully retrieved list of participants for room: {room_name}")
        
        # Convert participants to a serializable format
        participants_list = [livekit_to_dict(participant) for participant in participants_response.participants]

        # Return the list of participants
        return JSONResponse(status_code=200, content={"participants": participants_list})
    except Exception as err:
        log_error(log, f"Error listing participants for room {room_name}: {err}")
        log_debug(log, traceback.format_exc())
        return JSONResponse(status_code=500, content={"message": f"An error occurred while listing participants for room {room_name}."})
    finally:
        await lkapi.aclose()
