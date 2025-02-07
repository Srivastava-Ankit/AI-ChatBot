import asyncio
import json
import os
import re
from typing import List
import uuid
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import JSONResponse
import httpx
from app.dg_component.client_session import extract_token_and_add_crsf
from app.dg_component.coach.coach import get_coach

from app.dg_component.login import login_
from app.dg_component.profile import get_user, get_user_profile_key
from app.llm.llm_client import AZURE_ASYNC_CLIENT
from app.llm.prompt import PREPARE_SYSTEM_PROMPT, PREPARE_SYSTEM_PROMPT_JSON, SESSION_EVALUATOR_JSON, SESSION_EVALUATOR_TEMPLATE
from app.request_and_response.request import ConnectRequestModel, TestData
from app.utils.default import create_headers
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn

log = get_logger(__name__)

# test_data = [
#     {
#         "username": "degassistant28325",
#         "password": "F8?oi3dsfiBz",
#         "number_of_sessions": 5,
#         "goal": "Learn Effective team building and handling conflict resolution",
#         "session_scenario": [
#             {
#                 "session_number": 1,
#                 "session_goal": "introduction of yourself and your goals",
#                 "scenario": "You work as Data Scientist in a company. You been expecting a promotion for a while now. You have been facing some challenges in your current role. You need to learn about effective team building and handling conflict resolution to achieve your goals."
#             },
#             {
#                 "session_number": 2,
#                 "session_goal": "Learn about effective team building",
#                 "scenario": "You have been assigned to a new project. You need to learn about effective team building to work effectively with your team members."
#             },
#             {
#                 "session_number": 3,
#                 "session_goal": "Learn about handling conflict resolution",
#                 "scenario": "You have been facing some challenges in your current role. You need to learn about handling conflict resolution to resolve the conflicts."
#             },
#             {
#                 "session_number": 4,
#                 "session_goal": "Practice effective team building",
#                 "scenario": "You have been assigned to a new project. You need to practice effective team building to work effectively with your team members."
#             },
#             {
#                 "session_number": 5,
#                 "session_goal": "Practice handling conflict resolution",
#                 "scenario": "You have been facing some challenges in your current role. You need to practice handling conflict resolution to resolve the conflicts."
#             }
#         ],
#         "coach_id": 10
#     }
# ]

router = APIRouter()

@router.post("/start_test", tags=["Test"])
async def start_test(data: List[TestData], custom_header: str = Header(default=None)):
    try:
        custom_header_value = "testaidgcoachgpt"
        # Check custom header value
        if custom_header == custom_header_value:

            simulate_test = SimulateTest(test_data=data)
            await simulate_test.start_test()
            return JSONResponse(content={"test_data": test_data}, status_code=200)
    except Exception as e:
        log_error(log, f"Error in start_test: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

class SimulateTest:
    def __init__(self, test_data):
        self.test_data = test_data

    async def start_test(self):
        """
        Start the test.
        """
        try:
            tasks = [self.simulate_sessions(data.dict()) for data in self.test_data]
            results = await asyncio.gather(*tasks)
            for data, result in zip(self.test_data, results):
                data["evaluation_report"] = result

            return self.test_data
        except Exception as e:
            log_error(log, f"Error in start_test method: {e}")
            raise

    async def simulate_sessions(self, test_data):
        """
        Simulate a series of sessions between a user and a coach.
        """
        try:
            result = []
            for data in test_data["session_scenario"]:
                response = await self.simulate_conversation(username=test_data["username"], password=test_data["password"], session_scenario=data, goal=test_data["goal"], coach_id=test_data["coach_id"])
                result.append(response)

            evaluate_result = await self.evaluate_result(result, username=test_data["username"], password=test_data["password"], goal=test_data["goal"])
            return evaluate_result
        except Exception as e:
            log_error(log, f"Error in simulate_sessions method: {e}")
            raise

    async def simulate_conversation(self, username, password, session_scenario, goal, coach_id):
        """
        Simulate a conversation between a user and a coach.
        """
        try:
            session_id = str(uuid.uuid4())
            cookies = await login_(session_id, username=username, password=password)
            user_profile_key = await get_user_profile_key(sid=session_id)
            host = f"https://{os.getenv('BASE_URL', 'pr43546.degreed.dev')}"
            sys_prompt = await self.prepare_sys_prompt(sid=session_id, coach_id=coach_id, session_scenario=session_scenario)
            conversation_id = await self.create_conversation(session_id=session_id, coach_id=coach_id)
            messages = [
                {
                    "role": "system",
                    "content": sys_prompt
                }
            ]

            data = ConnectRequestModel(sessionId=session_id,
                                       userProfileKey=user_profile_key,
                                       coachId=coach_id,
                                       event="connect",
                                       prompt="",
                                       cookies=cookies,
                                       host=host,
                                       timeZone="Asia/Kolkata",
                                       skill={},
                                       pathwayDetails={},
                                       conversationId=conversation_id,
                                       correlationId=str(uuid.uuid4()))

            coach_response = await self.get_coach_message(data)
            messages.append({
                "role": "user",
                "content": coach_response
            })

            for _ in range(30):
                user_message = await self.get_user_message(messages)
                messages.append({
                    "role": "assistant",
                    "content": user_message
                })

                data = ConnectRequestModel(
                    sessionId=session_id,
                    userProfileKey=user_profile_key,
                    coachId=coach_id,
                    event="chat",
                    prompt=user_message,
                    cookies=cookies,
                    host=host,
                    timeZone="Asia/Kolkata",
                    skill={},
                    pathwayDetails={},
                    conversationId=conversation_id,
                    correlationId=str(uuid.uuid4())
                )

                coach_response = await self.get_coach_message(data)
                messages.append({
                    "role": "user",
                    "content": coach_response
                })
            return {"session_id": session_id, "conversation_id": conversation_id, "session_goal": session_scenario["session_goal"]}
        except Exception as e:
            log_error(log, f"Error in simulate_conversation method: {e}")
            raise

    async def get_user_message(self, messages: list):
        try:
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=messages,
                temperature=0
            )
            return response.choices[0].message.content
        except Exception as e:
            log_error(log, f"Error in get_user_message method: {e}")
            raise

    async def get_coach_message(self, data: ConnectRequestModel):
        """
        This method should hit the llm-text-connect endpoint first with the provided data,
        then hit the llm-text-sse endpoint to get the streaming response.
        
        Args:
            data (ConnectRequestModel): The data to send to the endpoints.
        
        Returns:
            str: The coach's message from the streaming response.
        """
        try:
            headers = create_headers(data.sessionId)
            client_session, token = await extract_token_and_add_crsf(headers)
            if not client_session:
                raise HTTPException(status_code=403, detail="Invalid Authorization")

            connect_response = await client_session.post("https://pr43546.degreed.dev/api/Coach/Connect", json=data.dict())

            if connect_response.status_code not in (200, 302):
                raise HTTPException(status_code=connect_response.status_code, detail=connect_response.text)

            connect_response.raise_for_status()

            async with client_session.stream("GET", f"https://pr43546.degreed.dev/api/Coach/Chat/{data.sessionId}") as sse_response:
                sse_response.raise_for_status()
                async for line in sse_response.aiter_lines():
                    if line:
                        message = json.loads(line)
                        if message.get("is_final"):
                            return message.get("answer")
        except Exception as e:
            log_error(log, f"Error in get_coach_message method: {e}")
            raise

    def format_user_data(self, user_data: dict) -> str:
        """
        Formats the user data into a readable string.

        Args:
            user_data (dict): The user data to format.

        Returns:
            str: The formatted user data.
        """
        try:
            formatted_string = ""

            def add_to_string(key, label, is_dict=False, is_list=False, sub_keys=None):
                nonlocal formatted_string  # Declare formatted_string as nonlocal
                value = user_data.get(key)
                if value is None:
                    return
                if is_dict:
                    if isinstance(value, dict):
                        formatted_string += f"{label}:\n"
                        for sub_key in sub_keys:
                            sub_value = value.get(sub_key, 'N/A')
                            if sub_value is not None:
                                formatted_string += f"  - {sub_key.capitalize()}: {sub_value}\n"
                elif is_list:
                    if isinstance(value, list):
                        filtered_values = [v for v in value if v is not None]
                        formatted_string += f"{label}: " + ", ".join(filtered_values) + "\n"
                else:
                    formatted_string += f"{label}: {value}\n"

            add_to_string('name', 'Name')
            add_to_string('age', 'Age')
            add_to_string('city', 'City')
            add_to_string('role', 'Role')

            skills = user_data.get('skills', {})
            if skills and isinstance(skills, dict):
                formatted_string += "Skills:\n"
                for skill, level in skills.items():
                    if level is not None:
                        formatted_string += f"  - {skill}: Level {level}\n"

            add_to_string('experience', 'Experience')

            add_to_string('education', 'Education', is_dict=True, sub_keys=['university', 'degree', 'college'])

            add_to_string('languages', 'Languages', is_list=True)

            projects = user_data.get('projects', [])
            if projects and isinstance(projects, list):
                formatted_string += "Projects:\n"
                for project in projects:
                    if project and isinstance(project, dict):
                        for key in ['name', 'description', 'technologies', 'duration']:
                            value = project.get(key)
                            if value is not None:
                                if key == 'technologies':
                                    formatted_string += f"    {key.capitalize()}: " + ", ".join(value) + "\n"
                                else:
                                    formatted_string += f"  - {key.capitalize()}: {value}\n"

            certifications = user_data.get('certifications', [])
            if certifications and isinstance(certifications, list):
                formatted_string += "Certifications:\n"
                for cert in certifications:
                    if cert and isinstance(cert, dict):
                        for key in ['name', 'organization']:
                            value = cert.get(key)
                            if value is not None:
                                formatted_string += f"  - {key.capitalize()}: {value}\n"

            add_to_string('interests', 'Interests', is_list=True)
            add_to_string('previousCompanies', 'Previous Companies', is_list=True)

            return formatted_string
        except Exception as e:
            log_error(log, f"Error in format_user_data method: {e}")
            raise

    async def prepare_sys_prompt(self, sid, coach_id, session_scenario):
        """
        Prepare the system prompt.
        """
        try:
            coach_data = await get_coach(sid=sid, coach_id=coach_id)
            headers = create_headers(sid=sid)
            user_data = await get_user(headers)

            formatted_session_scenario = f"""
            Sesion Goal: {session_scenario["session_goal"]}
            Scenario: {session_scenario["scenario"]}
            """
            formatted_user_profile = self.format_user_data(user_data)
            formatted_coach_metadata = f"""
            Coach Name: {coach_data["coachName"]}
            Coach Descriptions {coach_data["coachDescription"]}
            """
            sys_prompt = [
                {
                    "role": "system",
                    "content": PREPARE_SYSTEM_PROMPT.format(scenario=formatted_session_scenario, coach_metadata=formatted_coach_metadata, system_prompt_json=PREPARE_SYSTEM_PROMPT_JSON, user_info=formatted_user_profile)
                }
            ]
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=sys_prompt,
                temperature=0,
                response_format={ "type": "json_object" }
            )

            content = response.choices[0].message.content
            # matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content = json.loads(content)

            return parsed_content["system_prompt"]
        except Exception as e:
            log_error(log, f"Error in prepare_sys_prompt method: {e}")
            raise

    async def create_conversation(self, session_id, coach_id):
        try:
            headers = create_headers(session_id)
            client_session, token = await extract_token_and_add_crsf(headers)
            if not client_session:
                raise HTTPException(status_code=403, detail="Invalid Authorization")

            response = await client_session.post("https://pr43546.degreed.dev/api/Coach/Conversations/Create", json={"coachId": coach_id})

            if response.status_code not in (200, 302, 201):
                raise HTTPException(status_code=response.status_code, detail=response.text)

            return response.json().get("conversationId")
        except Exception as e:
            log_error(log, f"Error in create_conversation method: {e}")
            raise

    async def get_conversation_messages(self, session_id, conversation_id):
        try:
            headers = create_headers(session_id)
            client_session, token = await extract_token_and_add_crsf(headers)
            if not client_session:
                raise HTTPException(status_code=403, detail="Invalid Authorization")

            response = await client_session.get(f"https://pr43546.degreed.dev/api/Coach/Conversations/{conversation_id}")
            if response.status_code not in (200, 302):
                raise HTTPException(status_code=response.status_code, detail=response.text)

            return response.json()
        except Exception as e:
            log_error(log, f"Error in get_conversation_messages method: {e}")
            raise

    async def evaluate_result(self, result, username, password, goal):
        """
        Evaluate the result of the test.
        """

        try:
            session_id = str(uuid.uuid4())
            cookies = await login_(session_id, username=username, password=password)

            tasks = [self.get_conversation_messages(session_id, res["conversation_id"]) for res in result]
            conversation_infos = await asyncio.gather(*tasks)

            for res, conversation_info in zip(result, conversation_infos):
                messages = conversation_info.get("messages")
                formatted_transcript = "\n".join([f'{msg["senderType"]}: {msg["messageText"]}' for msg in messages])
                res["transcript"] = formatted_transcript

            formatted_session_info = "\n".join([f'Session Goal: {res["session_goal"]}\nTranscript:\n{res["transcript"]}' for res in result])
            sys_prompt = [
                {
                    "role": "system",
                    "content": SESSION_EVALUATOR_TEMPLATE.format(session_info=formatted_session_info, overall_goal=goal, session_evaluator_json=SESSION_EVALUATOR_JSON)
                }
            ]
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=sys_prompt,
                temperature=0,
                response_format={ "type": "json_object" }
            )

            content = response.choices[0].message.content
            # matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content = json.loads(content)

            return parsed_content
   
        except Exception as e:
            log_error(log, f"Error in evaluate_result method: {e}")
            raise

@router.post("/test_gpt", tags=["Test"])
async def test_gpt(custom_header: str = Header(default=None)):
    """
    Endpoint to test GPT model response.
    """
    try:
        custom_header_value = "testaidgcoachgpt"
        # Check custom header value
        if custom_header == custom_header_value:
            messages = [
                {
                    "role": "system",
                    "content": "Hello, how can I help you today?"
                }
            ]
            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=messages,
                temperature=1
            )

            # Ensure the response is JSON serializable
            response_content = response.choices[0].message.content

        return JSONResponse(content={"response": response_content}, status_code=200)
    except Exception as e:
        log_error(log, f"Error in test_gpt: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
