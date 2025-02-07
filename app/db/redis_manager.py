
import json
from datetime import datetime
import pytz

from app.db.redis_client import get_redis_client
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn

log = get_logger(__name__)


class RedisManager:
    redis_client = None

    def __init__(self):
        if not self.redis_client:
            self.redis_client = get_redis_client()

    def add_object(self, key, data, expiry=12 * 60 * 60):
        # Add object to Redis Cluster
        self.redis_client.set(key, data)
        # Set expiry if provided
        if expiry:
            self.redis_client.expire(name=key, time=expiry)

    def get_object(self, key):
        # Retrieve object from Redis Cluster
        return self.redis_client.get(name=key)

    def add_coach_data(self, key, data, expiry=12 * 60 * 60):
        # Add object to Redis Cluster
        self.redis_client.set(f"coach{key}", json.dumps(data))
        # Set expiry if provided
        if expiry:
            self.redis_client.expire(name=f"coach{key}", time=expiry)

    def get_coach_data(self, key):
        # Retrieve object from Redis Cluster
        coach_data = self.redis_client.get(name=f"coach{key}")
        if coach_data:
            return json.loads(coach_data)
    
    def add_user_data(self, key, data, expiry=10 * 60):
        # Add object to Redis Cluster
        self.redis_client.set(f"user{key}", json.dumps(data))
        # Set expiry if provided
        if expiry:
            self.redis_client.expire(name=f"user{key}", time=expiry)

    def get_user_data(self, key):
        # Retrieve object from Redis Cluster
        user_data = self.redis_client.get(name=f"user{key}")
        if user_data:
            return json.loads(user_data)
        
    def add_previous_session_data(self, key, data, expiry=12 * 60 * 60):
        # Add object to Redis Cluster
        self.redis_client.set(f"previous_session_{key}", json.dumps(data))
        # Set expiry if provided
        if expiry:
            self.redis_client.expire(name=f"previous_session_{key}", time=expiry)

    def get_previous_session_data(self, key):
        # Retrieve object from Redis Cluster
        session_data = self.redis_client.get(name=f"previous_session_{key}")
        if session_data:
            return json.loads(session_data)

    def add_task_item_data(self, key, data, expiry=12 * 60 * 60):
        # Add object to Redis Cluster
        self.redis_client.set(f"task_items_{key}", json.dumps(data))
        # Set expiry if provided
        if expiry:
            self.redis_client.expire(name=f"task_items_{key}", time=expiry)

    def get_task_item_data(self, key):
        # Retrieve object from Redis Cluster
        session_data = self.redis_client.get(name=f"task_items_{key}")
        if session_data:
            return json.loads(session_data)

    def add_plan_data(self, key, data, expiry=12 * 60 * 60):
        # Add object to Redis Cluster
        self.redis_client.set(f"plans_{key}", json.dumps(data))
        # Set expiry if provided
        if expiry:
            self.redis_client.expire(name=f"plans_{key}", time=expiry)

    def get_plan_data(self, key):
        # Retrieve object from Redis Cluster
        session_data = self.redis_client.get(name=f"plans_{key}")
        if session_data:
            return json.loads(session_data)
        
    def store_session_data(self, session_id, session_data):
        session_data_json = json.dumps(session_data)
        self.redis_client.set(f"session_data_{session_id}", session_data_json)
        log_info(log, f"Session data stored successfully for session_id: {session_id}")

    def retrieve_session_data(self, session_id):
        session_data_json = self.redis_client.get(f"session_data_{session_id}")
        if session_data_json:
            session_data = json.loads(session_data_json)
            log_info(log, f"Session data retrieved successfully for session_id: {session_id}")
            return session_data
        else:
            log_warn(log, f"No session data found for session_id: {session_id}")
            return {}
        
    def store_call_id_data(self, call_id, call_data):
        call_data_json = json.dumps(call_data)
        self.redis_client.set(f"call_data_{call_id}", call_data_json)
        log_info(log, f"Call data stored successfully for call_id: {call_id}")

    def retrieve_call_id_data(self, call_id):
        call_data_json = self.redis_client.get(f"call_data_{call_id}")
        if call_data_json:
            call_data = json.loads(call_data_json)
            log_info(log, f"Call data retrieved successfully for call_id: {call_id}")
            return call_data
        else:
            log_warn(log, f"No call data found for call_id: {call_id}")
            return {}

    def retrieve_chat(self, conversation_id):
        chat_data_json = self.redis_client.get(conversation_id)
        if chat_data_json:
            chat_data = json.loads(chat_data_json)
            return chat_data
        else:
            log_warn(log, f"No session data found for call_id: {conversation_id}")
            return {}

    def store_chat(self, messages, conversation_id, expire_time=12 * 60 * 60):

        chat_data = self.retrieve_chat(conversation_id)
        if chat_data:
            chat_data.extend(messages)
        else:
            chat_data = messages

        chat_data_json = json.dumps(chat_data)

        self.redis_client.set(conversation_id, chat_data_json)
        if expire_time:
            self.redis_client.expire(name=conversation_id, time=expire_time)

        log_info(log, f"Chat data stored successfully for call_id: {conversation_id}")

    def store_base_url(self, sid, base_url, expire_time=60 * 60):
        self.redis_client.set(f"base_url_{sid}", base_url)
        log_info(log, f"Base URL stored successfully for session_id: {sid}")
        if expire_time:
            self.redis_client.expire(name=f"base_url_{sid}", time=expire_time)

    def retrieve_base_url(self, sid):
        base_url = self.redis_client.get(f"base_url_{sid}")
        if base_url:
            log_info(log, f"Base URL retrieved successfully for session_id: {sid}")
            return base_url
        else:
            log_warn(log, f"No Base URL found for session_id: {sid}")
            return None

    def store_instructions(self, sid, data, expire_time=60 * 60):
        self.add_object(key = f"realtime_data_{sid}", data=json.dumps(data), expiry=expire_time)
        log_info(log, f"Realtime data stored successfully for session_id: {sid}")

    def retrieve_instructions(self, sid):
        data = self.get_object(key = f"realtime_data_{sid}")
        if data:
            log_info(log, f"Realtime data retrieved successfully for session_id: {sid}")
            return json.loads(data)
        else:
            log_warn(log, f"No Realtime data found for session_id: {sid}")
            return None