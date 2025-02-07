from enum import Enum
import os
from fastapi import HTTPException
from pydantic import BaseModel, Field, validator
from typing import Any, List, Dict, Literal, Optional, Union

from app.config import ACCEPTED_EVENT_STATUS


class ConnectRequestModel(BaseModel):
    sessionId: Any
    userProfileKey: Any
    coachId: Any
    conversationId: Optional[Any] = None
    timeZone: str = None
    skill: Optional[dict] = None
    pathwayDetails: Optional[dict] = None
    event: str
    prompt: str = None
    correlationId: str = None
    cookies: dict = None
    host: str = None
    pathwayId: Optional[int] = None

    @validator("event")
    def validate_event(cls, v):
        if v not in ACCEPTED_EVENT_STATUS:
            raise HTTPException(status_code=400, detail=f"Invalid event type. Accepted values are {ACCEPTED_EVENT_STATUS}")
        return v
    
class ConversationInfoExtract(BaseModel):
    conversationId: Optional[int] = None
    userProfileKey: Any
    coachId: Any
    coach: Optional[Dict] = None
    conversationSummary: Optional[str] = None
    startedAt: str
    endedAt: Optional[str] = None
    isActive: Optional[bool] = None
    messages: List[Dict]
    inferences: Optional[List[Dict]] = []
    host: str
    cookies: Optional[Dict] = None

    @validator("coach", pre=True, always=True)
    def convert_coach_subtype(cls, v):
        if v and "coachSubType" in v:
            if v["coachSubType"] == 1:
                v["coachSubType"] = "Skills"
            elif v["coachSubType"] == 0:
                v["coachSubType"] = "Career"
        return v

class AgentModel(BaseModel):
    """
    Pydantic model for Agent parameters.
    """
    llm_websocket_url: Optional[str] = os.getenv("LLM_WEBSOCKET_URL")
    voice_id: str
    agent_name: Optional[str] = None
    ambient_sound: Optional[Literal["coffee-shop", "convention-hall", "summer-outdoor", "mountain-outdoor", "static-noise"]] = None
    backchannel_frequency: Optional[float] = None
    backchannel_words: Optional[List[str]] = None
    boosted_keywords: Optional[List[str]] = None
    enable_backchannel: Optional[bool] = None
    interruption_sensitivity: Optional[float] = None
    language: Optional[Literal["en-US", "en-IN", "en-GB", "de-DE", "es-ES", "es-419", "hi-IN", "ja-JP", "pt-PT", "pt-BR", "fr-FR"]] = None
    opt_out_sensitive_data_storage: Optional[bool] = None
    reminder_max_count: Optional[int] = None
    reminder_trigger_ms: Optional[float] = None
    responsiveness: Optional[float] = None
    voice_speed: Optional[float] = None
    voice_temperature: Optional[float] = None
    webhook_url: Optional[str] = os.getenv("WEBHOOK_URL")
    extra_headers: Optional[dict] = None
    extra_query: Optional[dict] = None
    extra_body: Optional[dict] = None
    timeout: Optional[Union[float, dict]] = None

    @validator("enable_backchannel", pre=True, always=True)
    def set_default_backchannel_words(cls, v, values):
        if v and not values.get('backchannel_words'):
            values['backchannel_words'] = ["okay", "uh-huh", "mhmm", "yah"]
        return v

    class Config:
        """
        Configuration for the Pydantic model.
        """
        use_enum_values = True

class SessionScenario(BaseModel):
    session_number: int = Field(..., description="The session number")
    session_goal: str = Field(..., description="The goal of the session")
    scenario: str = Field(..., description="The scenario for the session")

class TestData(BaseModel):
    username: str = Field(..., description="The username of the test user")
    password: str = Field(..., description="The password of the test user")
    number_of_sessions: int = Field(..., description="The number of sessions")
    goal: str = Field(..., description="The overall goal of the test")
    session_scenario: List[SessionScenario] = Field(..., description="List of session scenarios")
    coach_id: int = Field(..., description="The ID of the coach")

class ListTestData(BaseModel):
    test_data: List[TestData] = Field(..., description="List of test data")