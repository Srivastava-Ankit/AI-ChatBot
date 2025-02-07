import asyncio
import json
from datetime import datetime, timedelta
from collections import defaultdict
import os
import traceback
from typing import Dict, List
import pytz
from ddtrace import tracer

from app.db.langchain_chroma_manager import LangchainChromaManager
from app.db.redis_manager import RedisManager
from app.dg_component.profile import UserTagsParams, get_user_data
from app.llm.prompt import ACTION_ITEMS_PROMPT_TEMPLATE, COACH_INSTRUCTIONS, END_NOTE_PROMPT_TEMPLATE, KEY_POINTS_PROMPT_TEMPLATE, KNOWLEDGE_BASE_PROMPT_TEMPLATE, PATHWAY_PROMPT_TEMPLATE, PLAN_PROMPT_TEMPLATE, TEXT_HEADER, VOICE_HEADER, VOICE_PROMPT_TEMPLATE, TEXT_PROMPT_TEMPLATE
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.request_and_response.custom_types import ResponseRequiredRequest, Utterance
from app.dg_component.coach.coach import get_coach
from app.utils.api_utils import get_previous_conversation, save_message

# Get the logger instance
log = get_logger(__name__)

class PromptPreprocessor:
    def __init__(self, user_id=None, call_id=None, conversation_id=None, coach_id=None, time_zone='Asia/Kolkata'):
        """
        Initialize the PromptPreprocessor with user, call, conversation, and coach details.

        Args:
            user_id (str, optional): The ID of the user. Defaults to None.
            call_id (str, optional): The ID of the call. Defaults to None.
            conversation_id (str, optional): The ID of the conversation. Defaults to None.
            coach_id (str, optional): The ID of the coach. Defaults to None.
            time_zone (str, optional): The time zone for the user. Defaults to 'Asia/Kolkata'.
        """
        self.timezone = time_zone
        self.call_id = call_id
        self.user_id = user_id
        self.coach_id = coach_id
        self.conversation_id = conversation_id
        self.redis_manager = RedisManager()
        self.chroma_manager = LangchainChromaManager()
        self.plus_day = 0
        self.plus_hours = 0

    @tracer.wrap(name="dd_trace.initialize",service="degreed-coach-builder")
    async def initialize(self):
        """
        Asynchronous initialization to fetch coach name and user data.

        This method runs the fetching of coach data and user data in parallel.
        It first attempts to retrieve the data from Redis. If the data is not available in Redis,
        it fetches the data from the respective sources and stores it in Redis for future use.

        Raises:
            Exception: Logs any exception that occurs during the initialization process.
        """
        tasks = []
        
        try:
            # Fetch coach data from Redis if available
            coach_data = self.redis_manager.get_coach_data(self.call_id)
            if not coach_data:
                tasks.append(get_coach(self.call_id, self.coach_id))
            else:
                self.coach_data = coach_data

            # Fetch user data from Redis if available
            user_data = self.redis_manager.get_user_data(self.call_id)
            if not user_data:
                tasks.append(get_user_data(self.call_id, UserTagsParams(includeRatings=True, focusedOnly=False)))
            else:
                self.user_data = user_data

            if tasks:
                # Run the tasks concurrently
                results = await asyncio.gather(*tasks)
                if not coach_data:
                    self.coach_data = results[0]
                    self.redis_manager.add_coach_data(self.call_id, self.coach_data)
                if not user_data:
                    self.user_data = results[-1]
                    self.redis_manager.add_user_data(self.call_id, self.user_data)
        except Exception as e:
            log_error(log, f"Error during initialization: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")

    @tracer.wrap(name="dd_trace.format_coach_details",service="degreed-coach-builder")
    def format_coach_details(self, coach_details: List[dict], required_keys: List[str]) -> str:
        """
        Format coach details into a readable string.

        Args:
            coach_details (List[dict]): List of dictionaries containing coach details.
            required_keys (List[str]): List of keys that are required in the formatted string.

        Returns:
            str: Formatted string of coach details.

        Raises:
            Exception: Logs any exception that occurs during the formatting process.
        """
        try:
            formatted_str = ""
            # Iterate over each coach's details
            for coach_id, coach_data in coach_details.items():
                # Skip if the coach is marked as a master
                if coach_data.get("is_master"):
                    continue
                formatted_str += f"### Coach ID `{coach_id}`:\n"
                # Iterate over each key-value pair in the coach's data
                for key, value in coach_data.items():
                    # Only include required keys
                    if key not in required_keys:
                        continue
                    # Format the value based on its type
                    if isinstance(value, list):
                        formatted_str += f"{key.capitalize()}: {', '.join(value)}\n"
                    elif isinstance(value, bool):
                        formatted_str += f"{key.capitalize()}: {'Yes' if value else 'No'}\n"
                    else:
                        formatted_str += f"{key.capitalize()}: {value}\n"
            return formatted_str
        except Exception as e:
            log_error(log, f"Error formatting coach details: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            return ""

    @tracer.wrap(name="dd_trace.format_user_preferences",service="degreed-coach-builder")
    def format_user_preferences(self, user_preferences: dict) -> str:
        """
        Format user preferences into a readable string.

        Args:
            user_preferences (dict): Dictionary containing user preferences.

        Returns:
            str: Formatted string of user preferences.

        Raises:
            Exception: Logs any exception that occurs during the formatting process.
        """
        try:
            formatted_str = "### User Profile Preferences:\n"
            formatted_str += (
                "This section contains the user's preferences, goals, and objectives which is not explicitly mentioned "
                "in the conversation but extracted based on the conversations over the period of time.\n"
            )
            formatted_str += (
                "Use this information to personalize your responses and tailor the conversation to the user's needs.\n"
            )
            formatted_str += f"Name and Personal Details: {user_preferences.get('name_and_personal_details', 'N/A')}\n\n"
            
            preferences = user_preferences.get('preferences', {})
            formatted_str += "#### User Profile Preferences:\n"
            formatted_str += f"  Topics of Interest: {preferences.get('topics_of_interest', 'N/A')}\n"
            formatted_str += f"  Preferred Learning Style: {preferences.get('preferred_learning_style', 'N/A')}\n"
            formatted_str += f"  Preferred Interaction Times: {preferences.get('preferred_interaction_times', 'N/A')}\n"
            formatted_str += f"  ETC: {preferences.get('ETC', 'N/A')}\n\n"
            
            goals_and_objectives = user_preferences.get('goals_and_objectives', {})
            formatted_str += "Goals and Objectives:\n"
            formatted_str += f"  Long-term Goals: {goals_and_objectives.get('long_term_goals', 'N/A')}\n"
            formatted_str += f"  Short-term Goals: {goals_and_objectives.get('short_term_goals', 'N/A')}\n\n"
            
            formatted_str += f"Previous Feedback: {user_preferences.get('previous_feedback', 'N/A')}\n"
            
            return formatted_str
        except Exception as e:
            log_error(log, f"Error formatting user preferences: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            return ""

    @tracer.wrap(name="dd_trace.extract_conversation",service="degreed-coach-builder")
    def extract_conversation(self, conversations, key):
        """
        Extracts the conversation data based on the provided key.

        Args:
            conversations (list): List of conversation dictionaries.
            key (str): The type of data to extract from the conversations.

        Returns:
            dict or list or None: The extracted data based on the key or None if not found.
        """
        try:
            data = []
            if key == "ConversationContext":
                for conversation in conversations["inferences"]:
                    conversation_data = {
                        "start_time": conversation.get("startedAt"),
                        "end_time": conversation.get("endedAt")
                    }
                    for inference in conversation["inferences"]:
                        if inference.get("inferenceType") == key:
                            conversation_data["conversation_context"] = json.loads(inference.get("inferredData"))
                            data.append(conversation_data)
                            break
                return data

            elif key == "TaskItems":
                for conversation in conversations["inferences"]:
                    for inference in conversation["inferences"]:
                        if inference.get("inferenceType") == key:
                            data.extend(json.loads(inference.get("inferredData")).get("Activity", []))
                return data

            elif key == "PathwayData":
                for conversation in conversations["inferences"]:
                    if conversation.get("conversationId") == self.conversation_id:
                        for inference in conversation["inferences"]:
                            if inference.get("inferenceType") == key:
                                pathway = json.loads(inference.get("inferredData"))
                                if isinstance(pathway, str):
                                    pathway = json.loads(pathway)
                                return pathway
                return None

            else:
                for conversation in conversations["inferences"]:
                    for inference in conversation["inferences"]:
                        if inference.get("inferenceType") == key:
                            data.append(inference)
                            break

            if not data:
                return None

            return json.loads(max(data, key=lambda x: x.get('conversationId'))["inferredData"])

        except Exception as e:
            log_error(log, f"Error extracting conversation: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            return None

    @tracer.wrap(name="dd_trace.get_previous_conversation", service="degreed-coach-builder")
    async def get_previous_conversation(self, last_n_conversations=-10):
        """
        Retrieve and format the previous conversation details for the user.

        Args:
            last_n_conversations (int): Number of past conversations to retrieve. Defaults to -10.

        Returns:
            str: Formatted string containing the previous conversation details.
        """
        if self.coach_data["coachSubType"] in ["Skills"]:
            return "No previous conversation found."

        previous_conversation = self.redis_manager.get_previous_session_data(key=self.call_id)
        log_info(log, f"Previous conversation from Redis: {previous_conversation}")
        if not previous_conversation:
            log_info(log, "Getting previous conversation")
            previous_conversation_info = await get_previous_conversation(sid=self.call_id, conversation_id=self.conversation_id, coach_id=self.coach_id)
            # logger.info(f"Previous conversation info: {previous_conversation_info}")
            if previous_conversation_info and self.coach_data["coachSubType"] not in ["Skills"]:

                previous_conversation = ""
                # Exclude specific keys from the conversation items
                conversation_items = self.extract_conversation(previous_conversation_info, "ConversationContext")
                last_10_items = list(conversation_items)[last_n_conversations:]

                feedback = self.extract_conversation(previous_conversation_info, "Feedback")
                behavior_patterns = self.extract_conversation(previous_conversation_info, "BehaviorPatterns")
                user_profile_preferences = self.extract_conversation(previous_conversation_info, "UserLearningPreferences")
                skill_progress = self.extract_conversation(previous_conversation_info, "SkillProgress")
                kirkpatrick_evaluation = self.extract_conversation(previous_conversation_info, "KirkpatrickEvaluation")
                skill_review = self.extract_conversation(previous_conversation_info, "SkillReview")
                agenda = self.extract_conversation(previous_conversation_info, "Agenda")
                progress = self.extract_conversation(previous_conversation_info, "Progress")

                if progress:
                    previous_conversation += f"### Progress:\n"
                    previous_conversation += "Use this Progress to know what the user learned in the session, what skills improved, and what needs to be improved more.\n"
                    previous_conversation += "you can also use this progress to congratulate the user on their progress, motivate them to improve more\n"
                    previous_conversation += f"**Learned:** {progress.get('progress', {}).get('learned', 'N/A')}\n"
                    previous_conversation += f"**Improved:** {progress.get('progress', {}).get('improved', 'N/A')}\n"
                    previous_conversation += f"**Need to Improve:** {progress.get('progress', {}).get('need_to_improve', 'N/A')}\n\n"
                    
                if agenda:
                    previous_conversation += f"### Agenda:\n"
                    previous_conversation += "Use this Agenda to know what topics to discuss, activities planned for this session, and goals for this session. It's not that important to follow this if user has some other plan go with it, it's just the outline\n"
                    previous_conversation += f"**Topics to Discuss:** {', '.join(agenda.get('agenda').get('topics', ['N/A']))}\n"
                    previous_conversation += f"**Activities Planned for this session:** {', '.join(agenda.get('agenda').get('activities', ['N/A']))}\n"
                    previous_conversation += f"**Goals for this session:** {', '.join(agenda.get('agenda').get('goals', ['N/A']))}\n\n"

                if last_10_items:
                    
                    previous_conversation += "### Previous Session Conversation Knowledge\n"
                    previous_conversation += f"**Number of Previous Conversations:** {len(last_10_items)}\n"
                    previous_conversation += "Use this Conversation Context to know what happened in the previous conversation and what needs to be done in the current conversation, most importantly tune the conversation to upskill the user.\n"
                    for conversation in last_10_items:
                        # Conversation Context
                        conversation_context = conversation.get('conversation_context', {})
                        if not conversation_context:
                            continue
                        previous_conversation += f"##### Time of Conversation - {conversation.get('start_time')} - {conversation.get('end_time') or 'currently going on'}\n"
                        previous_conversation += f"**Conversation Summary:** {conversation_context.get('last_conversation_summary', 'N/A')}\n"
                        # previous_conversation += f"**Previous Questions and Responses:**\n"
                        # for q_and_r in conversation_context.get('previous_questions_and_responses', []):
                        #     previous_conversation += f"  - **Question:** {q_and_r.get('question', 'N/A')}\n"
                        #     previous_conversation += f"    **Response:** {q_and_r.get('response', 'N/A')}\n"
                        previous_conversation += f"**Unresolved Issues or Questions:** {conversation_context.get('unresolved_issues_or_questions', 'N/A')}\n"
                        previous_conversation += f"**Emotional Tone and Sentiment:** {conversation_context.get('emotional_tone_and_sentiment', 'N/A')}\n\n"

                # Feedback
                if feedback:
                    previous_conversation += f"### Feedback:\n"
                    previous_conversation += "Use this Feedback to know how the user felt about the previous session, what they liked, what they didn't like, and what they want to change.\n"
                    previous_conversation += "Use this Feedback and adapt your coaching style to meet the user's needs.\n"
                    previous_conversation += f"**User Feedback on Coach Performance:** {feedback.get('user_feedback_on_coach_performance', 'N/A')}\n"
                    previous_conversation += f"**Adaptations in Coaching Style:** {feedback.get('adaptations_in_coaching_style', 'N/A')}\n"
                    previous_conversation += f"**User Disagreements:** {feedback.get('user_disagreements', 'N/A')}\n\n"
                
                # Behavior Patterns
                if behavior_patterns:
                    previous_conversation += f"### Behavior Patterns:\n"
                    previous_conversation += "Use this Behavior Patterns to know how the user interacts, responds, and what motivates them.\n"
                    previous_conversation += "Use this Behavior Patterns information to personalize your responses and tailor the conversation to the user's needs.\n"
                    previous_conversation += f"**Interaction Patterns:** {behavior_patterns.get('interaction_patterns', 'N/A')}\n"
                    previous_conversation += f"**Response Patterns:** {behavior_patterns.get('response_patterns', 'N/A')}\n"
                    previous_conversation += f"**Motivational Triggers:** {behavior_patterns.get('motivational_triggers', 'N/A')}\n\n"
                
                # User Profile Preferences
                if user_profile_preferences:
                    previous_conversation += self.format_user_preferences(user_profile_preferences)
                
                # Skill Review
                if skill_review:
                    previous_conversation += f"### Skill Review:\n"
                    previous_conversation += f"**{skill_review.get('skill_name')}** - Level {skill_review.get('skill_level')}\n"
                    previous_conversation += f"**User's understanding about the Skill:** {skill_review.get('skill_understanding')}\n"
                    previous_conversation += f"**Reason for the skill level given in the conversation:** {skill_review.get('reason')}\n"
                    previous_conversation += f"**Improvement needed in the skill:** {skill_review.get('improvement')}\n\n"
                    
                # Skill Progress
                if skill_progress:
                    previous_conversation += f"### Skill Progress:\n"
                    for skill, skill_data in skill_progress.items():
                        if skill != 'observation':
                            previous_conversation += f"**{skill}** - Level {skill_data.get('level', 'N/A')}\n"
                            for sub_skill, sub_skill_data in skill_data.get('sub_skills', {}).items():
                                previous_conversation += f"  - **{sub_skill}** - Level {sub_skill_data.get('level', 'N/A')}\n"
                                for sub_sub_skill, level in sub_skill_data.get('sub_sub_skills', {}).items():
                                    previous_conversation += f"    - **{sub_sub_skill}** - Level {level}\n"
                                previous_conversation += f"    - **Observation** - {sub_skill_data.get('observation', 'N/A')}\n"
                            previous_conversation += f"  - **Observation** - {skill_data.get('observation', 'N/A')}\n"
                    previous_conversation += f"**Overall Observation** - {skill_progress.get('observation', 'N/A')}\n\n"

                # Kirkpatrick Evaluation
                if kirkpatrick_evaluation:
                    previous_conversation += f"### Kirkpatrick Evaluation:\n"
                    previous_conversation += "Use this Kirkpatrick Evaluation to know how the user's enagement, learning, behavior, and results have changed over the period of time.\n"
                    evaluation = kirkpatrick_evaluation.get('evaluation', {})
                    
                    # Level 1: Reaction
                    level_1 = evaluation.get('level_1_reaction', {})
                    previous_conversation += f"**Level 1: Reaction**\n"
                    previous_conversation += f"  - **Engagement:** {level_1.get('engagement', 'N/A')}\n"
                    previous_conversation += f"  - **Relevance:** {level_1.get('relevance', 'N/A')}\n"
                    previous_conversation += f"  - **Favorability:** {level_1.get('favorability', 'N/A')}\n"
                    previous_conversation += f"  - **Comments:** {level_1.get('comments', 'N/A')}\n\n"
                    
                    # Level 2: Learning
                    level_2 = evaluation.get('level_2_learning', {})
                    previous_conversation += f"**Level 2: Learning**\n"
                    previous_conversation += f"  - **Knowledge Acquisition:** {level_2.get('knowledge_acquisition', 'N/A')}\n"
                    previous_conversation += f"  - **Skills Development:** {level_2.get('skills_development', 'N/A')}\n"
                    previous_conversation += f"  - **Attitude Change:** {level_2.get('attitude_change', 'N/A')}\n"
                    previous_conversation += f"  - **Confidence Boost:** {level_2.get('confidence_boost', 'N/A')}\n"
                    previous_conversation += f"  - **Commitment Level:** {level_2.get('commitment_level', 'N/A')}\n"
                    previous_conversation += f"  - **Comments:** {level_2.get('comments', 'N/A')}\n\n"
                    
                    # Level 3: Behavior
                    level_3 = evaluation.get('level_3_behavior', {})
                    previous_conversation += f"**Level 3: Behavior**\n"
                    previous_conversation += f"  - **Behavior Change:** {level_3.get('behavior_change', 'N/A')}\n"
                    previous_conversation += f"  - **Application of Learning:** {level_3.get('application_of_learning', 'N/A')}\n"
                    previous_conversation += f"  - **Comments:** {level_3.get('comments', 'N/A')}\n\n"
                    
                    # Level 4: Results
                    level_4 = evaluation.get('level_4_results', {})
                    previous_conversation += f"**Level 4: Results**\n"
                    previous_conversation += f"  - **Business Outcome:** {level_4.get('business_outcome', 'N/A')}\n"
                    previous_conversation += f"  - **KPI Impact:** {level_4.get('kpi_impact', 'N/A')}\n"
                    previous_conversation += f"  - **Comments:** {level_4.get('comments', 'N/A')}\n\n"
            else:
                previous_conversation = "No previous conversation found."
            self.redis_manager.add_previous_session_data(key=self.call_id, data=previous_conversation)
        return previous_conversation   
     
    @tracer.wrap(name="dd_trace.format_user_data",service="degreed-coach-builder")
    def format_user_data(self, user_data: dict) -> str:
        """
        Formats the user data into a readable string.

        Args:
            user_data (dict): The user data to format.

        Returns:
            str: The formatted user data.
        """
        formatted_string = ""

        def add_to_string(key, label, is_dict=False, is_list=False, sub_keys=None):
            """
            Helper function to add formatted data to the formatted_string.

            Args:
                key (str): The key to look for in the user_data.
                label (str): The label to use in the formatted string.
                is_dict (bool): Whether the value is expected to be a dictionary.
                is_list (bool): Whether the value is expected to be a list.
                sub_keys (list): List of sub-keys to extract if the value is a dictionary.
            """
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

        try:
            add_to_string('name', 'Name')
            add_to_string('age', 'Age')
            # add_to_string('city', 'City')
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
        except Exception as e:
            log_error(log, f"Error formatting user data: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")

        return formatted_string

    @tracer.wrap(name="dd_trace.get_user_data",service="degreed-coach-builder")
    async def get_user_data(self):
        """
        Retrieves and formats user data.

        This method fetches user data, removes unnecessary fields, and formats the remaining data.
        It logs the process and handles any exceptions that may occur.

        Returns:
            str: The formatted user data or an error message if user data is not found or an exception occurs.
        """
        log_info(log, f"Getting user data for {self.user_id}")

        # Check if user data is available
        if not self.user_data:
            return f"User {self.user_id} not found."

        # Fields to be removed from user data
        fields_to_remove = ['inferred_skill', 'knowledge', 'resume_file']
        for field in fields_to_remove:
            self.user_data.pop(field, None)

        try:
            # Format the user data
            formatted_data = self.format_user_data(self.user_data)
        except Exception as e:
            # Log the error with traceback
            log_error(log, f"Error getting user data: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            formatted_data = "Error retrieving user data."

        return formatted_data

    @tracer.wrap(name="dd_trace.get_user_action_items",service="degreed-coach-builder")
    async def get_user_action_items(self):
        """
        Retrieves and formats user action items.

        This method fetches user action items from Redis. If not found, it retrieves the data from previous conversations,
        groups the tasks by their status, and formats them into a string. The formatted string is then cached in Redis.

        Returns:
            str: The formatted user action items or an error message if an exception occurs.
        """
        log_info(log, "Getting user action items")

        # Fetch task items from Redis
        tasks_items = self.redis_manager.get_task_item_data(self.call_id)
        log_info(log, f"Tasks items from Redis: {tasks_items}")

        if not tasks_items:
            try:
                # Fetch previous conversation info
                previous_conversation_info = await get_previous_conversation(
                    sid=self.call_id, 
                    conversation_id=self.conversation_id, 
                    coach_id=self.coach_id
                )

                # Check if tasks are present in the previous conversation info
                if previous_conversation_info["tasks"]:
                    grouped_items = defaultdict(list)
                    for item in previous_conversation_info["tasks"]:
                        status = item.get("taskStatus", "Unknown")
                        grouped_items[status].append(item)
                    
                    tasks_items = ""
                    for status, items in grouped_items.items():
                        status = status or "Unknown"  # Ensure status is not None
                        tasks_items += f"\nStatus: {status}\n"
                        tasks_items += "-" * (len(status) + 8) + "\n"
                        for item in items[:5]:  # Get only the recent 5 items
                            tasks_items += f"Activity: {item.get('taskName', 'N/A')}\n"
                            tasks_items += f"Description: {item.get('taskDescription', 'N/A')}\n"
                            tasks_items += f"Started at: {item.get('startTime', 'N/A')} - Ends at: {item.get('endTime', 'N/A')}\n"
                            tasks_items += "\n"
                else:
                    # Extract task items from the previous conversation info
                    task_items = self.extract_conversation(previous_conversation_info, "TaskItems")

                    grouped_items = defaultdict(list)
                    for item in task_items:
                        status = item.get("ActivityStatus", "Unknown")
                        grouped_items[status].append(item)
                    
                    tasks_items = ""
                    for status, items in grouped_items.items():
                        status = status or "Unknown"
                        tasks_items += f"\nStatus: {status}\n"
                        tasks_items += "-" * (len(status) + 8) + "\n"
                        for item in items[:5]:  # Get only the recent 5 items
                            tasks_items += f"Activity: {item.get('Activity', 'N/A')}\n"
                            tasks_items += f"Description: {item.get('ActivityDescription', 'N/A')}\n"
                            tasks_items += f"Started at: {item.get('StartTime', 'N/A')} - Ends at: {item.get('EndTime', 'N/A')}\n"
                            tasks_items += "\n"

                # Cache the formatted task items in Redis
                self.redis_manager.add_task_item_data(self.call_id, tasks_items)
                log_info(log, f"Tasks items: {tasks_items}")
            except Exception as e:
                # Log the error with traceback
                log_error(log, f"Error getting user action items: {e}")
                log_debug(log, f"Traceback: {traceback.format_exc()}")
                tasks_items = "Error retrieving user action items."
        
        return tasks_items.strip()
    
    @tracer.wrap(name="dd_trace.get_formatted_todays_plan",service="degreed-coach-builder")
    async def get_formatted_todays_plan(self):
        """
        Retrieves and formats today's plan for the user.

        This method fetches the plan data from Redis. If the data is not available in Redis,
        it retrieves the previous conversation information, processes it to find today's plan,
        and then caches the formatted plan data in Redis.

        Returns:
            str: A formatted string of today's plan.
        """
        plans = self.redis_manager.get_plan_data(self.call_id)
        log_info(log, f"Plans from Redis: {plans}")

        if not plans:
            try:
                # Fetch previous conversation information
                previous_conversation_info = await get_previous_conversation(
                    sid=self.call_id, 
                    conversation_id=self.conversation_id, 
                    coach_id=self.coach_id
                )

                plans = ""
                if previous_conversation_info.get("plans"):
                    for plan in previous_conversation_info["plans"]:
                        plan_title = plan.get("planName", False)
                        plan_description = plan.get("planDescription", False)
                        today_plan = None

                        if plan.get("taskItems"):
                            today = datetime.now(self.timezone)
                            closest_task = min(
                                plan["taskItems"], 
                                key=lambda task: abs(datetime.fromisoformat(task["startTime"]) - today)
                            )
                            today_plan = closest_task if closest_task else None

                        if plan_title and plan_description and today_plan:
                            plans += (
                                f"Plan Title: {plan_title}\n"
                                f"Plan Description: {plan_description}\n"
                                f"Today's Plan: {today_plan}\n"
                            )

                # Cache the formatted plan data in Redis
                self.redis_manager.add_plan_data(self.call_id, plans)
                log_info(log, f"Plans: {plans}")

            except Exception as e:
                # Log the error with traceback
                log_error(log, f"Error getting today's plan: {e}")
                log_debug(log, f"Traceback: {traceback.format_exc()}")
                plans = "Error retrieving today's plan."

        return plans.strip()

    @tracer.wrap(name="dd_trace.get_knowledge",service="degreed-coach-builder")
    async def get_knowledge(self, user_data, prompt):
        """
        Retrieves knowledge based on the user's data and the last prompt message.

        Args:
            user_data (dict): The user data containing knowledge information.
            prompt (list): The list of messages in the prompt, where the last message is used as the query.

        Returns:
            list: The knowledge data retrieved based on the user's query.
        """
        knowledge = []
        try:
            # Check if prompt is not empty and user_data contains knowledge for the current coach
            if prompt and "knowledge" in user_data and self.coach_id in user_data["knowledge"]:
                user_query = prompt[-1]['content']  # Extract the last message content as the user query
                collection_names = [
                    knowledge_data["collection_name"]
                    for knowledge_data in user_data[self.user_id]["knowledge"][self.coach_id]
                ]
                # Retrieve knowledge from the Chroma manager
                knowledge = await self.chroma_manager.get_knowledge(
                    collection_names=collection_names,
                    query=user_query
                )
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error getting knowledge: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
        return knowledge
    
    @tracer.wrap(name="dd_trace.get_degreed_knowledge",service="degreed-coach-builder")
    async def get_degreed_knowledge(self, user_data, prompt, k=3):
        """
        Retrieves degreed knowledge based on the user's data and the last prompt message.

        Args:
            user_data (dict): The user data containing knowledge information.
            prompt (list): The list of messages in the prompt, where the last message is used as the query.
            k (int, optional): The number of related documents to retrieve. Defaults to 3.

        Returns:
            list: The degreed knowledge data retrieved based on the user's query.
        """
        try:
            # Extract the last message content as the user query
            user_query = prompt[-1]['content']
            
            # Retrieve degreed knowledge from the Chroma manager
            degreed_knowledge = await self.chroma_manager.degreed_search_knowledge(
                collection_name="degreed-knowledge", 
                query=user_query, 
                k=k,
                related_docs=True
            )
            
            # Format the retrieved knowledge
            knowledge = [
                f"{doc.page_content}\nSource: {doc.metadata['URL']}"
                for doc in degreed_knowledge
            ]
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error getting degreed knowledge: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            knowledge = []
        
        return knowledge

    @tracer.wrap(name="dd_trace.convert_transcript_to_openai_messages",service="degreed-coach-builder")
    async def convert_transcript_to_openai_messages(self, transcript: List[Utterance]) -> List[Dict[str, str]]:
        """
        Converts a transcript of utterances to a list of messages formatted for OpenAI.

        Args:
            transcript (List[Utterance]): The list of utterances to convert.

        Returns:
            List[Dict[str, str]]: A list of messages formatted for OpenAI.
        """
        log_info(log, "Converting transcript to OpenAI messages")
        messages = []
        try:
            for utterance in transcript:
                # Determine the role and format the message accordingly
                if utterance.role == "agent":
                    messages.append({"role": "assistant", "content": utterance.content})
                else:
                    messages.append({"role": "user", "content": utterance.content})

            # Asynchronously save the messages
            asyncio.create_task(save_message(
                sid=self.call_id, 
                original_message=messages, 
                type="User", 
                conversation_id=self.conversation_id,
                coach_id=self.coach_id
            ))
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error converting transcript to OpenAI messages: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")

        return messages
    
    @tracer.wrap(name="dd_trace.prepare_prompt",service="degreed-coach-builder")
    async def prepare_prompt(self, request: ResponseRequiredRequest = None, prompt_type: str = "voice"):
        """
        Prepares the prompt for the AI model based on user data, previous conversations, and action items.

        Args:
            request (ResponseRequiredRequest, optional): The request object containing the transcript and interaction type.
            prompt_type (str): The type of prompt to prepare ("voice" or "text"). Defaults to "voice".

        Returns:
            list: A list of messages formatted for the AI model.
        """
        log_info(log, "Preparing prompt")
        try:
            # Gather all necessary data concurrently
            tasks = await asyncio.gather(
                self.get_previous_conversation(),
                self.get_user_data(),
                self.get_user_action_items(),
                self.get_formatted_todays_plan(),
                self.get_pathway_info()
            )
            previous_conversation, user_detail, action_items, formatted_todays_plan, pathway = tasks

            # Determine the header based on the prompt type
            header = VOICE_HEADER.format(coach_name=self.coach_data["coachName"]) if prompt_type == "voice" else TEXT_HEADER.format(coach_name=self.coach_data["coachName"])
            prompt = []

            if request:
                # Convert transcript to OpenAI messages
                transcript_messages = await self.convert_transcript_to_openai_messages(request.transcript)
                prompt.extend(transcript_messages)

                # Add reminder message if interaction type is reminder_required
                if request.interaction_type == "reminder_required":
                    prompt.append(
                        {
                            "role": "user",
                            "content": "(Now the user has not responded in a while, you would say:)",
                        }
                    )
            else:
                # Retrieve conversation chat from Redis
                conversation_chat = self.redis_manager.retrieve_chat(self.conversation_id)
                if conversation_chat:
                    for message in conversation_chat:
                        if message["role"] == "function":
                            prompt.append({"role": message["role"], "name": message["name"], "content": message["content"]})
                        else:
                            prompt.append({"role": message["role"], "content": message["content"]})

            # Retrieve knowledge based on user data and current prompt
            knowledge = await self.get_knowledge(self.user_data, prompt)

            # Determine instructions based on coach type
            if self.coach_data["coachName"] == "Career Development Coach":
                instructions = self.coach_data['instructions'].format(user_skill=self.user_data.get('inferred_skill', {}))
            elif self.coach_data["coachSubType"] in ["Skills"]:
                header = ""
                call_data = self.redis_manager.retrieve_call_id_data(self.call_id)
                skill_name = call_data.get('skill', {}).get('name')
                skill_level = call_data.get('skill', {}).get('level')
                instructions = self.coach_data['instructions'].format(skill_name=skill_name, level=skill_level)
            else:
                instructions = self.coach_data['instructions']

            # Append degreed knowledge if applicable
            if self.coach_id == "Degreed_Readiness" and prompt:
                degreed_knowledge = await self.get_degreed_knowledge(self.user_data, prompt)
                knowledge = degreed_knowledge + knowledge

            # Select the appropriate template based on prompt type
            template = VOICE_PROMPT_TEMPLATE if prompt_type == "voice" else TEXT_PROMPT_TEMPLATE

            # Create the system prompt
            system_prompt = [
                {
                    "role": "system",
                    "content": template.format(
                        user_details=user_detail,
                        previous_conversation=previous_conversation,
                        current_time=(datetime.now(self.timezone) + timedelta(days=self.plus_day, hours=self.plus_hours)).isoformat(),
                        current_day=(datetime.now(self.timezone) + timedelta(days=self.plus_day, hours=self.plus_hours)).strftime("%A"),
                        coach_name=self.coach_data["coachName"],
                        instructions=instructions,
                        knowledge=KNOWLEDGE_BASE_PROMPT_TEMPLATE.format(knowledge=knowledge) if knowledge else "",
                        persona=", ".join(self.coach_data['persona']),
                        action_items=ACTION_ITEMS_PROMPT_TEMPLATE.format(action_items=action_items) if action_items else "",
                        plan=PLAN_PROMPT_TEMPLATE.format(plan=formatted_todays_plan) if formatted_todays_plan else "",
                        domain=self.coach_data.get('domains', self.coach_data["coachName"]),
                        pathway=PATHWAY_PROMPT_TEMPLATE.format(pathway=pathway) if pathway else "",
                        key_points=KEY_POINTS_PROMPT_TEMPLATE,
                        end_note=END_NOTE_PROMPT_TEMPLATE,
                        header=header
                    )
                }
            ]

            # Combine system prompt with user messages
            prompt = system_prompt + prompt

            # Format the final prompt
            self.format_prompt(prompt)
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error preparing prompt: {e}\n{traceback.format_exc()}")
            prompt = [{"role": "system", "content": "Error preparing prompt."}]

        return prompt
    
    @tracer.wrap(name="dd_trace.get_pathway_info",service="degreed-coach-builder")
    async def get_pathway_info(self):
        """
        Retrieves pathway information for the current call session.

        This method fetches pathway data from Redis cache or extracts it from previous conversation data.
        It formats the pathway information into a readable string format.

        Returns:
            str: Formatted pathway information if available, otherwise None.
        """
        try:
            # Check if the coach is related to Skill Review, if so, return None
            if self.coach_data["coachSubType"] in ["Skills"]:
                return None

            # Retrieve pathway data from Redis cache
            pathway = self.redis_manager.retrieve_call_id_data(self.call_id).get('pathway', None)

            # If pathway data is not found in Redis, extract it from previous conversation
            if not pathway:
                previous_conversation_info = await get_previous_conversation(
                    sid=self.call_id, 
                    conversation_id=self.conversation_id, 
                    coach_id=self.coach_id
                )
                pathway = self.extract_conversation(previous_conversation_info, "PathwayData")

            # If pathway data is available, format it into a readable string
            if pathway:
                pathway_info = f"Pathway Title: {pathway['title']}\nDescription: {pathway['description']}\n"
                for level in pathway.get('levels', []):
                    pathway_info += f"### Section Title: {level['title']}:\n"
                    pathway_info += f"Description: {level['description']}\n"
                    if level.get('steps', []):
                        pathway_info += f"#### Contents:\n"
                        for step in level.get('steps', []):
                            pathway_info += f" - Content Title: {step['title']}:\n"
                            pathway_info += f" - Summary: {step['description']}\n"
                            pathway_info += f" - Status: {'Completed' if step['isCompleted'] else 'Not Completed'}\n"
                pathway_info += f"Tags: {', '.join(pathway['tags'])}\n"
                log_info(log, f"Pathway info: {pathway_info}")
                return pathway_info
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error getting pathway info: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
        return None

    
    @tracer.wrap(name="dd_trace.format_prompt",service="degreed-coach-builder")
    def format_prompt(self, prompt):
        """
        Formats the given prompt with color codes based on the role of the message.

        This method takes a list of messages and formats each message with a specific color
        based on the role (user, assistant, function, system). It also logs the formatted prompt
        if prompt logging is enabled via environment variable.

        Args:
            prompt (list): A list of dictionaries where each dictionary represents a message with 'role' and 'content' keys.

        Returns:
            str: The formatted prompt with color codes.
        """
        role_colors = {
            "user": "\033[94m",  # Blue
            "assistant": "\033[92m",  # Green
            "function": "\033[93m",  # Yellow
            "system": "\033[95m"  # Magenta
        }
        reset_color = "\033[0m"
        formatted_prompt = ""
        
        try:
            for message in prompt:
                role = message['role']
                color = role_colors.get(role, "\033[0m")
                if role == 'function':
                    formatted_prompt += f'{color}{role.capitalize()} (Function: {message.get("name", "")}): {message["content"]}{reset_color}\n'
                else:
                    formatted_prompt += f'{color}{role.capitalize()}: {message["content"]}{reset_color}\n'
            
            # Log the formatted prompt if logging is enabled
            if os.getenv("ENABLE_PROMPT_LOGGING", "True") == "True":
                print(f"\033[92m{formatted_prompt}\033[0m")
        
        except Exception as e:
            # Log the error with traceback for debugging purposes
            log_error(log, f"Error formatting prompt: {e}")
            log_debug(log, f"Traceback: {traceback.format_exc()}")
            formatted_prompt = "Error formatting prompt."
        
        return formatted_prompt
