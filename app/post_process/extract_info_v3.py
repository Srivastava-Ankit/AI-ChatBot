from asyncio import Semaphore
import asyncio
import re
import json
import os
import sys
from datetime import datetime
import uuid
from ddtrace import tracer
import pytz

from app.db.redis_manager import RedisManager
from app.dg_component.coach.coach import get_coach
from app.dg_component.find_content.find_learning_resources import find_learning_resources
from app.dg_component.find_content.request_builder import ContentSearchRequest
from app.dg_component.mentor.mentor import find_mentor
from app.dg_component.mentor.request_builder import MentorSearchRequest
from app.dg_component.profile import UserTagsParams, get_user_data, get_user_org
from app.llm.llm_client import AZURE_ASYNC_CLIENT
from app.llm.prompt import ACTIVITY_EXTRACT_INFO_TEMPLATE, AGENDA_TEMPLATE, AGENDA_TEMPLATE_JSON, BEHAVIOR_PATTERNS_TEMPLATE, BEHAVIOR_PATTERNS_TEMPLATE_JSON, CONVERSATION_CONTEXT_TEMPLATE, CONVERSATION_CONTEXT_TEMPLATE_JSON, CONVERSATION_ONE_LINER_TEMPLATE, CONVERSATION_ONE_LINER_TEMPLATE_JSON, CONVERSATION_SUMMARY_TEMPLATE, CONVERSATION_SUMMARY_TEMPLATE_JSON, FEEDBACK_TEMPLATE, FEEDBACK_TEMPLATE_JSON, KIRKPATRICK_EVALUATION_TEMPLATE, KIRKPATRICK_EVALUATION_TEMPLATE_JSON, PROGRESS_TEMPLATE, PROGRESS_TEMPLATE_JSON, RADAR_CHART, RECOMMENDATIONS_KEYWORDS_TEMPLATE, RECOMMENDATIONS_KEYWORDS_TEMPLATE_JSON, SKILL_PROGRESS_TEMPLATE_JSON, SKILL_PROGRESS_TEMPLATE, SKILL_RATING_GUIDE, SKILL_REVIEW_COACH_1_TEMPLATE, SKILL_REVIEW_TEMPLATE, SKILL_REVIEW_TEMPLATE_1_JSON, SKILL_REVIEW_TEMPLATE_JSON, USER_PROFILE_PREFERENCES_TEMPLATE, USER_PROFILE_PREFERENCES_TEMPLATE_JSON, VALIDATE_CONVERSATION_TEMPLATE, VALIDATE_CONVERSATION_TEMPLATE_JSON
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
import traceback

log = get_logger(__name__)

class ExtractInfoV3():
    def __init__(self, user_id, chat_datas, sid=None, coach_name=None, coach_data=None, coach_id=None, time_zone=None, additional_info={}):
        """
        Initialize the ExtractInfoV3 class with user and session details.

        Args:
            user_id (str): The ID of the user.
            chat_datas (dict): The chat data.
            sid (str, optional): The session ID. Defaults to None.
            coach_name (str, optional): The name of the coach. Defaults to None.
            coach_data (dict, optional): The coach data. Defaults to None.
            coach_id (str, optional): The ID of the coach. Defaults to None.
            time_zone (str, optional): The time zone. Defaults to None.
            additional_info (dict, optional): Additional information. Defaults to {}.
        """
        self.user_id = user_id
        self.coach_id = coach_id
        self.sid = sid
        self.coach_data = coach_data
        self.client = AZURE_ASYNC_CLIENT
        self.chat_datas = chat_datas
        self.max_retries = 5
        self.timezone = time_zone
        self.coach_name = coach_name
        self.additional_info = additional_info

    @tracer.wrap(name="dd_trace.initialize",service="degreed-coach-builder")
    async def initialize(self):
        """
        Asynchronous initialization to fetch coach name and user data.

        This method runs the fetching of coach data and user data in parallel.
        """
        try:
            if not self.coach_data:
                # Fetch coach data and user data concurrently
                coach_task = get_coach(self.sid, self.coach_id)
                user_task = get_user_data(self.sid, UserTagsParams(includeRatings=True, focusedOnly=False))
                self.coach_data, self.user_data = await asyncio.gather(coach_task, user_task)
            else:
                # Fetch only user data if coach data is already available
                self.user_data = await get_user_data(self.sid, UserTagsParams(includeRatings=True, focusedOnly=False))

            # Add pathway data to chat data if available
            if "pathway" in self.additional_info:
                self.chat_datas["PathwayData"] = self.additional_info["pathway"]

            self.coach_name = self.coach_data["coachName"]
            log_info(log, f"Additional info: {self.additional_info}\nCoach Data: {self.coach_data}\nUser Data: {self.user_data}")
        except Exception as e:
            log_error(log, f"Error during initialization: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            raise  # Re-raise the exception after logging

    @tracer.wrap(name="dd_trace.extract_info",service="degreed-coach-builder")
    async def extract_info(self, template, data_key):
        """
        Extract information from chat data using a given template and data key.

        Args:
            template (str): The template to be used for extracting information.
            data_key (str): The key for the data to be extracted.

        Returns:
            dict: Updated chat data with the extracted information.
        """
        try:
            if self.chat_datas["messages"]:
                # Prepare the message for the AI model
                message = [
                    {
                        "role": "system",
                        "content": template
                    },
                    {
                        "role": "user",
                        "content": f"Here is the conversation that happened at {self.chat_datas['startedAt']} - {self.chat_datas['endedAt']}, between the user and the AI Coach: \n{self.chat_datas['messages']}"
                    }
                ]
                attempts = 0
                log_info(log, f"Extracting {data_key} info.") 

                while attempts < self.max_retries:
                    try:
                        # Call the AI model to get the response
                        response = await self.client.chat.completions.create(
                            model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                            messages=message
                        )
                        content = response.choices[0].message.content
                        if data_key == "ConversationSummary":
                            parsed_content = {"conversation_summary": content}
                        else:
                            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
                            parsed_content = json.loads(matches[0])

                        if data_key == "SkillReview":
                            # Process skill review data
                            if "attributes" in parsed_content:
                                skills, levels = zip(*[(skill["attribute_name"], skill["attribute_level"]) for skill in parsed_content["attributes"]])
                                parsed_content["sub_skills"] = parsed_content.pop("attributes")
                                for skill in parsed_content["sub_skills"]:
                                    skill["sub_skill_name"] = skill.pop("attribute_name")
                                    skill["sub_skill_level"] = skill.pop("attribute_level")

                            elif "sub_skills" in parsed_content:
                                skills, levels = zip(*[(skill["sub_skill_name"], skill["sub_skill_level"]) for skill in parsed_content["sub_skills"]])
                                keywords = [skill.pop("keyword") for skill in parsed_content["sub_skills"]]
                                if keywords:
                                    responses = await self.get_mentor_and_content(keywords)
                                    if "recommendations" not in self.chat_datas:
                                        self.chat_datas["recommendations"] = responses
                                    else:
                                        self.chat_datas["recommendations"].extend(responses)

                            RADAR_CHART["title"]["text"] = parsed_content.pop("title")
                            RADAR_CHART["xAxis"]["categories"] = skills
                            RADAR_CHART["series"][0]["data"] = levels
                            parsed_content["radar_chart"] = RADAR_CHART
                        elif data_key == "recommendations":
                            # Process recommendations data
                            responses = await self.get_mentor_and_content(parsed_content)
                            if "recommendations" not in self.chat_datas:
                                self.chat_datas["recommendations"] = responses
                            else:
                                self.chat_datas["recommendations"].extend(responses)
                            log_info(log, f"{data_key} data extracted.")
                            break 

                        self.chat_datas[data_key] = parsed_content
                        log_info(log, f"{data_key} data extracted.")
                        break
                    except Exception as e:
                        attempts += 1
                        log_error(log, f"Extracting {data_key} info failed. Error: {e}\nLLM Response: {content}")
                        log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
                        continue
            else:
                log_warn(log, f"No data found.")
        except Exception as e:
            log_error(log, f"Error in extract_info: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
        return self.chat_datas
    
    @tracer.wrap(name="dd_trace.extract_kirkpatrick_evaluation",service="degreed-coach-builder")
    async def extract_kirkpatrick_evaluation(self):
        """
        Extract Kirkpatrick evaluation data and update the chat data.

        This method uses the Kirkpatrick evaluation template to extract relevant information
        and updates the 'KirkpatrickEvaluation' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting Kirkpatrick evaluation.")
            existing_evaluation = self.chat_datas.get("KirkpatrickEvaluation", None)

            await self.extract_info(
                KIRKPATRICK_EVALUATION_TEMPLATE.format(
                    coach=self.coach_name,
                    existing_evaluation=existing_evaluation,
                    evaluation_template_json=KIRKPATRICK_EVALUATION_TEMPLATE_JSON
                ),
                "KirkpatrickEvaluation"
            )
        except Exception as e:
            log_error(log, f"Error in extract_kirkpatrick_evaluation: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_user_profile_preferences",service="degreed-coach-builder")
    async def extract_user_profile_preferences(self):
        """
        Extract user profile preferences and update the chat data.

        This method uses the user profile preferences template to extract relevant information
        and updates the 'UserLearningPreferences' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting user profile preferences.")
            existing_profile = self.chat_datas.get("UserLearningPreferences", None)

            await self.extract_info(
                USER_PROFILE_PREFERENCES_TEMPLATE.format(
                    coach=self.coach_name,
                    profile=existing_profile,
                    profile_json=USER_PROFILE_PREFERENCES_TEMPLATE_JSON
                ),
                "UserLearningPreferences"
            )
        except Exception as e:
            log_error(log, f"Error in extract_user_profile_preferences: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_conversation_context",service="degreed-coach-builder")
    async def extract_conversation_context(self):
        """
        Extract conversation context and update the chat data.

        This method uses the conversation context template to extract relevant information
        and updates the 'ConversationContext' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting conversation context.")
            await self.extract_info(
                CONVERSATION_CONTEXT_TEMPLATE.format(
                    coach=self.coach_name,
                    conversation_context_json=CONVERSATION_CONTEXT_TEMPLATE_JSON
                ),
                "ConversationContext"
            )
        except Exception as e:
            log_error(log, f"Error in extract_conversation_context: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_skill_progress",service="degreed-coach-builder")
    async def extract_skill_progress(self):
        """
        Extract skill progress and update the chat data.

        This method reads the coach skill data from a JSON file and updates the 'SkillProgress' key in the chat data.
        It formats the required skills based on the coach ID and existing skills.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting skill progress.")
            with open(COACH_SKILL_DATA_PATH, 'r') as file: # TODO:: Update Get coach skill data from .Net layer As of now this extract_skill_progress is not used
                coach_skill_data = json.load(file)

            existing_skill = self.chat_datas.get("SkillProgress", None)

            if self.coach_id == "Career_Development":
                user_skills = self.user_data["skills"]
                inferred_skills = self.user_data["inferred_skill"]["Skills"]
                required_skill = f"Here is the user's evaluated skills and their levels: {user_skills}\n\nThese are the skills that the user is required to learn: {inferred_skills}"
            else:
                required_skill = coach_skill_data[self.coach_id]["skills"]

            await self.extract_info(
                SKILL_PROGRESS_TEMPLATE.format(
                    coach=self.coach_name,
                    skill_progress_json=SKILL_PROGRESS_TEMPLATE_JSON,
                    required_skills=required_skill,
                    existing_skills=existing_skill
                ),
                "SkillProgress"
            )
        except Exception as e:
            log_error(log, f"Error in extract_skill_progress: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_behavior_patterns",service="degreed-coach-builder")
    async def extract_behavior_patterns(self):
        """
        Extract behavior patterns and update the chat data.

        This method uses the behavior patterns template to extract relevant information
        and updates the 'BehaviorPatterns' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting behavior patterns.")
            previous_behavior = self.chat_datas.get("BehaviorPatterns", None)
            await self.extract_info(
                BEHAVIOR_PATTERNS_TEMPLATE.format(
                    coach=self.coach_name,
                    behavior_patterns_json=BEHAVIOR_PATTERNS_TEMPLATE_JSON,
                    previous_patterns=previous_behavior
                ),
                "BehaviorPatterns"
            )
        except Exception as e:
            log_error(log, f"Error in extract_behavior_patterns: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_skill_assessment",service="degreed-coach-builder")
    async def extract_skill_assessment(self):
        """
        Extract skill assessment and update the chat data.

        This method uses the skill assessment template to extract relevant information
        and updates the 'SkillReview' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            skill_name = self.additional_info.get("skill", {}).get("name")
            skill_level = self.additional_info.get("skill", {}).get("level")

            if skill_name:
                skill_data = f"Here is the skill that you will be evaluating: {skill_name}. While having the conversation, the user is at level {skill_level}."
            else:
                skill_data = "Refer to the conversation and pick the skill that's been discussed."

            # if self.coach_data["coachId"] == 9 and self.coach_data["coachSubType"] in ["Skills"]:
            #     log_info(log, "Extracting skill assessment.")
            #     previous_skill_review = None
            #     await self.extract_info(
            #         SKILL_REVIEW_TEMPLATE.format(
            #             coach=self.coach_name,
            #             skill_data=skill_data,
            #             skill_rating_guidelines=SKILL_RATING_GUIDE,
            #             skill_review_json=SKILL_REVIEW_TEMPLATE_JSON,
            #             previous_skill_review=previous_skill_review
            #         ),
            #         "SkillReview"
            #     )
            # else:
            log_info(log, "Extracting skill assessment.")
            previous_skill_review = None
            await self.extract_info(
                SKILL_REVIEW_COACH_1_TEMPLATE.format(
                    coach=self.coach_name,
                    skill_data=skill_data,
                    skill_rating_guidelines=SKILL_RATING_GUIDE,
                    skill_review_json=SKILL_REVIEW_TEMPLATE_1_JSON,
                    previous_skill_review=previous_skill_review
                ),
                "SkillReview"
            )
        except Exception as e:
            log_error(log, f"Error in extract_skill_assesment: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_feedback",service="degreed-coach-builder")
    async def extract_feedback(self):
        """
        Extract feedback and update the chat data.

        This method uses the feedback template to extract relevant information
        and updates the 'Feedback' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting feedback.")
            previous_feedback = self.chat_datas.get("Feedback", None)
            await self.extract_info(
                FEEDBACK_TEMPLATE.format(
                    coach=self.coach_name,
                    feedback_json=FEEDBACK_TEMPLATE_JSON,
                    previous_feedback_json=previous_feedback
                ),
                "Feedback"
            )
        except Exception as e:
            log_error(log, f"Error in extract_feedback: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_agenda",service="degreed-coach-builder")
    async def extract_agenda(self):
        """
        Extract agenda and update the chat data.

        This method uses the agenda template to extract relevant information
        and updates the 'Agenda' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting Agenda.")
            agenda = self.chat_datas.get("Agenda", None)
            await self.extract_info(
                AGENDA_TEMPLATE.format(
                    coach=self.coach_name,
                    agenda_json=AGENDA_TEMPLATE_JSON,
                    previous_agenda=agenda
                ),
                "Agenda"
            )
        except Exception as e:
            log_error(log, f"Error in extract_agenda: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_progress",service="degreed-coach-builder")
    async def extract_progress(self):
        """
        Extract user's progress and update the chat data.

        This method uses the progress template to extract relevant information
        and updates the 'Progress' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting user's Progress.")
            progress = self.chat_datas.get("Progress", None)
            await self.extract_info(
                PROGRESS_TEMPLATE.format(
                    coach=self.coach_name,
                    progress_json=PROGRESS_TEMPLATE_JSON,
                    previous_progress=progress
                ),
                "Progress"
            )
        except Exception as e:
            log_error(log, f"Error in extract_progress: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_action_items",service="degreed-coach-builder")
    async def extract_action_items(self):
        """
        Extract action items and update the chat data.

        This method uses the activity extract info template to extract relevant information
        and updates the 'TaskItems' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting action items.")
            await self.extract_info(ACTIVITY_EXTRACT_INFO_TEMPLATE, "TaskItems")
        except Exception as e:
            log_error(log, f"Error in extract_action_items: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_conversation_one_liner",service="degreed-coach-builder")
    async def extract_conversation_one_liner(self):
        """
        Extract conversation one-liner and update the chat data.

        This method uses the conversation one-liner template to extract relevant information
        and updates the 'ConversationOneLiner' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting Conversation One Liner.")
            await self.extract_info(
                CONVERSATION_ONE_LINER_TEMPLATE.format(
                    coach=self.coach_name,
                    conversation_summary_json=CONVERSATION_ONE_LINER_TEMPLATE_JSON
                ),
                "ConversationOneLiner"
            )
        except Exception as e:
            log_error(log, f"Error in extract_conversation_one_liner: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_conversation_summary",service="degreed-coach-builder")
    async def extract_conversation_summary(self):
        """
        Extract conversation summary and update the chat data.

        This method uses the conversation summary template to extract relevant information
        and updates the 'ConversationSummary' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting Conversation Summary.")
            await self.extract_info(
                CONVERSATION_SUMMARY_TEMPLATE.format(
                    coach=self.coach_name,
                    conversation_summary_json=CONVERSATION_SUMMARY_TEMPLATE_JSON
                ),
                "ConversationSummary"
            )
        except Exception as e:
            log_error(log, f"Error in extract_conversation_summary: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.extract_recommendations_keywords",service="degreed-coach-builder")
    async def extract_recommendations_keywords(self):
        """
        Extract recommendations keywords and update the chat data.

        This method uses the recommendations keywords template to extract relevant information
        and updates the 'recommendations' key in the chat data.

        Raises:
            Exception: If there is an error during the extraction process.
        """
        try:
            log_info(log, "Extracting Recommendations.")
            await self.extract_info(
                RECOMMENDATIONS_KEYWORDS_TEMPLATE.format(
                    coach=self.coach_name,
                    recommendations_keywords_json=RECOMMENDATIONS_KEYWORDS_TEMPLATE_JSON
                ),
                "recommendations"
            )
        except Exception as e:
            log_error(log, f"Error in extract_recommendations_keywords: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging

    @tracer.wrap(name="dd_trace.find_content",service="degreed-coach-builder")
    async def find_content(self, key, types=None):
        """
        Find content based on the provided key and types.

        Args:
            key (str): The search term to find content.
            types (Optional[list]): The types of content to filter by.

        Returns:
            dict: A dictionary containing the RecommendedItemId and RecommendedItemType of the found content.
            None: If an error occurs during the process.
        """
        try:
            # Build the search request with or without types filter
            if types:
                search_request = ContentSearchRequest.Builder().set_terms(key).set_count(1).set_filters({"type": types}).build()
            else:
                search_request = ContentSearchRequest.Builder().set_terms(key).set_count(1).build()

            # Send the search request and process the response
            response = await find_learning_resources(self.sid, search_request)
            # Check which response format is present
            if "Results" in response:
                id = {"RecommendedItemId": response["Results"][0].get("ReferenceId"), 
                      "RecommendedItemType": response["Results"][0].get("ReferenceType")}
            else:
                id = {"RecommendedItemId": response["results"][0].get("referenceId"), 
                      "RecommendedItemType": response["results"][0].get("referenceType")}
            return id
        except Exception as e:
            log_error(log, f"Error in find_content: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            return None

    @tracer.wrap(name="dd_trace.find_mentor",service="degreed-coach-builder")
    async def find_mentor(self, key, org_id):
        """
        Find a mentor based on the provided key and organization ID.

        Args:
            key (str): The search term to find a mentor.
            org_id (str): The organization ID to filter mentors.

        Returns:
            dict: A dictionary containing the RecommendedItemId and RecommendedItemType of the found mentor.
            None: If an error occurs during the process.
        """
        try:
            # Build the search request for mentors
            search_request = MentorSearchRequest.Builder().set_terms(key).set_count(1).set_organization_id(org_id).build()

            # Send the search request and process the response
            response = await find_mentor(self.sid, search_request)
            if "profiles" in response:
                id = {"RecommendedItemId": response.get("profiles", [{}])[0].get("userProfileKey"), 
                      "RecommendedItemType": "User"}
            else:
                id = {"RecommendedItemId": response.get("Items", [{}])[0].get("UserProfile", {}).get("UserProfileKey"), 
                      "RecommendedItemType": "User"}
            return id
        except Exception as e:
            log_error(log, f"Error in find_mentor: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            return None
    
    @tracer.wrap(name="dd_trace.get_mentor_and_content",service="degreed-coach-builder")
    async def get_mentor_and_content(self, key):
        """
        Retrieve both mentor and content information based on the provided key.

        Args:
            key (Union[list, dict]): The search terms to find mentors and content.

        Returns:
            list: A list of responses containing mentor and content information.
            list: An empty list if an error occurs during the process.
        """
        try:
            # Get the organization ID for the user
            org_id = await get_user_org(self.sid)
            tasks = []

            # Check if the key is a list
            if isinstance(key, list):
                for k in key:
                    tasks.append(self.find_content(k, types=['book', 'article', 'video', 'course']))
                    tasks.append(self.find_content(k, types=['pathway']))
                    tasks.append(self.find_mentor(k, org_id))
                responses = await asyncio.gather(*tasks)
                return responses

            # Check if the key is a dictionary
            elif isinstance(key, dict):
                for k, v in key.items():
                    if k == "Content":
                        for content in v:
                            tasks.append(self.find_content(content, types=['book', 'article', 'video', 'course']))
                    elif k == "Mentor":
                        for mentor in v:
                            tasks.append(self.find_mentor(mentor, org_id))
                    elif k == "Pathways":
                        for pathway in v:
                            tasks.append(self.find_content(pathway, types=['pathway']))
                responses = [response for response in await asyncio.gather(*tasks) if response is not None]
                return responses
                
        except Exception as e:
            log_error(log, f"Error in get_mentor_and_content: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            return []
        
    @tracer.wrap(name="dd_trace.validate_conversation",service="degreed-coach-builder")
    async def validate_conversation(self):
        """
        Validate the conversation between the user and the AI Coach.

        This method sends the conversation data to the Azure GPT-4 model for validation and returns the parsed content.

        Returns:
            dict: Parsed content from the model's response.
            bool: False if an error occurs during the process.
        """
        try:
            if self.chat_datas["messages"]:
                message = [
                    {
                        "role": "system",
                        "content": VALIDATE_CONVERSATION_TEMPLATE.format(validate_conversation_json=VALIDATE_CONVERSATION_TEMPLATE_JSON)
                    },
                    {
                        "role": "user",
                        "content": f"Here is the conversation that happened at {self.chat_datas['startedAt']} - {self.chat_datas['endedAt']}, between the user and the AI Coach: \n{self.chat_datas['messages']}"
                    }
                ]
                attempts = 0
                log_info(log, "Validating Conversation.") 

                while attempts < self.max_retries:
                    try:
                        response = await self.client.chat.completions.create(
                            model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                            messages=message
                        )
                        content = response.choices[0].message.content
                        matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
                        parsed_content = json.loads(matches[0])
                        return parsed_content
                    except Exception as e:
                        attempts += 1
                        log_error(log, f"Validating conversation failed. Error: {e}\nLLM Response: {content}")
                        log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
                        continue

        except Exception as e:
            log_error(log, f"Error in validate_conversation: {e}")
            log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
            return False

    @tracer.wrap(name="dd_trace.trigger_post_process",service="degreed-coach-builder")
    async def trigger_post_process(self):
        """
        Trigger the post-processing tasks for the conversation.

        This method runs various post-processing tasks with retries and gathers the results.

        Returns:
            dict: The chat data after post-processing.
        """
        async def run_with_retries(coro, max_retries=3):
            """
            Run a coroutine with retries.

            Args:
                coro (coroutine): The coroutine to run.
                max_retries (int): The maximum number of retries. Defaults to 3.
            """
            attempts = 0
            while attempts < max_retries:
                try:
                    await coro
                    break
                except Exception as e:
                    attempts += 1
                    log_error(log, f"Error in {coro.__name__}: {e}")
                    log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
                    if attempts == max_retries:
                        log_error(log, f"Max retries reached for {coro.__name__}")

        tasks = [
            run_with_retries(self.extract_conversation_context()),
            run_with_retries(self.extract_feedback()),
            run_with_retries(self.extract_kirkpatrick_evaluation()),
            run_with_retries(self.extract_conversation_one_liner()),
            run_with_retries(self.extract_conversation_summary()),
            run_with_retries(self.extract_recommendations_keywords()),
            run_with_retries(self.extract_agenda()),
            run_with_retries(self.extract_progress()),
        ]
        if self.coach_data["coachSubType"] in ["Skills"]:
            tasks.append(run_with_retries(self.extract_skill_assessment()))
        else:
            tasks.extend([
                run_with_retries(self.extract_user_profile_preferences()),
                # run_with_retries(self.extract_skill_progress()),
                run_with_retries(self.extract_behavior_patterns()),
                run_with_retries(self.extract_action_items()),
            ])

        await asyncio.gather(*tasks)
        return self.chat_datas


    @tracer.wrap(name="dd_trace.trigger_partial_post_process",service="degreed-coach-builder")
    async def trigger_partial_post_process(self):
        """
        Trigger the post-processing tasks for the conversation.

        This method runs various post-processing tasks with retries and gathers the results.

        Returns:
            dict: The chat data after post-processing.
        """
        async def run_with_retries(coro, max_retries=3):
            """
            Run a coroutine with retries.

            Args:
                coro (coroutine): The coroutine to run.
                max_retries (int): The maximum number of retries. Defaults to 3.
            """
            attempts = 0
            while attempts < max_retries:
                try:
                    await coro
                    break
                except Exception as e:
                    attempts += 1
                    log_error(log, f"Error in {coro.__name__}: {e}")
                    log_debug(log, traceback.format_exc())  # Log the traceback for better debugging
                    if attempts == max_retries:
                        log_error(log, f"Max retries reached for {coro.__name__}")

        tasks = [
            run_with_retries(self.extract_conversation_one_liner()),
            run_with_retries(self.extract_conversation_summary()),
            run_with_retries(self.extract_recommendations_keywords()),
            run_with_retries(self.extract_action_items()),
        ]
        await asyncio.gather(*tasks)
        return self.chat_datas
