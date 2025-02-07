

import asyncio
from datetime import datetime
import json
import os
import re
from typing import List
from fastapi import HTTPException
import httpx
from pydantic import BaseModel

import pytz
from app.config import BASE_URL
from app.db.redis_manager import RedisManager
from app.dg_component.client_session import extract_token_and_add_crsf
from app.llm.llm_client import AZURE_ASYNC_CLIENT
from app.llm.prompt import BIO_PARSER_TEMPLATE, BIO_PARSER_VALIDATE_JSON, BIO_PARSER_VALIDATE_TEMPLATE, CONVERSATION_ONE_LINER_TEMPLATE, CONVERSATION_ONE_LINER_TEMPLATE_JSON, RESUME_JSON, RESUME_PARSER_TEMPLATE, RESUME_PARSER_VALIDATE_JSON, RESUME_PARSER_VALIDATE_TEMPLATE
from ddtrace import tracer

from langchain_community.document_loaders import PyMuPDFLoader

from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.request_and_response.custom_types import Utterance
from app.utils.default import convert_keys_to_camel_case, create_headers
import traceback

log = get_logger(__name__)
redis_manager = RedisManager()

@tracer.wrap(name="dd_trace.is_mobile",service="degreed-coach-builder")
async def is_mobile(sid):
    isMobile = False
    session_data = json.loads(redis_manager.get_object(f"user_session_{sid}"))
    if session_data.get('token'):
        isMobile = True
    return isMobile


@tracer.wrap(name="dd_trace.resume_parse",service="degreed-coach-builder")
async def resume_parse(resume_file_path):

    loader = PyMuPDFLoader(file_path=resume_file_path)
    documents = loader.load()
    resume = [doc.page_content for doc in documents]
    
    message = [
    {
        "role": "system",
        "content": RESUME_PARSER_TEMPLATE.format(resume_json=RESUME_JSON, resume=resume)
    },
    {
        "role": "user",
        "content": "Begin to parse the resume"
    }
    ]
    for attempt in range(3 + 1):  # +1 because range is exclusive
        try:
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=message
            )
            content = response.choices[0].message.content
            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content =  json.loads(matches[0])
            break  # Exit the loop if the attempt is successful
        except Exception as e:
            print(f"Error in resume_parse: {e}")
            if attempt == 3:
                raise  # Re-raise the last exception if all retries fail
    
    return parsed_content

@tracer.wrap(name="dd_trace.validate_resume_parse",service="degreed-coach-builder")
async def validate_resume_parse(resume_file_path):
    loader = PyMuPDFLoader(file_path=resume_file_path)
    documents = loader.load()
    resume = [doc.page_content for doc in documents]
    
    message = [
    {
        "role": "system",
        "content": RESUME_PARSER_VALIDATE_TEMPLATE.format(resume_validate_json=RESUME_PARSER_VALIDATE_JSON, resume=resume)
    }
    ]
    for attempt in range(3 + 1):  # +1 because range is exclusive
        try:
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=message
            )
            content = response.choices[0].message.content
            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content =  json.loads(matches[0])
            break  # Exit the loop if the attempt is successful
        except Exception as e:
            print(f"Error in resume_parse: {e}")
            if attempt == 3:
                raise  # Re-raise the last exception if all retries fail
    
    return parsed_content

@tracer.wrap(name="dd_trace.bio_parser",service="degreed-coach-builder")
async def bio_parser(bio):
    message = [
    {
        "role": "system",
        "content": BIO_PARSER_TEMPLATE.format(resume_json=RESUME_JSON, bio=bio.description, domain=bio.domain, name=bio.name, role=bio.role)
    },
    {
        "role": "user",
        "content": "Begin to parse the profile data"
    }
    ]
    for attempt in range(3 + 1):  # +1 because range is exclusive
        try:
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=message
            )
            content = response.choices[0].message.content
            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content =  json.loads(matches[0])
            break  # Exit the loop if the attempt is successful
        except Exception as e:
            print(f"Error in bio_parser: {e}")
            if attempt == 3:
                raise  # Re-raise the last exception if all retries fail
    
    return parsed_content

@tracer.wrap(name="dd_trace.validate_bio_parser",service="degreed-coach-builder")
async def validate_bio_parser(bio):
    message = [
    {
        "role": "system",
        "content": BIO_PARSER_VALIDATE_TEMPLATE.format(bio=bio.description, domain=bio.domain, bio_validate_json=BIO_PARSER_VALIDATE_JSON)
    }
    ]
    for attempt in range(3 + 1):  # +1 because range is exclusive
        try:
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=message
            )
            content = response.choices[0].message.content
            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content =  json.loads(matches[0])
            break  # Exit the loop if the attempt is successful
        except Exception as e:
            print(f"Error in bio_parser: {e}")
            if attempt == 3:
                raise  # Re-raise the last exception if all retries fail
    
    return parsed_content

@tracer.wrap(name="dd_trace.coach_prompt_suggestion",service="degreed-coach-builder")
async def coach_prompt_suggestion(system_message):
    message = [
    {
        "role": "system",
        "content": system_message
    }
    ]
    for attempt in range(3 + 1):  # +1 because range is exclusive
        try:
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=message
            )
            content = response.choices[0].message.content
            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content =  json.loads(matches[0])
            break  # Exit the loop if the attempt is successful
        except Exception as e:
            print(f"Error in coach_prompt_suggestion: {e}")
            if attempt == 3:
                raise  # Re-raise the last exception if all retries fail
    
    return parsed_content

@tracer.wrap(name="dd_trace.send_ping_pong_messages",service="degreed-coach-builder")
async def send_ping_pong_messages(sid: str, queue: asyncio.Queue, stop_event: asyncio.Event):
    """
    Send ping-pong messages to the queue at regular intervals until the stop event is set.

    Args:
        sid (str): The session ID.
        queue (asyncio.Queue): The queue to put messages into.
        stop_event (asyncio.Event): The event to signal stopping the message sending.
    """
    try:
        while not stop_event.is_set():
            await asyncio.sleep(5)
            if not stop_event.is_set():
                ping_pong_message = "ping-pong"
                await queue.put(ping_pong_message)
    except Exception as e:
        log_error(log, f"Error in send_ping_pong_messages: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.process_messages",service="degreed-coach-builder")
async def process_messages(queue: asyncio.Queue):
    """
    Process messages from the queue.

    Args:
        queue (asyncio.Queue): The queue to get messages from.

    Yields:
        message: The message from the queue.
    """
    try:
        while True:
            message = await queue.get()
            if message is None:
                break
            yield message
            queue.task_done()
    except Exception as e:
        log_error(log, f"Error in process_messages: {e}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.remove_common_messages",service="degreed-coach-builder")
def remove_common_messages(original_message, stored_message):
    """
    Remove common messages between original_message and stored_message.

    Args:
        original_message (list): The list of original messages.
        stored_message (list): The list of stored messages.

    Returns:
        tuple: A tuple containing two lists - unique messages and messages to delete.
    """
    # Find unique messages in original_message
    unique_messages = [m for m in original_message if m['content'] not in {msg['content'] for msg in stored_message}]
    
    # Find messages to delete in stored_message
    unique_messages_to_delete = [m for m in stored_message if m['content'] not in {msg['content'] for msg in original_message}]

    return unique_messages, unique_messages_to_delete

@tracer.wrap(name="dd_trace.get_messages",service="degreed-coach-builder")
async def get_messages(sid: str, conversation_id: int, coach_id: int, is_mobile:bool):
    """
    Retrieve messages for a given conversation ID.

    Args:
        sid (str): The session ID.
        conversation_id (int): The ID of the conversation.

    Returns:
        dict: The response containing the messages.

    Raises:
        HTTPException: If the client session is invalid or the request fails.
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            log_info(log, f"Retrieving messages for conversation_id: {conversation_id}, Attempt: {attempt}")

            headers = create_headers(sid)
            client_session, token = await extract_token_and_add_crsf(headers)

            if not client_session:
                raise HTTPException(status_code=403, detail="Invalid Authorization")
            
            base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))

            # Get Messages by conversationId
            if is_mobile:
                get_message_response = await client_session.get(
                    url=f"{base_url}/api/mobile/coaches/{coach_id}/conversations/{conversation_id}"
                )
            else:
                get_message_response = await client_session.get(
                    url=f"{base_url}/api/Coach/Conversations/{conversation_id}"
                )
            get_message_response.raise_for_status()

            return convert_keys_to_camel_case(get_message_response.json()).get("messages", [])
        except HTTPException as e:
            log_error(log, f"HTTPException occurred: {e.detail}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                raise
        except Exception as e:
            log_error(log, f"An error occurred while retrieving messages: {str(e)}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                raise
        await asyncio.sleep(1)  # Adding a small delay before retrying

@tracer.wrap(name="dd_trace.delete_message",service="degreed-coach-builder")
async def delete_message(sid: str, message_id: int, conversation_id: int, coach_id: int, is_mobile:bool):
    """
    Delete a message with the specified ID.

    Args:
        sid (str): The session ID.
        message_id (int): The ID of the message.

    Raises:
        HTTPException: If the client session is invalid or the request fails.
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            log_info(log, f"Deleting message with ID: {message_id}, Attempt: {attempt}")

            headers = create_headers(sid)
            client_session, token = await extract_token_and_add_crsf(headers)

            if not client_session:
                raise HTTPException(status_code=403, detail="Invalid Authorization")

            base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))

            # Delete Message by messageId
            if is_mobile:
                delete_message_response = await client_session.delete(
                    url=f"{base_url}/api/mobile/coaches/{coach_id}/conversations/{conversation_id}/messages/{message_id}"
                )
            else:
                delete_message_response = await client_session.delete(
                    url=f"{base_url}/api/Coach/Messages/Delete/{message_id}"
                )
            delete_message_response.raise_for_status()
            break  # Exit the loop if the attempt is successful
        except HTTPException as e:
            log_error(log, f"HTTPException occurred: {e.detail}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                raise
        except Exception as e:
            log_error(log, f"An error occurred while deleting message: {str(e)}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                raise

@tracer.wrap(name="dd_trace.save_message",service="degreed-coach-builder")
async def save_message(sid: str, original_message: list, type: str, conversation_id: int, coach_id) -> int:
    """
    Send a message to the specified conversation.

    Args:
        sid (str): The session ID.
        original_message (list): The list of original messages.
        type (str): The type of the message.
        conversation_id (int): The ID of the conversation.

    Returns:
        int: The ID of the created message.

    Raises:
        HTTPException: If the client session is invalid or the request fails.
    """
    try:
        log_info(log, f"Sending message: {original_message} of type: {type} to conversation_id: {conversation_id}")

        ismobile = await is_mobile(sid)

        headers = create_headers(sid)
        client_session, token = await extract_token_and_add_crsf(headers)

        if not client_session:
            raise HTTPException(status_code=403, detail="Invalid Authorization")

        # Get Messages by conversationId
        message_list = await get_messages(sid=sid, conversation_id=conversation_id, is_mobile=ismobile, coach_id=coach_id)
        stored_message = []
        for m in message_list:
            if m["sessionId"] == sid:
                content = {
                    "role": "assistant" if m["senderType"] == "Coach" else "user",
                    "content": m["messageText"]
                }
                stored_message.append(content)

        unique_messages, messages_to_delete = remove_common_messages(stored_message=stored_message, original_message=original_message)

        delete_message_tasks = [
            delete_message(sid=sid, message_id=message['messageId'], conversation_id=conversation_id, coach_id=coach_id, is_mobile=ismobile)
            for msg_to_delete in messages_to_delete
            for message in message_list
            if (message['messageText'] == msg_to_delete['content'] and
                ((message['senderType'] == 'Coach' and msg_to_delete['role'] == 'assistant') or
                 (message['senderType'] != 'Coach' and msg_to_delete['role'] == 'user')))
        ]
        await asyncio.gather(*delete_message_tasks)
        
        add_message_list = []
        for add_message in unique_messages:
            add_message_list.append({
                "conversationId": conversation_id,
                "senderType": "Coach" if add_message['role']=="assistant" else "User",
                "messageText": add_message['content'],
                "sessionId": sid
            })

        payload = {"messages": add_message_list}

        max_retries = 3
        # Get the last messageText where senderType is Coach
        last_coach_message = False
        for message in reversed(add_message_list):
            if message["senderType"] == "Coach":
                last_coach_message = message["messageText"]
                break


        asyncio.create_task(prepare_one_liner(sid, conversation_id, last_coach_message, coach_id=coach_id))


        for attempt in range(1, max_retries + 1):
            try:
                log_info(log, f"Attempt {attempt} to save message to conversation_id: {conversation_id}")

                base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))
                if ismobile:
                    response = await client_session.post(
                        url=f"{base_url}/api/mobile/coaches/messages/bulk-create",
                        data=json.dumps(payload)
                    )
                else:
                    response = await client_session.post(
                        url=f"{base_url}/api/Coach/Messages/BulkCreate",
                        data=json.dumps(payload)
                    )
                response.raise_for_status()
                break  # Exit the loop if the attempt is successful
            except httpx.HTTPStatusError as e:
                log_error(log, f"HTTPStatusError occurred on attempt {attempt}: {str(e)}")
                log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
                if attempt == max_retries:
                    raise HTTPException(status_code=e.response.status_code, detail=str(e))
            except Exception as e:
                log_error(log, f"An error occurred on attempt {attempt}: {str(e)}")
                log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
                if attempt == max_retries:
                    raise

        save_message_response = convert_keys_to_camel_case(response.json())
        if not save_message_response:
            return None

        for saved_message in save_message_response:
            if saved_message["senderType"] == "Coach":
                return saved_message["messageId"]
        return save_message_response[0]["messageId"]
    except Exception as e:
        log_error(log, f"An error occurred in save_message: {str(e)}")
        log_debug(log, traceback.format_exc())
        raise

@tracer.wrap(name="dd_trace.save_chat_message",service="degreed-coach-builder")
async def save_chat_message(sid: str, message: str, type: str, conversation_id: int, coach_id: int) -> int:
    """
    Send a message to the specified conversation.

    Args:
        sid (str): The session ID.
        message (str): The message content.
        type (str): The type of the message.
        conversation_id (int): The ID of the conversation.

    Returns:
        int: The ID of the created message.

    Raises:
        HTTPException: If the client session is invalid or the request fails.
    """
    log_info(log, f"Sending message: {message} of type: {type} to conversation_id: {conversation_id}")
    ismobile = await is_mobile(sid)   

    headers = create_headers(sid)
    client_session, token = await extract_token_and_add_crsf(headers)

    if not client_session:
        raise HTTPException(status_code=403, detail="Invalid Authorization")

    payload = {
        "conversationId": conversation_id,
        "senderType": type,
        "messageText": message,
        "sessionId": sid
    }

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            asyncio.create_task(prepare_one_liner(sid, conversation_id, message if type == "Coach" else False, coach_id=coach_id))
            
            log_info(log, f"Attempt {attempt} to send message to conversation_id: {conversation_id}")

            base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))

            if ismobile:
                response = await client_session.post(
                    url=f"{base_url}/api/mobile/coaches/{coach_id}/conversations/{conversation_id}/messages",
                    data=json.dumps(payload)
                )
            else:
                response = await client_session.post(
                    url=f"{base_url}/api/Coach/Messages/Create",
                    data=json.dumps(payload)
                )
            response.raise_for_status()
            break  # Exit the loop if the attempt is successful
        except httpx.HTTPStatusError as e:
            log_error(log, f"HTTPStatusError occurred on attempt {attempt}: {str(e)}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                log_error(log, f"Max retries reached. Raising HTTPException.")
                raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except Exception as e:
            log_error(log, f"An error occurred on attempt {attempt}: {str(e)}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                log_error(log, f"Max retries reached. Raising Exception.")
                raise

    return convert_keys_to_camel_case(response.json())["messageId"]

@tracer.wrap(name="dd_trace.prepare_one_liner",service="degreed-coach-builder")
async def prepare_one_liner(sid, conversation_id, coach_response, coach_id):

    ismobile = await is_mobile(sid)

    messages = await get_messages(sid=sid, conversation_id=conversation_id, coach_id=coach_id, is_mobile=ismobile)

    messages = [{"role":msg["senderType"], "content":msg["messageText"]} for msg in messages ]
    if coach_response and messages and messages[-1]["content"] != coach_response:
        messages.append({"role":"Coach", "content":coach_response})
    if not len(messages)>5 and messages:
        sys_message = CONVERSATION_ONE_LINER_TEMPLATE.format(
                        coach="",
                        conversation_summary_json=CONVERSATION_ONE_LINER_TEMPLATE_JSON
                    )
        
        llm_message = [
            {
                "role": "system",
                "content": sys_message
            },
            {
                "role": "user",
                "content": f"Here is the conversation that happened between the user and the AI Coach: \n{messages}"
            }
        ]
        
        response = await AZURE_ASYNC_CLIENT.chat.completions.create(
            model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
            messages=llm_message
        )
        
        content = response.choices[0].message.content
        matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
        parsed_content = json.loads(matches[0])
        print(f"One Liner {parsed_content['conversation_one_liner']}")
        
        await save_one_liner(sid, conversation_id, parsed_content['conversation_one_liner'], is_mobile=ismobile)
        return parsed_content["conversation_one_liner"]

@tracer.wrap(name="dd_trace.get_messages",service="degreed-coach-builder")
async def save_one_liner(sid: str, conversation_id: int, oneliner: str, is_mobile:bool):
    """
    Retrieve messages for a given conversation ID.

    Args:
        sid (str): The session ID.
        conversation_id (int): The ID of the conversation.

    Returns:
        dict: The response containing the messages.

    Raises:
        HTTPException: If the client session is invalid or the request fails.
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            log_info(log, f"Retrieving messages for conversation_id: {conversation_id}, Attempt: {attempt}")

            headers = create_headers(sid)
            client_session, token = await extract_token_and_add_crsf(headers)

            if not client_session:
                raise HTTPException(status_code=403, detail="Invalid Authorization")
            
            base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))
            data = {
  "conversationId": conversation_id,
  "conversationSummary": oneliner
}
            if is_mobile:
                save_one_liner_response = await client_session.put(
                    url=f"{base_url}/api/coach/conversationsummary/update",
                    data=json.dumps(data)
                ) # :: TODO need to know the endpoint
            else:
                save_one_liner_response = await client_session.put(
                    url=f"{base_url}/api/coach/conversationsummary/update",
                    data=json.dumps(data)
                )
            save_one_liner_response.raise_for_status()
            break

        except HTTPException as e:
            log_error(log, f"HTTPException occurred: {e.detail}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                raise
        except Exception as e:
            log_error(log, f"An error occurred while retrieving messages: {str(e)}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            if attempt == max_retries:
                raise

    
@tracer.wrap(name="dd_trace.extract_user_message_from_transcript",service="degreed-coach-builder")
def extract_user_message_from_transcript(transcript: List[Utterance]) -> str:
    """
    Extracts user messages from a transcript and converts them to a single string.

    Args:
        transcript (List[Utterance]): The list of utterances in the transcript.

    Returns:
        str: A concatenated string of user messages.
    """
    log_info(log, "Converting transcript to OpenAI messages")
    messages = []
    
    # Convert transcript to OpenAI message format
    for utterance in transcript:
        if utterance.role == "agent":
            messages.append({"role": "assistant", "content": utterance.content})
        else:
            messages.append({"role": "user", "content": utterance.content})

    user_messages = ""
    
    # Extract user messages in reverse order until an assistant message is encountered
    for utterance in reversed(messages):
        if utterance["role"] == "assistant":
            break
        if utterance["role"] == "user":
            user_messages += utterance["content"]

    return user_messages

@tracer.wrap(name="dd_trace.get_previous_conversation",service="degreed-coach-builder")
async def get_previous_conversation(sid: str, conversation_id: int, coach_id: str) -> dict:
    """
    Retrieve the previous conversation details for a given session and coach.

    Args:
        sid (str): The session ID.
        conversation_id (int): The ID of the conversation.
        coach_id (str): The ID of the coach.

    Returns:
        dict: The response from the API containing the previous conversation details.

    Raises:
        HTTPException: If the client session is invalid or the request fails.
    """
    headers = create_headers(sid)
    client_session, token = await extract_token_and_add_crsf(headers)

    if not client_session:
        raise HTTPException(status_code=403, detail="Invalid Authorization")

    params = {
        "conversationId": conversation_id,
        "coachId": coach_id
    }
    try:
        ismobile = await is_mobile(sid)
        base_url = redis_manager.retrieve_base_url(sid=headers.get('sid'))
        if ismobile:
            response = await client_session.get(f"{base_url}/api/mobile/coaches/inferences-plans-tasks", params=params)
        else:
            response = await client_session.get(f"{base_url}/api/Coach/InferencesPlansTasks", params=params)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        log_error(log, f"HTTPStatusError occurred: {str(e)}")
        log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        log_error(log, f"An error occurred: {str(e)}")
        log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
        raise

    return convert_keys_to_camel_case(response.json())
