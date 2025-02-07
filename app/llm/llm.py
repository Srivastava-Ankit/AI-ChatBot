import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
import os
import re
from typing import List
import openai
import pytz
import tiktoken
import json
import os
import pandas as pd
from ddtrace import tracer

from app.db.langchain_chroma_manager import LangchainChromaManager
from app.db.redis_manager import RedisManager
from app.llm.llm_client import AZURE_ASYNC_CLIENT, AZURE_CLIENT
from app.llm.tools.llm_tools import Tools
from app.request_and_response.custom_types import (
    ResponseRequiredRequest,
    ResponseResponse
)
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.llm.prompt_preprocessor import PromptPreprocessor
from app.utils.api_utils import save_chat_message, save_message
from app.utils.llm_utils import num_tokens_from_messages, num_tokens_from_string
import traceback

# Get the logger instance
log = get_logger(__name__)


class LlmClient:
    """
    LlmClient is responsible for managing interactions with the LLM (Language Learning Model) and associated tools.
    
    Attributes:
        user_id (str): The ID of the user.
        call_id (str): The ID of the call.
        conversation_id (str): The ID of the conversation.
        coach_id (str): The ID of the coach.
        timezone (pytz.timezone): The timezone of the user.
        async_client (object): The asynchronous client for LLM.
        client (object): The synchronous client for LLM.
        redis_manager (RedisManager): The manager for Redis operations.
        chroma_manager (LangchainChromaManager): The manager for Langchain Chroma operations.
        preprocessor (PromptPreprocessor): The preprocessor for prompts.
        tools (Tools): The tools associated with the LLM.
    """

    @tracer.wrap(name="dd_trace.__init__",service="degreed-coach-builder")
    def __init__(self, user_id=None, call_id=None, conversation_id=None, coach_id=None, time_zone='Asia/Kolkata', queue=None):
        """
        Initializes the LlmClient with the provided parameters.

        Args:
            user_id (str, optional): The ID of the user. Defaults to None.
            call_id (str, optional): The ID of the call. Defaults to None.
            conversation_id (str, optional): The ID of the conversation. Defaults to None.
            coach_id (str, optional): The ID of the coach. Defaults to None.
            time_zone (str, optional): The timezone of the user. Defaults to 'Asia/Kolkata'.
            queue (object, optional): The queue for processing. Defaults to None.
        """
        self.timezone = pytz.timezone(time_zone)
        self.async_client = AZURE_ASYNC_CLIENT
        self.client = AZURE_CLIENT
        self.call_id = call_id
        self.user_id = user_id
        self.coach_id = coach_id
        self.conversation_id = conversation_id
        self.redis_manager = RedisManager()
        self.load_data()
        self.chroma_manager = LangchainChromaManager()
        self.preprocessor = PromptPreprocessor(
            user_id=self.user_id, 
            conversation_id=self.conversation_id, 
            call_id=self.call_id, 
            coach_id=self.coach_id, 
            time_zone=self.timezone
        )
        self.tools = Tools(
            coach_id=self.coach_id, 
            user_id=self.user_id, 
            conversation_id=self.conversation_id, 
            call_id=self.call_id, 
            time_zone=self.timezone, 
            queue=queue
        )

    @tracer.wrap(name="dd_trace.initialize",service="degreed-coach-builder")
    async def initialize(self):
        """
        Asynchronously initializes the preprocessor.
        """
        await self.preprocessor.initialize()

    @tracer.wrap(name="dd_trace.load_data",service="degreed-coach-builder")
    def load_data(self):
        """
        Loads data from Redis if user_id and coach_id are not provided.
        """
        if not self.user_id and not self.coach_id:
            try:
                data = self.redis_manager.retrieve_call_id_data(self.call_id)
                self.user_id = data.get("user_id")
                self.coach_id = data.get("coach_id")
                self.conversation_id = data.get("conversation_id")
                self.timezone = pytz.timezone(data.get("time_zone"))
                log_info(log, f"Data loaded from Redis for call_id {self.call_id}: {data}")
            except Exception as e:
                log_error(log, f"Failed to load data from Redis for call_id {self.call_id}: {e}")
                log_debug(log, f"Traceback: {traceback.format_exc()}")
        else:
            log_info(log, "User ID and Coach ID already loaded.")


    @tracer.wrap(name="dd_trace.tool_response",service="degreed-coach-builder")
    async def tool_response(self, request: ResponseRequiredRequest):
        """
        Asynchronously handles the tool response by preparing a prompt, sending it to the Azure client,
        and streaming the response back to the caller.

        Args:
            request (ResponseRequiredRequest): The request object containing the necessary information for generating the response.

        Yields:
            ResponseResponse: The response object containing the content and metadata.
        """
        try:
            # Prepare the prompt using the preprocessor
            prompt = await self.preprocessor.prepare_prompt(request)
            log_info(log, f"Prompt token of tool_response: {num_tokens_from_messages(prompt)}")

            # Create a stream for the response from the Azure client
            stream = await self.async_client.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=prompt,
                stream=True,
                temperature=1
            )

            # Define the pattern to filter out unwanted characters
            pattern = r'[*#]'

            # Stream the response chunks
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    response = ResponseResponse(
                        response_id=request.response_id,
                        content=re.sub(pattern, '', chunk.choices[0].delta.content),
                        content_complete=False,
                        end_call=False,
                    )
                    yield response

            # Send final response with "content_complete" set to True to signal completion
            response = ResponseResponse(
                response_id=request.response_id,
                content="",
                content_complete=True,
                end_call=False,
            )
            yield response

        except openai.BadRequestError as e:
            if e.status_code == 400 and e.body.get("code") == 'content_filter':
                log_info(log, f"Content filter triggered for call_id {self.call_id}")
                response = ResponseResponse(
                    response_id=request.response_id,
                    content="I'm sorry, your query contains inappropriate content. Please try again.",
                    content_complete=True,
                    end_call=False,
                )
                yield response


        except Exception as e:
            log_error(log, f"Failed to process tool response for request {request.response_id}: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
    
    @tracer.wrap(name="dd_trace.llm_voice_begin_message",service="degreed-coach-builder")
    async def llm_voice_begin_message(self, stream_response=False):
        """
        Asynchronously drafts the begin message for a voice interaction.

        This method prepares a prompt using the preprocessor, sends it to the Azure client,
        and streams the response back to the caller. It handles both streaming and non-streaming
        responses.

        Args:
            stream_response (bool): Flag to determine if the response should be streamed.

        Yields:
            ResponseResponse: The response object containing the content and metadata.
        """
        try:
            log_info(log, "Drafting begin message")
            prompt = await self.preprocessor.prepare_prompt()
            log_info(log, f"Prompt Tokens of Begin Message: {num_tokens_from_messages(prompt)}")
            pattern = r'[*#]'
            assistant_response = ""

            if stream_response:
                # Create a stream for the response from the Azure client
                stream_begin_sentence = await self.async_client.chat.completions.create(
                    model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                    messages=prompt,
                    temperature=1,
                    stream=True
                )

                # Stream the response chunks
                async for chunk in stream_begin_sentence:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content is not None:
                        response = ResponseResponse(
                            response_id=0,
                            content=re.sub(pattern, '', chunk.choices[0].delta.content),
                            content_complete=False,
                            end_call=False,
                        )
                        assistant_response += chunk.choices[0].delta.content
                        yield response

                # Send final response with "content_complete" set to True to signal completion
                response = ResponseResponse(
                    response_id=0,
                    content="",
                    content_complete=True,
                    end_call=False,
                )
                yield response

                # Save the assistant's response
                messages = [msg for msg in prompt if msg["role"] != "system"]
                messages.append({"role": "assistant", "content": assistant_response})
                asyncio.create_task(save_message(sid=self.call_id, original_message=messages, type="Coach", conversation_id=self.conversation_id, coach_id=self.coach_id))
            else:
                # Get the response from the Azure client without streaming
                begin_sentence = await self.async_client.chat.completions.create(
                    model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                    messages=prompt,
                    temperature=1
                )

                # Send final response with "content_complete" set to True to signal completion
                response = ResponseResponse(
                    response_id=0,
                    content=re.sub(pattern, '', begin_sentence.choices[0].message.content),
                    content_complete=True,
                    end_call=False,
                )
                yield response

        except openai.BadRequestError as e:
            if e.status_code == 400 and e.body.get("code") == 'content_filter':
                log_info(log, f"Content filter triggered for call_id {self.call_id}")
                response = ResponseResponse(
                    response_id=0,
                    content="I'm sorry, your query contains inappropriate content. Please try again.",
                    content_complete=True,
                    end_call=False,
                )
                yield response


        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Failed to draft begin message: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            response = ResponseResponse(
                response_id=0,
                content="Error drafting begin message.",
                content_complete=True,
                end_call=True,
            )
            yield response

    @tracer.wrap(name="dd_trace.llm_voice_response",service="degreed-coach-builder")
    async def llm_voice_response(self, request: ResponseRequiredRequest, async_tools=False):
        """
        Handles the voice response from the LLM (Language Model).

        Args:
            request (ResponseRequiredRequest): The request object containing the necessary information for the response.
            async_tools (bool, optional): Flag to determine if tools should be called asynchronously. Defaults to False.

        Yields:
            ResponseResponse: The response object containing the content and status of the response.
        """
        try:
            # Prepare the prompt for the LLM
            prompt = await self.preprocessor.prepare_prompt(request)
            log_info(log, f"Prompt token of draft_response: {num_tokens_from_messages(prompt)}")

            func_call = {}
            func_arguments = ""

            # Create a stream for the LLM response
            stream = await self.async_client.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=prompt,
                stream=True,
                temperature=1,
            )

            # Define the pattern to clean the response content
            pattern = r'[*#]'

            your_response = ""
            assistant_response = ""

            # Process the stream response
            async for chunk in stream:
                if len(chunk.choices) == 0:
                    continue

                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.tool_calls:
                    tool_calls = chunk.choices[0].delta.tool_calls[0]
                    if tool_calls.id:
                        if func_call:
                            # Another function received, old function complete, can break here.
                            break
                        func_call = {
                            "id": tool_calls.id,
                            "func_name": tool_calls.function.name or "",
                            "arguments": {},
                        }
                    else:
                        # Append argument
                        func_arguments += tool_calls.function.arguments or ""
                        matches = re.findall(r'"your_response":\s*"([^"]+)"', func_arguments)
                        if matches:
                            your_response = matches

                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content is not None:
                    response = ResponseResponse(
                        response_id=request.response_id,
                        content=re.sub(pattern, '', chunk.choices[0].delta.content),
                        content_complete=False,
                        end_call=False,
                    )
                    assistant_response += chunk.choices[0].delta.content
                    yield response

            # Save the assistant's response
            messages = [msg for msg in prompt if msg["role"] != "system"]
            messages.append({"role": "assistant", "content": assistant_response})
            asyncio.create_task(save_message(sid=self.call_id, original_message=messages, type="Coach", conversation_id=self.conversation_id, coach_id=self.coach_id))

            if func_call:
                function = self.tools.functions.get(func_call["func_name"])

                if function:
                    func_call["arguments"] = {k: v for k, v in json.loads(func_arguments).items() if k != "your_response"}

                    if async_tools:
                        asyncio.create_task(function(session_id=self.call_id, callback=False, **func_call["arguments"]))

                        your_response = your_response if isinstance(your_response, str) else your_response[0]
                        response = ResponseResponse(
                            response_id=request.response_id,
                            content=your_response,
                            content_complete=True,
                            end_call=False,
                        )
                        yield response

                    else:
                        response, callback = await function(
                            session_id=self.call_id,
                            callback=False,
                            **func_call["arguments"],
                        )

                        if callback:
                            async for resp in self.tool_response(request):
                                yield resp
                        else:
                            response = ResponseResponse(
                                response_id=request.response_id,
                                content=your_response,
                                content_complete=True,
                                end_call=False,
                            )
                            yield response

            else:
                response = ResponseResponse(
                    response_id=request.response_id,
                    content="",
                    content_complete=True,
                    end_call=False,
                )
                yield response

        except openai.BadRequestError as e:
            if e.status_code == 400 and e.body.get("code") == 'content_filter':
                log_info(log, f"Content filter triggered for call_id {self.call_id}")
                response = ResponseResponse(
                    response_id=request.response_id,
                    content="I'm sorry, your query contains inappropriate content. Please try again.",
                    content_complete=True,
                    end_call=False,
                )
                yield response

        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Failed to draft voice response: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            response = ResponseResponse(
                response_id=request.response_id,
                content="Error drafting voice response.",
                content_complete=True,
                end_call=True,
            )
            yield response
    
    @tracer.wrap(name="dd_trace.llm_text_begin_message",service="degreed-coach-builder")
    async def llm_text_begin_message(self):
        """
        Drafts the beginning message for the text chat.

        This method prepares the initial prompt for the text chat, sends it to the LLM for completion,
        and streams the response back. It also logs the prompt and response tokens for debugging purposes.

        Yields:
            dict: A dictionary containing the response content and status.
        """
        try:
            log_info(log, "Drafting text begin message")

            # Prepare the prompt for the text chat
            prompt = await self.preprocessor.prepare_prompt(prompt_type="text")
            log_info(log, f"Prompt token of begin message: {num_tokens_from_messages(prompt)}")

            # Send the prompt to the LLM and stream the response
            stream = await self.async_client.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=prompt,
                stream=True,
                temperature=1
            )

            response = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    response += chunk.choices[0].delta.content
                    yield self._create_yield_response(response=response, status="in-progress")

            # Save the chat message and get the message ID
            message_id = await save_chat_message(sid=self.call_id, message=response, type="Coach", conversation_id=self.conversation_id, coach_id=self.coach_id)

            # Get the current timestamp
            end_time_stamp = datetime.now(self.timezone).isoformat()

            # Yield the final response
            yield self._create_yield_response(response=response, message_id=message_id, status="done", time_stamp=end_time_stamp)

            log_info(log, f"Response token of begin message: {num_tokens_from_string(response)}")

            # Store the response in Redis for future reference
            self._store_response_in_redis(response=response, time_stamp=end_time_stamp, type="assistant")

        except openai.BadRequestError as e:
            if e.status_code == 400 and e.body.get("code") == 'content_filter':
                log_info(log, f"Content filter triggered for call_id {self.call_id}")
                yield self._create_yield_response(response="I'm sorry, your query contains inappropriate content. Please try again.", message_id=message_id, status="done", time_stamp=end_time_stamp)

        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Failed to draft text begin message: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            yield self._create_yield_response(response="Error Occurred", message_id=message_id, status="done", time_stamp=end_time_stamp)

    @tracer.wrap(name="dd_trace.llm_text_response",service="degreed-coach-builder")
    async def llm_text_response(self, user_query, session_id, correlation_id, async_tools=False):
        """
        Drafts the response message for the text chat.

        This method prepares the response prompt for the text chat, sends it to the LLM for completion,
        and streams the response back. It also handles any function calls that may be required based on the response.

        Args:
            user_query (str): The user's query message.
            session_id (str): The session ID for the chat.
            correlation_id (str): The correlation ID for tracking the request.
            async_tools (bool): Flag to determine if tools should be executed asynchronously.

        Yields:
            dict: A dictionary containing the response content and status.
        """
        try:
            log_info(log, "Drafting text response")
            start_time_stamp = datetime.now(self.timezone).isoformat()

            # Store the user query in Redis if provided
            if user_query:
                self._store_response_in_redis(response=user_query, time_stamp=start_time_stamp, type="user")

            func_call = {}
            func_arguments = ""

            # Prepare the prompt for the text chat
            prompt = await self.preprocessor.prepare_prompt(prompt_type="text")
            log_info(log, f"Prompt token of draft_text_response: {num_tokens_from_messages(prompt)}")

            # Send the prompt to the LLM and stream the response
            stream = await self.async_client.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=prompt,
                stream=True,
                temperature=1,
                # tools=self.tools.prepare_tools(),
            )

            response = ""
            async for chunk in stream:
                if len(chunk.choices) == 0:
                    continue
                if chunk.choices[0].delta and chunk.choices[0].delta.tool_calls:
                    tool_calls = chunk.choices[0].delta.tool_calls[0]
                    if tool_calls.id:
                        if func_call:
                            break
                        func_call = {
                            "id": tool_calls.id,
                            "func_name": tool_calls.function.name or "",
                            "arguments": {},
                        }
                    else:
                        func_arguments += tool_calls.function.arguments or ""
                        matches = re.findall(r'"your_response":\s*"([^"]+)"', func_arguments)
                        if matches:
                            response = matches

                if chunk.choices[0].delta and chunk.choices[0].delta.content is not None:

                    response += chunk.choices[0].delta.content
                    yield self._create_yield_response(response=response, status="in-progress")

            if response:
                response = response if isinstance(response, str) else response[0]
                self._store_response_in_redis(response=response, time_stamp=datetime.now(self.timezone).isoformat(), type="assistant")

            if func_call:
                async for resp in self._handle_function_call(func_call, func_arguments, session_id, correlation_id, async_tools, response):
                    yield resp
            else:
                end_time_stamp = datetime.now(self.timezone).isoformat()
                message_id = await save_chat_message(sid=self.call_id, message=response, type="Coach", conversation_id=self.conversation_id, coach_id=self.coach_id)
                yield self._create_yield_response(response=response, message_id=message_id, status="done", time_stamp=end_time_stamp)
                log_info(log, f"Response token of draft_text_response: {num_tokens_from_string(response)}")

        except openai.BadRequestError as e:
            if e.status_code == 400 and e.body.get("code") == 'content_filter':
                log_info(log, f"Content filter triggered for call_id {self.call_id}")
                yield self._create_yield_response(response=e.body.get("message", None) if "message" in e.body else "I'm sorry, your query hab been filtered by azure content policy. Please try again.", status="done")

        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Failed to draft text response: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            yield self._create_yield_response(response="Error Occurred", status="done")

    @tracer.wrap(name="dd_trace._create_yield_response",service="degreed-coach-builder")
    def _create_yield_response(self, response, status, message_id=None, time_stamp=None):
        """
        Creates a response dictionary for yielding.

        This method constructs a dictionary containing the response details to be yielded.
        It includes information such as coach ID, user ID, response content, status, message ID, 
        and timestamp. If the timestamp is not provided, the current time is used.

        Args:
            response (str): The response content to be included in the dictionary.
            status (str): The status of the response (e.g., "in-progress", "done").
            message_id (str, optional): The ID of the message. Defaults to None.
            time_stamp (str, optional): The timestamp of the response. Defaults to None.

        Returns:
            dict: A dictionary containing the response details.
        """
        try:
            # Use the current time if no timestamp is provided
            if not time_stamp:
                time_stamp = datetime.now(self.timezone).isoformat()
            
            # Construct the response dictionary
            response_dict = {
                "coach_id": self.coach_id,
                "answer": response,
                "user_id": self.user_id,
                "status": status,
                "message_id": message_id,
                "is_final": True if status == "done" else False,
                "time_stamp": time_stamp
            }
            
            return response_dict
        
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error creating yield response: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            raise

    @tracer.wrap(name="dd_trace._store_response_in_redis",service="degreed-coach-builder")
    def _store_response_in_redis(self, response, time_stamp, type):
        """
        Stores the response in Redis.

        This method constructs a message dictionary with the given response, timestamp, and type,
        and stores it in the Redis cache using the redis_manager.

        Args:
            response (str): The response content to be stored.
            time_stamp (str): The timestamp of the response.
            type (str): The type of the response (e.g., "user", "system", "assistant").

        Returns:
            None
        """
        try:
            log_info(log, "Storing response in Redis")
            
            # Construct the message dictionary
            message = [
                {
                    "role": type,
                    "content": response,
                    "timestamp": time_stamp
                }
            ]
            
            # Store the message in Redis
            self.redis_manager.store_chat(messages=message, conversation_id=self.conversation_id)
        
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error storing response in Redis: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            raise

    @tracer.wrap(name="dd_trace._handle_function_call",service="degreed-coach-builder")
    async def _handle_function_call(self, func_call, func_arguments, session_id, correlation_id, async_tools, response):
        """
        Handles the function call from the response.

        This method processes the function call specified in the response. It retrieves the function
        from the tools, prepares the arguments, and executes the function either asynchronously or
        synchronously based on the provided tools. The response is then stored in Redis and yielded
        back to the caller.

        Args:
            func_call (dict): The function call details including function name and arguments.
            func_arguments (str): The JSON string of function arguments.
            session_id (str): The session identifier.
            correlation_id (str): The correlation identifier.
            async_tools (bool): Flag indicating if asynchronous tools should be used.
            response (str): The response content to be yielded.

        Yields:
            dict: The response dictionary created by `_create_yield_response`.
        """
        try:
            log_info(log, f"Handling function call: {func_call['func_name']}")
            
            # Retrieve the function from the tools
            function = self.tools.functions.get(func_call["func_name"]).get("function")
            if function:
                # Prepare the function arguments, excluding 'your_response'
                func_call["arguments"] = {k: v for k, v in json.loads(func_arguments).items() if k != "your_response"}

                # Check if the function should be called asynchronously
                if async_tools or not self.tools.functions.get(func_call["func_name"]).get("is_sync"):
                    asyncio.create_task(function(session_id=session_id, correlation_id=correlation_id, **func_call["arguments"]))
                    yield self._create_yield_response(response=response, status="done")
                else:
                    # Call the function synchronously and await its response
                    tool_response, callback = await function(
                        session_id=session_id,
                        correlation_id=correlation_id,
                        **func_call["arguments"],
                    )

                    # Construct the user query dictionary
                    user_query = {
                        "role": "function", 
                        "name": func_call["func_name"],
                        "content": tool_response,
                        "timestamp": datetime.now(self.timezone).isoformat()
                    }
                    
                    # Store the response in Redis
                    self.redis_manager.store_chat(messages=[user_query], conversation_id=self.conversation_id)
                    
                    # Handle the callback if present
                    if callback:
                        async for resp in self.llm_text_response(user_query=None, session_id=session_id, correlation_id=correlation_id):
                            yield resp
                    else:
                        end_time_stamp = datetime.now(self.timezone).isoformat()
                        yield self._create_yield_response(response=response, status="done", time_stamp=end_time_stamp)
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error handling function call: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            raise
