from __future__ import annotations
import asyncio
from datetime import datetime
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Dict
from app.db.redis_manager import RedisManager
import time

from livekit import agents, rtc
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    WorkerType,
    cli,
    llm,
)

from livekit.agents.llm import (
    ChatContext,
    ChatImage,
    ChatMessage,
)

from livekit.agents.multimodal import MultimodalAgent
from livekit.plugins import openai
from livekit.agents.voice_assistant import AssistantCallContext, VoiceAssistant
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.dg_component.login import login_
from app.utils.api_utils import save_message
from ddtrace import tracer, patch_all
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.logger import get_logger
#from ddtrace.internal.sampler import RateSampler
#from ddtrace.sampler import RateSampler

#def silent_initialize(self, *args, **kwargs):
    # Override to remove any initialization logging
#    pass

#RateSampler.__init__ = silent_initialize

patch_all()

logging.getLogger("ddtrace.internal.writer").setLevel(logging.ERROR)
logging.getLogger("ddtrace.internal.rate_limiter").setLevel(logging.ERROR)
logging.getLogger("ddtrace.sampler").setLevel(logging.ERROR)
logging.getLogger("ddtrace.internal.telemetry.writer").setLevel(logging.ERROR)
logging.getLogger("ddtrace._trace.dogstatsd").setLevel(logging.ERROR)
logging.getLogger("ddtrace._trace.processor").setLevel(logging.ERROR)

#telemetry_writer.disable()

log = get_logger(__name__)
redis_manager = RedisManager()

# Ensure required environment variables are set
required_env_vars = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY"]
for var in required_env_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"Required environment variable {var} is not set.")

@dataclass
class SessionConfig:
    session_id: int
    conversation_id: int
    instructions: str
    coach_id: int
    voice: openai.realtime.api_proto.Voice
    temperature: float 
    max_response_output_tokens: str | int
    modalities: list[openai.realtime.api_proto.Modality]
    turn_detection: openai.realtime.ServerVadOptions

    def __post_init__(self):
        if self.modalities is None:
            self.modalities = self._modalities_from_string("text_and_audio")

    def to_dict(self):
        return {k: v for k, v in asdict(self).items() if k != "openai_api_key"}

    @staticmethod
    def _modalities_from_string(modalities: str) -> list[str]:
        modalities_map = {
            "text_and_audio": ["text", "audio"],
            "text_only": ["text"],
        }
        return modalities_map.get(modalities, ["text", "audio"])

@tracer.wrap(name="dd_trace.parse_session_config",service="degreed-coach-realtime")
async def parse_session_config(data: Dict[str, Any]) -> SessionConfig:
    """
    Parse session configuration from provided data.

    Args:
        data (Dict[str, Any]): The data containing session configuration.

    Returns:
        SessionConfig: The parsed session configuration.
    """
    try:
        turn_detection = None
        
        if data.get("turn_detection"):
            turn_detection_json = json.loads(data.get("turn_detection"))
            turn_detection = openai.realtime.ServerVadOptions(
                threshold=0.8,
                prefix_padding_ms=500,
                silence_duration_ms=700,
            )
        else:
            turn_detection = openai.realtime.DEFAULT_SERVER_VAD_OPTIONS

        instructions_data = redis_manager.retrieve_instructions(data.get("session_id"))

        config = SessionConfig(
            session_id = data.get("session_id"),
            instructions=instructions_data["instructions"],
            conversation_id=instructions_data["conversation_id"],
            coach_id=instructions_data["coach_id"], 
            voice=data.get("voice", "alloy"),
            temperature=float(data.get("temperature", 0.8)),
            max_response_output_tokens=data.get("max_output_tokens") if data.get("max_output_tokens") == 'inf' else int(data.get("max_output_tokens") or 2048),
            modalities=SessionConfig._modalities_from_string(
                data.get("modalities", "text_and_audio")
            ),
            turn_detection=turn_detection,
        )
        return config
    except Exception as e:
        log_error(log, f"Error parsing session config: {e}")
        raise

class AssistantFunction(agents.llm.FunctionContext):
    """This class is used to define functions that will be called by the assistant."""

    @agents.llm.ai_callable(
        description=(
            "Called when you need to know the Great leader name"
        )
    )
    async def great_leader(
        self,
        level: Annotated[
            int,
            agents.llm.TypeInfo(
                description="Which level of the Great Leader to return",
            ),
        ],
    ):
        """
        Retrieve the name of the Great Leader based on the provided level.

        Args:
            level (int): The level of the Great Leader to return.

        Returns:
            str: The name of the Great Leader.
        """
        try:
            log_info(log, f"Message triggering Great Leader: {level}")
            great_leaders = {
                1: "Kim Il-sung",
                2: "Kim Jong-il",
                3: "Kim Jong-un",
                4: "Kim Yo-jong",
                5: "Barack Obama"
            }
            context = AssistantCallContext.get_current()
            context.store_metadata("user_msg", great_leaders[level])
            return great_leaders[level]
        except Exception as e:
            log_error(log, f"Error in great_leader function: {e}")
            raise

@tracer.wrap(name="dd_trace.entrypoint",service="degreed-coach-realtime")
async def entrypoint(ctx: JobContext):
    """
    Entrypoint for the job context.

    Args:
        ctx (JobContext): The job context.
    """
    try:
        log_info(log, f"Connecting to room {ctx.room.name}")
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

        participant = await ctx.wait_for_participant()

        await run_multimodal_agent(ctx, participant)

        log_info(log, "Agent started")
    except Exception as e:
        log_error(log, f"Error in entrypoint: {e}")
        raise

@tracer.wrap(name="dd_trace.run_multimodal_agent",service="degreed-coach-realtime")
async def run_multimodal_agent(ctx: JobContext, participant: rtc.Participant):
    """
    Run the multimodal agent.

    Args:
        ctx (JobContext): The job context.
        participant (rtc.Participant): The participant.
    """
    try:
        start_time = datetime.now()
        metadata = json.loads(participant.metadata)
        config = await parse_session_config(metadata)
        messages = [
            {
                "role": "system",
                "content": config.instructions
            }
        ]
        
        log_info(log, f"Starting omni assistant")
        model = openai.realtime.RealtimeModel(
            api_key=os.getenv("OPENAI_API_KEY"),
            model=os.getenv("REALTIME_MODEL"),
            instructions=config.instructions,
            voice=config.voice,
            temperature=config.temperature,
            max_response_output_tokens=config.max_response_output_tokens,
            modalities=config.modalities,
            turn_detection=config.turn_detection,
            input_audio_transcription=openai.realtime.InputTranscriptionOptions(model="whisper-1")
        )
        assistant = MultimodalAgent(model=model)
                                    # fnc_ctx=AssistantFunction())

        assistant.start(ctx.room)
        session = model.sessions[0]

        # if config.modalities == ["text"]:
        #     session.conversation.item.create(
        #         llm.ChatMessage(
        #             role="user",
        #             content="Hi, Greetings...!"
        #                 )
        #             )
        #     session.response.create()

        # else:
        session.response.create()

        @tracer.wrap(name="dd_trace.send_session_expired",service="degreed-coach-realtime")
        async def send_session_expired(ctx):
            local_participant = ctx.room.local_participant
            track_sid = next(
                (
                    track.sid
                    for track in local_participant.track_publications.values()
                    if track.source == rtc.TrackSource.SOURCE_MICROPHONE
                ),
                None,
            )
            message = "Thanks for chatting! Our voice sessions are capped at 15 minutes, but you’re welcome to start a new session or continue the conversation over text."
            asyncio.create_task(
                send_transcription(
                    ctx=ctx, participant=local_participant, track_sid=track_sid, segment_id="time_limit_reached", text=message

                )
            )
            log_info(log, "15 minutes have passed since the start time.")

        @tracer.wrap(name="dd_trace.disconnect_after_timeout",service="degreed-coach-realtime")
        async def disconnect_after_timeout(ctx, start_time):
            while True:
                await asyncio.sleep(10)  
                print(f"Time elapsed: {(datetime.now() - start_time).total_seconds()}")
                if (datetime.now() - start_time).total_seconds() > 860:  # 900 seconds = 15 minutes
                    await send_session_expired(ctx)
                    break

        asyncio.create_task(disconnect_after_timeout(ctx, start_time))

        async def disconnect_after_timeout(ctx, start_time):
            while True:
                await asyncio.sleep(30)  
                print(f"Time elapsed: {(datetime.now() - start_time).total_seconds()}")
                if (datetime.now() - start_time).total_seconds() > 860:  # 900 seconds = 15 minutes
                    local_participant = ctx.room.local_participant
                    track_sid = next(
                        (
                            track.sid
                            for track in local_participant.track_publications.values()
                            if track.source == rtc.TrackSource.SOURCE_MICROPHONE
                        ),
                        None,
                    )
                    message = "Your session has expired. Please reconnect."
                    asyncio.create_task(
                        send_transcription(
                            ctx, local_participant, track_sid, "status-" + str(uuid.uuid4()), message
                        )
                    )
                    log_info(log, "15 minutes have passed since the start time.")

                    # ctx.room.disconnect()
                    ctx.shutdown(reason="Session expired")
                    break

        asyncio.create_task(disconnect_after_timeout(ctx, start_time))

        # @ctx.room.on("participant_attributes_changed")
        # def on_attributes_changed(
        #     changed_attributes: dict[str, str], changed_participant: rtc.Participant
        # ):
        #     """
        #     Handle participant attributes change event.

        #     Args:
        #         changed_attributes (dict[str, str]): The changed attributes.
        #         changed_participant (rtc.Participant): The changed participant.
        #     """
        #     try:
        #         if changed_participant == participant:
        #             return
                
        #         new_config = parse_session_config(
        #             {**participant.attributes, **changed_attributes}
        #         )
        #         log_info(log, f"Participant attributes changed for participant: {changed_participant.identity}")
        #         session = model.sessions[0]

        #         session.session_update(
        #             instructions=config.instructions,
        #             voice=new_config.voice,
        #             temperature=new_config.temperature,
        #             max_response_output_tokens=new_config.max_response_output_tokens,
        #             turn_detection=new_config.turn_detection,
        #             modalities=new_config.modalities,
        #         )
        #     except Exception as e:
        #         log_error(log, f"Error in on_attributes_changed: {e}")
        #         raise

        @tracer.wrap(name="dd_trace.send_transcription",service="degreed-coach-realtime")
        async def send_transcription(
            ctx: JobContext,
            participant: rtc.Participant,
            track_sid: str,
            segment_id: str,
            text: str,
            is_final: bool = True,
        ):
            """
            Send transcription to the room.

            Args:
                ctx (JobContext): The job context.
                participant (rtc.Participant): The participant.
                track_sid (str): The track SID.
                segment_id (str): The segment ID.
                text (str): The transcription text.
                is_final (bool): Whether the transcription is final.
            """
            try:
                transcription = rtc.Transcription(
                    participant_identity=participant.identity,
                    track_sid=track_sid,
                    segments=[
                        rtc.TranscriptionSegment(
                            id=segment_id,
                            text=text,
                            start_time=0,
                            end_time=0,
                            language="en",
                            final=is_final,
                        )
                    ],
                )
                await ctx.room.local_participant.publish_transcription(transcription)
            except Exception as e:
                log_error(log, f"Error in send_transcription: {e}")
                raise
        
        @tracer.wrap(name="dd_trace.on_response_done",service="degreed-coach-realtime")
        @session.on("response_done")
        def on_response_done(response: openai.realtime.RealtimeResponse):
            """
            Handle response done event.

            Args:
                response (openai.realtime.RealtimeResponse): The response.
            """
            try:
                local_participant = ctx.room.local_participant
                track_sid = next(
                    (
                        track.sid
                        for track in local_participant.track_publications.values()
                        if track.source == rtc.TrackSource.SOURCE_MICROPHONE
                    ),
                    None,
                )

                message = None
                nonlocal messages

                if response.status == "incomplete":
                    if response.status_details and response.status_details['reason']:
                        reason = response.status_details['reason']
                        if reason == "max_output_tokens":
                            message = "🚫 Max output tokens reached"
                        elif reason == "content_filter":
                            message = "🚫 Content filter applied"
                        else:
                            message = f"🚫 Response incomplete: {reason}"
                    else:
                        message = "🚫 Response incomplete"
                elif response.status == "failed":
                    if response.status_details and response.status_details['error']:
                        error_code = response.status_details['error']['code']
                        if error_code == "server_error":
                            message = "⚠️ Server error"
                        elif error_code == "rate_limit_exceeded":
                            message = "⚠️ Rate limit exceeded"
                        elif error_code == "session_expired":
                            asyncio.create_task(send_session_expired(ctx))
                        else:
                            message = "⚠️ Response failed"
                    else:
                        message = "⚠️ Response failed"
                else:
                    if response.output[0].content[0].text:
                        message = response.output[0].content[0].text
                        messages_with_timestamp = [{
                            "role": "assistant",
                            "content": message,
                            "timestamp": time.strftime("%Y%m%d_%H%M%S")
                        }]

                        redis_manager.store_chat(messages_with_timestamp, config.conversation_id)
                    
                        messages = [msg for msg in messages if msg["role"] != "system"]
                        messages.append({"role": "assistant", "content": message})

                        asyncio.create_task(save_message(sid=config.session_id, original_message=messages, type="Coach", conversation_id=config.conversation_id, coach_id=config.coach_id))

                if response.output and response.output[0].content:
                    log_info(log, f"Assistant: {response.output[0].content[0].text}")
                else:
                    log_info(log, "Assistant: No content available in the response output")
                asyncio.create_task(
                    send_transcription(
                        ctx, local_participant, track_sid, "status-" + str(uuid.uuid4()), ""
                    )
                )
            except Exception as e:
                log_error(log, f"Error in on_response_done: {e}")
                raise

        last_transcript_id = None

        @tracer.wrap(name="dd_trace.on_input_speech_started",service="degreed-coach-realtime")
        @session.on("input_speech_started")
        def on_input_speech_started():
            """
            Handle input speech started event.
            """
            try:
                nonlocal last_transcript_id
                remote_participant = next(iter(ctx.room.remote_participants.values()), None)
                if not remote_participant:
                    return

                track_sid = next(
                    (
                        track.sid
                        for track in remote_participant.track_publications.values()
                        if track.source == rtc.TrackSource.SOURCE_MICROPHONE
                    ),
                    None,
                )
                if last_transcript_id:
                    asyncio.create_task(
                        send_transcription(
                            ctx, remote_participant, track_sid, last_transcript_id, ""
                        )
                    )

                new_id = str(uuid.uuid4())
                last_transcript_id = new_id
                asyncio.create_task(
                    send_transcription(
                        ctx, remote_participant, track_sid, new_id, "…", is_final=False
                    )
                )
            except Exception as e:
                log_error(log, f"Error in on_input_speech_started: {e}")
                raise
        
        @tracer.wrap(name="dd_trace.on_input_speech_transcription_completed",service="degreed-coach-realtime")
        @session.on("input_speech_transcription_completed")
        def on_input_speech_transcription_completed(
            event: openai.realtime.InputTranscriptionCompleted,
        ):
            """
            Handle input speech transcription completed event.

            Args:
                event (openai.realtime.InputTranscriptionCompleted): The transcription completed event.
            """
            try:
                nonlocal messages

                if event.transcript:
                    message = event.transcript
                    messages_with_timestamp = [{
                        "role": "user",
                        "content": message,
                        "timestamp": time.strftime("%Y%m%d_%H%M%S")
                    }]

                    redis_manager.store_chat(messages_with_timestamp, config.conversation_id)

                    messages = [msg for msg in messages if msg["role"] != "system"]
                    messages.append({"role": "user", "content": message})

                    log_info(log, f"User: {event.transcript}, Conversation ID: {config.conversation_id}, Session ID: {config.session_id}")
                    asyncio.create_task(save_message(sid=config.session_id, original_message=messages, type="User", conversation_id=config.conversation_id, coach_id=config.coach_id))
                    log_info(log, f"User: {event.transcript}")
                nonlocal last_transcript_id
                if last_transcript_id:
                    remote_participant = next(iter(ctx.room.remote_participants.values()), None)
                    if not remote_participant:
                        return

                    track_sid = next(
                        (
                            track.sid
                            for track in remote_participant.track_publications.values()
                            if track.source == rtc.TrackSource.SOURCE_MICROPHONE
                        ),
                        None,
                    )
                    asyncio.create_task(
                        send_transcription(
                            ctx, remote_participant, track_sid, last_transcript_id, ""
                        )
                    )
                    last_transcript_id = None
            except Exception as e:
                log_error(log, f"Error in on_input_speech_transcription_completed: {e}")
                raise
        
        @tracer.wrap(name="dd_trace.on_input_speech_transcription_failed",service="degreed-coach-realtime")
        @session.on("input_speech_transcription_failed")
        def on_input_speech_transcription_failed(
            event: openai.realtime.InputTranscriptionFailed,
        ):
            """
            Handle input speech transcription failed event.

            Args:
                event (openai.realtime.InputTranscriptionFailed): The transcription failed event.
            """
            try:
                nonlocal last_transcript_id
                if last_transcript_id:
                    remote_participant = next(iter(ctx.room.remote_participants.values()), None)
                    if not remote_participant:
                        return

                    track_sid = next(
                        (
                            track.sid
                            for track in remote_participant.track_publications.values()
                            if track.source == rtc.TrackSource.SOURCE_MICROPHONE
                        ),
                        None,
                    )

                    error_message = "⚠️ Transcription failed"
                    asyncio.create_task(
                        send_transcription(
                            ctx,
                            remote_participant,
                            track_sid,
                            last_transcript_id,
                            error_message,
                        )
                    )
                    last_transcript_id = None
            except Exception as e:
                log_error(log, f"Error in on_input_speech_transcription_failed: {e}")
                raise

        @tracer.wrap(name="dd_trace.on_function_calls_finished",service="degreed-coach-realtime")
        @assistant.on("function_calls_finished")
        def on_function_calls_finished(called_functions: list[agents.llm.CalledFunction]):
            """
            Handle function calls finished event.

            Args:
                called_functions (list[agents.llm.CalledFunction]): The list of called functions.
            """
            try:
                if len(called_functions) == 0:
                    return

                level = called_functions[0].call_info.arguments.get("level")
            except Exception as e:
                log_error(log, f"Error in on_function_calls_finished: {e}")
                raise

    except Exception as e:
        if str(e) == "OpenAI S2S connection closed unexpectedly":
            asyncio.create_task(send_session_expired(ctx))
        else:
            log_error(log, f"Unexpected error in _main_task: {e}")
            raise

if __name__ == "__main__":
    print("Inside Main")
    try:
        print("Starting room")
        cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, worker_type=WorkerType.ROOM))
    except Exception as e:
        log_error(log, f"Error in main: {e}")
        raise