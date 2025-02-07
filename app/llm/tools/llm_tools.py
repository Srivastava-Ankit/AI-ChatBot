
import asyncio
from datetime import datetime, timedelta
import json
import os
import re
from typing import Optional

import pytz
import uuid

from app.dg_component.find_content.request_builder import ContentSearchRequest
from app.dg_component.find_content.find_learning_resources import find_learning_resources
# from app.dg_component.login import login
from app.utils.default import get_value_by_path
from app.utils.role_to_skill import RoleToSkill
from app.llm.llm_client import AZURE_ASYNC_CLIENT
from app.db.redis_manager import RedisManager
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
import traceback

log = get_logger(__name__)

class Tools():
    def __init__(self, user_id, coach_id, call_id, conversation_id, time_zone, queue=None):
        self.functions = {
                          "Action_items": {"function": self.action_items, "is_sync": True},
                          "Find_content": {"function": self.find_content, "is_sync": True},
                          "Role_To_Skill": {"function": self.role_to_skill, "is_sync": True},
                          "Prepare_Plan": {"function": self.prepare_plan, "is_sync": False},
                          }
        
        self.coach_id = coach_id
        self.user_id = user_id
        self.conversation_id = conversation_id
        self.timezone = time_zone
        self.redis_manager = RedisManager()
        self.call_id = call_id
        self.queue = queue

    def prepare_tools(self):
        functions = [
            {
                "type": "function",
                "function": {
                    "name": "Action_items",
                    "description": "Use this tool to assign action items or when you want to discuss the progress of the activities.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "Activity": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "Activity": {
                                            "type": "string",
                                            "description": "Activity discussed in the conversation."
                                        },
                                        "ActivityStatus": {
                                            "type": "string",
                                            "enum": ["Planned", "Started", "InProgress", "Done", "Review", "Assessment"],
                                            "description": "Status of the activity discussed."
                                        },
                                        "ActivityDescription": {
                                            "type": "string",
                                            "description": "Description of the activity discussed."
                                        },
                                        "ActivityFeedback": {
                                            "type": "string",
                                            "description": "Feedback on the activity discussed."
                                        },
                                        "ActivityType": {
                                            "type": "string",
                                            "enum": ["Weekly challenge", "Daily challenge", "tasks", "action items"],
                                            "description": "Type of the activity discussed."
                                        },
                                        "TimetoComplete": {
                                            "type": "object",
                                            "properties": {
                                                "days": {
                                                    "type": "integer",
                                                    "description": "Number of days taken to complete the activity."
                                                },
                                                "hours": {
                                                    "type": "integer",
                                                    "description": "Number of hours taken to complete the activity."
                                                },
                                                "minutes": {
                                                    "type": "integer",
                                                    "description": "Number of minutes taken to complete the activity."
                                                }
                                            },
                                            "required": ["days", "hours", "minutes"],
                                            "description": "Time taken to complete the activity discussed. For example if the activity takes 2days, 3hours and 30minutes to complete, then the value should be `days: 2, hours: 3, minutes: 30`."
                                        },
                                        "LearningsFromActivity": {
                                            "type": "string",
                                            "description": "Learnings from the activity discussed."
                                        }
                                    },
                                    "required": ["Activity", "ActivityStatus", "ActivityType", "ActivityDescription", "TimetoComplete"]
                                }
                            },
                            "your_response": {
                                "type": "string",
                                "description": "It should be your response which conveys `that you Assigned the action items successfully` in a natural way."
                            }
                        },
                        "required": ["Activity", "your_response"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "Find_content",
                    "description": "Search for relevant content from the web based on user input.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "search_word": {
                                "type": "string",
                                "description": "Content the user wants to learn."
                            },
                            "mode": {
                                "type": "string",
                                "enum": ["video", "article", "book", "course", "event", "assessment", "episode", "pathway", "target"],
                                "description": "Type of content. If not specified, no need of mode. Don't assume mode on your own."
                            },
                            "duration": {
                                "type": "string",
                                "enum": ["LessThan5", "LessThan10", "LessThan30", "LessThan1Hour", "LessThan4Hours", "LessThan1Day", "GreaterThan1Day"],
                                "description": "Duration of the content. If not specified, no need of duration. Don't assume duration on your own."
                            },
                            "boost_popular": {
                                "type": "boolean",
                                "description": "Boost popular content. If not specified, default is true."
                            },
                            "boost_recent": {
                                "type": "boolean",
                                "description": "Boost recent content. If not specified, default is true."
                            },
                            "your_response": {
                                "type": "string",
                                "description": "It should be your response which conveys `that I recommended you a few contents on the topic you asked` in a natural way and make the conversation going."
                            }
                        },
                        "required": ["search_word", "your_response"]
                    }
                }
            },
            {
            "type": "function",
            "function": {
                "name": "Role_To_Skill",
                "description": "Get the skills required for a specified role. Use this tool only if user wantes to know the skills for a specific role.",
                "parameters": {
                    "type": "object",
                    "properties": {
                    "role_name": {
                        "type": "string",
                        "description": "The name of the role the user wants to know the skills for."
                        }
                    },
                    "required": ["role_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "Prepare_Plan",
                    "description": "This tool is to only prepare an UpSkilling and Career Development Plan for the user. Use this tool only if the user wants to prepare a plan and you know the user's ambitions, goals, preferences, and current skills. Don't use this tool if the user wanted to check whether the plan is prepared or not. If the plan is Prepared you will have a response from function `Prepare_Plan`.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "plan_title": {
                                "type": "string",
                                "description": "Title of the plan."
                            },
                            "plan_description": {
                                "type": "string",
                                "description": "Description of the plan."
                            },
                            "plan_duration": {
                                "type": "integer",
                                "description": "Number of days for the plan."
                            },
                            "skills_to_learn": {
                                "type": "array",
                                "description": "List of skills that the user wants to learn newly.",
                                "items": {
                                    "type": "string"
                                }
                            },
                            "skills_to_upgrade": {
                                "type": "array",
                                "description": "List of skills that the user already has and wants to upgrade.",
                                "items": {
                                    "type": "string"
                                }
                            },
                            "your_response": {
                                "type": "string",
                                "description": "A message that says 'I began preparing a plan for you, I will share the details soon' in a Natural and conversational way and continue the conversation."
                            }
                        },
                        "required": ["plan_title", "plan_description", "plan_duration", "skills_to_learn", "skills_to_upgrade", "your_response"]
                    }
                }
            }
        ]
        return functions
    
    async def action_items(self, Activity, session_id=None, correlation_id=None, callback=True):
        """
        Assign action items based on the provided activities and update the action item data.

        Args:
            Activity (list): List of activities to be assigned as action items.
            session_id (str, optional): Session ID for tracking. Defaults to None.
            correlation_id (str, optional): Correlation ID for tracking. Defaults to None.
            callback (bool, optional): Flag to indicate if a callback is required. Defaults to True.

        Returns:
            tuple: A message indicating the success of the operation and the callback status.
        """
        try:
            # Load existing action item data from the file
            with open(ACTION_ITEM_DATA_PATH, 'r') as file: # TODO:: Get Action data from .Net Layer but as of now llm_tools is not being user
                action_item_data = json.load(file)

            # Create action items from the provided activities
            action_items = [{         
                "activity_id": str(uuid.uuid4()),
                "activity": activity.get("Activity", "N/A"),
                "activity_status": activity.get("ActivityStatus", "N/A"),
                "activity_description": activity.get("ActivityDescription", "N/A"),
                "activity_type": activity.get("ActivityType", "N/A"),
                "learnings_from_activity": activity.get("LearningsFromActivity", "N/A"),
                "activity_feedback": activity.get("ActivityFeedback", "N/A"),
                "time_to_complete": activity.get("TimetoComplete", "N/A"),
                "time_stamp": datetime.now(self.timezone).isoformat()
            } for activity in Activity]

            data = {"action_items": action_items}

            # Update the action item data with the new action items
            if self.user_id in action_item_data:
                if self.coach_id in action_item_data[self.user_id]:
                    action_item_data[self.user_id][self.coach_id].extend(action_items)
                else:
                    action_item_data[self.user_id][self.coach_id] = action_items
            else:
                action_item_data[self.user_id] = {self.coach_id: action_items}

            # Save the updated action item data back to the file
            with open(ACTION_ITEM_DATA_PATH, 'w') as file:  # TODO:: Get Action data from .Net Layer but as of now llm_tools is not being user
                json.dump(action_item_data, file, indent=4)
            
            return_data = {
                "coach_id": self.coach_id,
                "data": data,
                "answer": "",
                "user_id": self.user_id,
                "status": "done" if not callback else "in-progress",
                "is_final": True if not callback else False,
                "time_stamp": datetime.now(self.timezone).isoformat(),
                "session_id": session_id,
                "correlation_id": correlation_id
            }
            
            # If a queue is available, put the return data into the queue
            if self.queue:
                await self.queue.put(json.dumps(return_data))

            return f"Action Item Successfully Assigned \nHere are the details: {action_items}", callback
        except Exception as e:
            # Log the error and traceback
            log_error(log, f"Error assigning action items: {e}")
            log_debug(log, traceback.format_exc())
            raise

    async def find_content(self, search_word: str, mode: Optional[str] = None, duration: Optional[str] = None, boost_popular: bool = False, boost_recent: bool = False, session_id: Optional[str] = None, correlation_id: Optional[str] = None, callback: bool = True) -> tuple:
        """
        Find content based on the search word and various filters.

        Args:
            search_word (str): The word to search for.
            mode (Optional[str]): The mode/type of content to filter by.
            duration (Optional[str]): The duration to filter by.
            boost_popular (bool): Whether to boost popular content.
            boost_recent (bool): Whether to boost recent content.
            session_id (Optional[str]): The session ID.
            correlation_id (Optional[str]): The correlation ID.
            callback (bool): Whether to use a callback.

        Returns:
            tuple: A formatted string of the found content and the callback status.
        """
        try:
            with open(RECOMMENDATION_DATA_PATH, 'r') as file:  # TODO:: Get Recommendation data from .Net Layer but as of now llm_tools is not being user
                find_content_data = json.load(file)

            user = {
                "credentials": {
                    "key1": {
                        "email": "user@example.com",
                        "username": "degassistant28325",
                        "password": "F8?oi3dsfiBz"
                    }
                }
            }

            filter_dict = {}
            if duration:
                filter_dict["Duration"] = duration
            if mode:
                filter_dict["type"] = mode

            # Build the search request
            search_request = ContentSearchRequest.Builder().set_terms(search_word).set_filters(filter_dict).set_count(3).set_boost_popular(boost_popular).set_boost_recent(boost_recent).build()

            # Find learning resources
            response = await find_learning_resources(self.call_id, search_request)

            if response.status_code == 200:
                data = response.json()
                extracted_data = []
                for item in data.get('results') or data.get('Results', []):
                    # Convert all keys to lowercase recursively for the item dictionary
                    item = {k.lower(): v for k, v in item.items()}
                    if 'reference' in item:
                        item['reference'] = {k.lower(): v for k, v in item['reference'].items()}

                    # Process and append data to extracted_data
                    date_created = get_value_by_path(item, ['reference', 'datecreated'])
                    year = date_created.split('-')[0]

                    if get_value_by_path(item, ['referencetype']) == "Target":
                        public_url = get_value_by_path(item, ['reference', 'internalurl'])
                    else:
                        public_url = get_value_by_path(item, ['reference', 'publicurl'])
                    if public_url:
                        public_url = os.getenv("BASE_URL", "https://staging.degreed.com") + public_url
                    else:
                        public_url = get_value_by_path(item, ['reference', 'url'])

                    extracted_item = {
                        'referencetype': get_value_by_path(item, ['referencetype']),
                        'referenceid': get_value_by_path(item, ['referenceid']),
                        'title': get_value_by_path(item, ['reference', 'title']),
                        'summary': get_value_by_path(item, ['reference', 'summary']),
                        'url': public_url,
                        'imageurl': get_value_by_path(item, ['reference', 'imageurl']),
                        'isendorsed': get_value_by_path(item, ['reference', 'isendorsed']),
                        'datecreated': year,
                        'durationminutes': get_value_by_path(item, ['reference', 'durationdisplay']),
                        'providername': get_value_by_path(item, ['reference', 'providername']),
                        'resourceid': get_value_by_path(item, ['reference', 'resourceid']),
                        'resourcetype': get_value_by_path(item, ['reference', 'resourcetype']),
                        'recommendation_status': "Planned",
                        'time_stamp': datetime.now(self.timezone).isoformat()
                    }

                    extracted_data.append(extracted_item)
            else:
                log_error(log, "Error fetching data")
                return "Error fetching data", callback

            reference_ids = [item["referenceId"] for item in find_content_data.get(self.user_id, {}).get(self.coach_id, [])]

            if self.user_id in find_content_data:
                if self.coach_id in find_content_data[self.user_id]:
                    for item in extracted_data:
                        if item["referenceId"] not in reference_ids:
                            find_content_data[self.user_id][self.coach_id].append(item)
                        else:
                            for content_item in find_content_data[self.user_id][self.coach_id]:
                                if content_item["referenceId"] == item["referenceId"]:
                                    content_item["time_stamp"] = datetime.now(self.timezone).isoformat()
                else:
                    find_content_data[self.user_id][self.coach_id] = extracted_data
            else:
                find_content_data[self.user_id] = {self.coach_id: extracted_data}

            with open(RECOMMENDATION_DATA_PATH, 'w') as file:  # TODO:: Get Recommendation data from .Net Layer but as of now llm_tools is not being user
                json.dump(find_content_data, file, indent=4)

            return_data = {
                "coach_id": self.coach_id,
                "data": {"find_content": extracted_data},
                "answer": "",
                "user_id": self.user_id,
                "status": "done" if not callback else "in-progress",
                "is_final": True if not callback else False,
                "time_stamp": datetime.now(self.timezone).isoformat(),
                "session_id": session_id,
                "correlation_id": correlation_id
            }
            if self.queue:
                await self.queue.put(json.dumps(return_data))

            formatted_string = ""
            for item in extracted_data:
                formatted_string += f"Title: {item['title']}\nSummary: {item['summary']}\nProvider: {item['providerName']}\nDate Created: {item['dateCreated']}\n\n"

            return formatted_string, callback
        except Exception as e:
            # Log the error and traceback
            log_error(log, f"Error finding content: {e}")
            log_debug(log, traceback.format_exc())
            raise
    
    async def role_to_skill(self, role_name: str, session_id: Optional[str] = None, correlation_id: Optional[str] = None, callback: bool = True) -> tuple:
        """
        Retrieve and format the skills required for a given role.

        Args:
            role_name (str): The name of the role to retrieve skills for.
            session_id (Optional[str]): The session ID for tracking purposes. Defaults to None.
            correlation_id (Optional[str]): The correlation ID for tracking purposes. Defaults to None.
            callback (bool): Flag to indicate if a callback is required. Defaults to True.

        Returns:
            tuple: A tuple containing the formatted string of skills and the callback flag.
        """
        try:
            role_to_skill = RoleToSkill()
            skills = role_to_skill.get_skills(role_name)
            formatted_string = f"Here are complete details of the skills required for the role {skills}:\n"
            return formatted_string, callback
        except Exception as e:
            # Log the error and traceback
            log_error(log, f"Error retrieving skills for role '{role_name}': {e}")
            log_debug(log, traceback.format_exc())
            raise
    
    async def plan_duplicate_checker(self, plan_title, plan_description, plan_duration, skills_to_learn, skills_to_upgrade):
        log_info(log, f"Checking for duplicate plans for user {self.user_id} with coach {self.coach_id}")
        with open(PLAN_DATA_JSON, 'r') as file:
            plan_data = json.load(file)
        plans = []
        if self.user_id in plan_data:
            if self.coach_id in plan_data[self.user_id]:
                for plan in plan_data[self.user_id][self.coach_id]:
                    plans.append(plan)

        duplicate_plans = []

        output_json = """```json
{
    "is_duplicate": true/false
    "reason": "Reason for the duplicate or not duplicate"
}
```"""
        for plan in plans:
            log_info(log, f"Checking for duplicate plan with plan id {plan['plan_id']} plan title {plan['plan_title']}")
            sys_prompt = [
                {
                    "role": "system",
                    "content": """
You are a Helpfull Assistant, your Task is to check whether the such kind of plan is already created or not. You will be given 'Plan Title', 'Plan Description', 'Plan Duration', 'Skills to Learn' and 'Skills to Upgrade' as inputs. with these input You need to check whether the Existing plan is similar or cover the same requirments or not. 

Inputs:
- Plan Title: {plan_title}
- Plan Description: {plan_description}
- Plan Duration: {plan_duration}
- Skills to Learn: {skills_to_learn}
- Skills to Upgrade: {skills_to_upgrade}

Existing Plans:
{existing_plan}

Your response should be JSON with the following structure:
{output_json}
""".format(
    plan_title=plan_title,
    plan_description=plan_description,
    plan_duration=plan_duration,
    skills_to_learn=skills_to_learn,
    skills_to_upgrade=skills_to_upgrade,
    existing_plan=plan,
    output_json=output_json
)
                }
            ]

            response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                messages=sys_prompt,
                temperature=1
            )
            content = response.choices[0].message.content
            matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
            parsed_content = json.loads(matches[0])

            if parsed_content["is_duplicate"]:
                parsed_content["plan_id"] = plan["plan_id"]
                parsed_content["plan_title"] = plan["plan_title"]
                duplicate_plans.append(parsed_content)

        return duplicate_plans

        
    async def prepare_plan(self, plan_title, plan_description, plan_duration, skills_to_learn, skills_to_upgrade, session_id=None, correlation_id=None, callback=True, websocket=None):
        log_info(log, f"Preparing plan for user {self.user_id} with coach {self.coach_id}")
        callback = False    
        # Initialize variables
        current_date = datetime.now(self.timezone).isoformat()
        total_days = plan_duration
        chunk_size = 10
        final_plan = {}
        max_retries = 5

        duplicate_plans = await self.plan_duplicate_checker(plan_title, plan_description, plan_duration, skills_to_learn, skills_to_upgrade)

        if duplicate_plans:
            response = f"Plan with the same requirements already exists. Here are the details of the duplicate plans: {duplicate_plans}"
            user_query = {"role": "function", 
                    "name": "Prepare_Plan",
                    "content": response,
                    "timestamp": datetime.now(self.timezone).isoformat()
                    }
            self.redis_manager.store_chat(messages=[user_query], call_id=self.call_id, user_id=self.user_id, coach_id=self.coach_id, time_zone=self.timezone, convo_type="text")
            return response, callback
        
        # Load user data
        with open(USER_DATA_PATH, "r") as f:  # TODO:: Get User data from .Net Layer but as of now llm_tools is not being user
            user_data = json.load(f).get(self.user_id, {})

        profile_data = self.redis_manager.retrieve_chat(self.user_id).get(self.coach_id, {}).get("profile_data", {})

        formatted_user_string = ""

        if user_data:
            formatted_user_string = f"""
            Name: {user_data['name']}
            Role: {user_data['role']}
            Skills: 
            """
            for skill, level in user_data['skills'].items():
                formatted_user_string += f"  - {skill}: {level}\n"

            formatted_user_string += "\nProjects:\n"
            for project in user_data['projects']:
                formatted_user_string += f"  - {project['name']}\n    Description: {project['description']}\n    Technologies: {', '.join(project['technologies'])}\n    Duration: {project['duration']}\n"

        if profile_data:
            formatted_user_string += f"""
            Profile Data:
            - User Personality: {profile_data['UserPersonality']}
            - User Preferences: {profile_data['UserPreferences']}
            - User Goals: {profile_data['UserGoals']}
            - User Behavior: {profile_data['UserBehavior']}
            - User Engagement: {profile_data['UserEngagement']}
            - Preferred Mode of Learning: {profile_data['PrefferedModeOfLearning']}
            """

        output_json = """```json
        {
        "19-07-2024": {
            "Task": "task title",
            "Task Description": "task description",
            "Learning materials": {
            "Keyword": "name of the Topic to search",
            "Mode": ["video", "article", "book"]  // choose based on user preferences
            },
            "Learnings": {
            "Skill": "name of the skill that will be learned or upgraded by doing this task"
            }
        }
        }
        ```
        """

        # Helper function to create prompt for OpenAI
        def create_prompt(plan_duration, current_date, skills_to_learn, skills_to_upgrade, user_data, previous_plan=None):
            prompt_content = f"""
You are an expert coach specializing in creating personalized upskilling plans for individuals. Your task is to create a detailed plan for upskilling a coachee based on the provided inputs. The plan duration, skills to learn, skills to upgrade, user's existing skills and profile, and current date will be given as inputs. Generate a structured JSON response where each day's task includes a task title, task description, learning materials (with keywords and modes), and the skill that will be learned or upgraded by completing the task. Every Friday, include an assessment of the tasks completed during the week. Here are the placeholders for the inputs:

Instructions:
- Don't repeat the same learning material for the same skill.
- Ensure that the learning materials are relevant to the skills to learn and upgrade.
- Make sure as the days progress, the difficulty level of the tasks increases.
- If the Plan period is less make sure to include all the skills to learn and upgrade.
- If the Plan period is more make sure to include all the skills to learn and upgrade and also include some additional skills to learn and upgrade.
- Make sure to include the assessment every Friday.
- Always start the plan from the next day of the current date.
- Give some Ultimate Assesment, Feedback, Recommendation and Interview Tips at the end of the plan.

Inputs:
- Plan duration: {plan_duration}
- Current date: {current_date}
- Skills to learn: {skills_to_learn}
- Skills to upgrade: {skills_to_upgrade}
- User's existing skills and profile: 
{formatted_user_string}
"""

            if previous_plan:
                prompt_content += f"\nPrevious plan:\n```json\n{json.dumps(previous_plan)}\n```"

            prompt_content += f"\nExample response structure for one day:\n{output_json}\nGenerate a similar structured JSON response for the entire plan duration, ensuring that there is an assessment every Friday."
            return [{"role": "system", "content": prompt_content}]

        # Loop to generate plans in chunks of 10 days
        for start_day in range(0, total_days, chunk_size):
            log_info(log, f"Generating plan for days {start_day} to {start_day + chunk_size}")
            chunk_duration = min(chunk_size, total_days - start_day)
            sys_prompt = create_prompt(chunk_duration, current_date, skills_to_learn, skills_to_upgrade, user_data, final_plan)
            
            for attempt in range(max_retries):
                try:
                    response = await AZURE_ASYNC_CLIENT.chat.completions.create(
                        model=os.getenv("AZURE_GPT_4O_DEPLOYMENT_NAME"),
                        messages=sys_prompt,
                        temperature=1
                    )
                    content = response.choices[0].message.content
                    matches = re.findall(r"```json(.*?)```", content, re.DOTALL)
                    parsed_content = json.loads(matches[0])
                    
                    # Merge the new plan chunk into the final plan
                    for key, value in parsed_content.items():
                        final_plan[key] = value

                    # Update the current date for the next chunk
                    current_date = (datetime.fromisoformat(current_date) + timedelta(days=chunk_duration)).isoformat()
                    break  # Exit the retry loop on success

                except Exception as e:
                    if attempt == max_retries - 1:
                        log_error(log, f"Failed to generate plan for days {start_day} to {start_day + chunk_size}")
                        raise e  # Re-raise the exception if the max retries have been reached
                    else:
                        log_warn(log, f"Failed to generate plan for days {start_day} to {start_day + chunk_size}. Retrying...")
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff

        with open(PLAN_DATA_JSON, 'r') as file:
            plan_data = json.load(file)

        final_plan = {
            "plan_id": str(uuid.uuid4()),
            "plan_title": plan_title,
            "plan_description": plan_description,
            "plan_duration": plan_duration,
            "skills_to_learn": skills_to_learn,
            "skills_to_upgrade": skills_to_upgrade,
            "plan": final_plan,
            "time_stamp": datetime.now(self.timezone).isoformat()
        }

        if self.user_id in plan_data:
            if self.coach_id in plan_data[self.user_id]:
                plan_data[self.user_id][self.coach_id].append(final_plan)
            else:
                plan_data[self.user_id][self.coach_id] = [final_plan]
        else:
            plan_data[self.user_id] = {self.coach_id: [final_plan]}

        with open(PLAN_DATA_JSON, 'w') as file:
            json.dump(plan_data, file, indent=4)

        response = "Personlaized Plan was created for you, You can check that under Plan section On the Right side."

        user_query = {"role": "function", 
                    "name": "Prepare_Plan",
                    "content": response,
                    "timestamp": datetime.now(self.timezone).isoformat()
                    }
        self.redis_manager.store_chat(messages=[user_query], call_id=self.call_id, user_id=self.user_id, coach_id=self.coach_id, time_zone=self.timezone, convo_type="text")
        log_info(log, f"Plan generated successfully for user {self.user_id} with coach {self.coach_id}")
        return_data = {
                    "coach_id": self.coach_id,
                    "data": final_plan,
                    "answer": "",
                    # "user_name": self.user_name,
                    "user_id": self.user_id,
                    "status": "done" if not callback else "in-progress",
                    "is_final": True if not callback else False,
                    "time_stamp": datetime.now(self.timezone).isoformat(),
                    "session_id": session_id,
                    "correlation_id": correlation_id
                }
        if self.queue:
            await self.queue.put(json.dumps(return_data))

        return response, callback
