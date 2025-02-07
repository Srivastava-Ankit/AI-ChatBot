import asyncio
from datetime import datetime
import json
import traceback
from urllib.parse import urlparse
from fastapi.responses import JSONResponse
import httpx
import pytz
from sse_starlette.sse import EventSourceResponse
from fastapi import APIRouter, HTTPException, Request
from app.config import BASE_URL
from app.db.redis_manager import RedisManager
from app.dg_component.login import login_
from ddtrace import tracer

from app.llm.llm import LlmClient
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.request_and_response.request import ConnectRequestModel
from app.utils.api_utils import process_messages, save_chat_message, save_message, send_ping_pong_messages
from app.dg_component.cookie_utils import create_cookie_dict
from app.dg_component.login_utils import validate_auth_cookies
from app.config import session
from app.utils.cookie_manager import cookie_manager

log = get_logger(__name__)

router = APIRouter()
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.connect",service="degreed-coach-builder")
@router.post("/llm-text-connect/{session_id}")
async def connect(session_id: str, input_data: ConnectRequestModel, request: Request):
    """
    Handle user input via POST request and store it with the session ID.

    Args:
        session_id (str): The unique session identifier.
        input_data (ConnectRequestModel): The incoming request object.
        request (Request): The incoming request object.

    Returns:
        JSONResponse: Acknowledgement of received input.
    """
    try:
        redis_manager = RedisManager()
        session_data = {
            "skill": input_data.skill,
            "pathway": input_data.pathwayDetails
        }
        redis_manager.store_call_id_data(session_id, session_data)

        data = {
            "session_id": session_id,
            "cookies": input_data.cookies
        }

        # Extract the authorization token from the headers
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
        # await login_(data["session_id"], username="degassistant28325", password="F8?oi3dsfiBz") 

        # Handle cookies if provided
        cookies = httpx.Cookies()
        if data["cookies"]:
            cookie_manager.store_cookies(session_id=session_id, cookies=data["cookies"], host=host)

        elif token:
            data["token"] = token

        # Store session data in Redis
        redis_manager.add_object(f"user_session_{data['session_id']}", json.dumps(data))

        # Validate cookies if no token is provided
        if not token:
            valid = await validate_auth_cookies(data.get("session_id"))
            if not valid:
                log_error(log, "HTTP Status Code : 401, Error: Invalid Cookies!!!")
                raise HTTPException(status_code=401, detail="Invalid Cookies")

        asyncio.create_task(save_chat_message(sid=session_id, message=input_data.prompt, type="User", conversation_id=input_data.conversationId, coach_id=input_data.coachId))

        # Log success and return response
        log_info(log, "Connect Successful!")

        redis_manager.store_session_data(session_id, input_data.dict())
        return JSONResponse(content={"status": "received"}, status_code=200)
    except HTTPException as http_exc:
        log_error(log, f"HTTP Exception: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        log_error(log, f"Unexpected error in connect: {e}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal Server Error")

@tracer.wrap(name="dd_trace.text_sse_handler",service="degreed-coach-builder")
@router.get("/llm-text-sse/{sessionId}")
async def text_sse_handler(request: Request, sessionId: str):
    """
    Handle SSE connections for text-based LLM interactions.

    Args:
        request (Request): The incoming request object.
        session_id (str): The unique session identifier.

    Returns:
        EventSourceResponse: An SSE response object for streaming events.
    """
    try:
        log_info(log, f"Received SSE request for call_id: {sessionId}")

        # Access the 'Cookie' header from the request
        cookie_header = request.headers.get('X-Cookie')
        host = "https://" + request.headers.get('X-Host')
        
        redis_manager.store_base_url(sid=sessionId, base_url=host)
        parsed_url = urlparse(host)
        host = parsed_url.netloc
        log_info(log, f"Received SSE request for call_id: {sessionId}, Host: {host}")
        token = None
        auth_header = request.headers.get('Authorization')
        if auth_header:
            token = auth_header.split(" ")[1]
        if cookie_header:
            cookie_manager.store_cookies(session_id=sessionId, cookies=cookie_header, host=host)


            # Store cookies in the session using session_id
            session_data = redis_manager.retrieve_session_data(sessionId)
            # Retrieve stored session data
            if not session_data:
                log_error(log, "HTTP Status Code: 401, Error: Unauthorized!")
                raise HTTPException(status_code=401, detail="Unauthorized")
        elif token:
            session_data = json.loads(redis_manager.get_object(f"user_session_{sessionId}"))
            if not session_data or not session_data.get('token'):
                log_error(log, "HTTP Status Code: 401, Error: Unauthorized!")
                raise HTTPException(status_code=401, detail="Unauthorized")
        else:
            log_error(log, "HTTP Status Code: 401, Error: Unauthorized! No cookie header found in the request")
            raise HTTPException(status_code=401, detail="Unauthorized, no cookie header found in the request")

        async def event_generator():
            llm_client = None
            queue = asyncio.Queue()

            # Event to signal when message processing is complete
            stop_event = asyncio.Event()

            # Start a background task to send ping pong messages every 5 seconds
            asyncio.create_task(send_ping_pong_messages(sessionId, queue, stop_event))

            try:
                llm_client = LlmClient(
                    user_id=session_data["userProfileKey"],
                    call_id=sessionId,
                    conversation_id=session_data["conversationId"],
                    coach_id=session_data["coachId"],
                    time_zone=session_data["timeZone"],
                    queue=queue
                )
                await llm_client.initialize()

                if session_data.get("event") == "connect":
                    asyncio.create_task(_enqueue_begin_messages(llm_client.llm_text_begin_message(), queue, sessionId, session_data))

                elif session_data.get("event") == "chat":
                    asyncio.create_task(_enqueue_chat_messages(llm_client, session_data, request, queue, sessionId))

            except Exception as e:
                error_message = f"Error in LLM SSE: {e} for {sessionId}"
                log_error(log, error_message)
                traceback_str = traceback.format_exc()
                log_debug(log, traceback_str)
                error_response = {
                    "coach_id": session_data["coachId"],
                    "answer": "Error Occurred, Please try again later.",
                    "user_id": session_data["userProfileKey"],
                    "status": "done",
                    "is_final": True,
                    "time_stamp": datetime.now(pytz.timezone(session_data.get("time_zone"))).isoformat(),
                    "session_id": sessionId,
                    "correlation_id": session_data["correlation_id"]
                }
                await queue.put(json.dumps(error_response))

            finally:
                log_info(log, f"SSE connection closed for call_id: {sessionId}")

            # Start processing messages concurrently
            async for message in process_messages(queue):
                try:
                    message = json.loads(message)
                except Exception as e:
                    log_error(log, f"Error parsing message: {e}")
                    log_debug(log, traceback.format_exc())
                is_continue = True
                if "ping-pong" in message:
                    yield json.dumps(message)
                else:
                    if message['is_final']:
                        is_continue = False
                    yield json.dumps(message)
                if not is_continue:
                    stop_event.set()
                    break

        response = EventSourceResponse(event_generator())
        response.headers['X-Accel-Buffering'] = 'no'
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        return response
    except HTTPException as http_exc:
        log_error(log, f"HTTP Exception: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        log_error(log, f"Unexpected error in text_sse_handler: {e}")
        log_debug(log, traceback.format_exc())
        raise HTTPException(status_code=500, detail="Internal Server Error")

@tracer.wrap(name="dd_trace._enqueue_begin_messages",service="degreed-coach-builder")
async def _enqueue_begin_messages(message_generator, queue, session_id, input_dict):
    """
    Enqueue begin messages to the queue.

    Args:
        message_generator (AsyncGenerator): The message generator.
        queue (asyncio.Queue): The queue to put messages into.
        session_id (str): The unique session identifier.
        input_dict (dict): The input dictionary containing session data.
    """
    async for message in message_generator:
        if isinstance(message, dict):
            message["session_id"] = session_id
            message["correlation_id"] = input_dict["correlationId"]
            await queue.put(json.dumps(message))

@tracer.wrap(name="dd_trace._enqueue_chat_messages",service="degreed-coach-builder")
async def _enqueue_chat_messages(llm_client, input_dict, request, queue, session_id):
    """
    Enqueue chat messages to the queue.

    Args:
        llm_client (LlmClient): The LLM client instance.
        input_dict (dict): The input dictionary containing session data.
        request (Request): The incoming request object.
        queue (asyncio.Queue): The queue to put messages into.
        session_id (str): The unique session identifier.
    """
    while True:
        if await request.is_disconnected():
            break

        # Check for user query from the session data
        user_query = input_dict.get("prompt")
        if user_query:
            async for message in llm_client.llm_text_response(
                user_query=user_query,
                session_id=session_id,
                correlation_id=input_dict["correlationId"]
            ):
                if isinstance(message, dict):
                    message["session_id"] = session_id
                    message["correlation_id"] = input_dict["correlationId"]
                    await queue.put(json.dumps(message))

        await asyncio.sleep(1)  # Prevent tight loop