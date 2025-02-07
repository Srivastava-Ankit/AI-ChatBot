from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
import asyncio
from typing import Iterator, List
import anyio
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import traceback
from websockets.exceptions import ConnectionClosedError

from fastapi.responses import JSONResponse
from concurrent.futures import ThreadPoolExecutor, as_completed 
from concurrent.futures import TimeoutError as ConnectionTimeoutError
from fastapi.websockets import WebSocketState
import httpx
from pydantic import BaseModel
import pytz
from app.config import BASE_URL
from app.db.redis_manager import RedisManager
from app.request_and_response.custom_types import (
    ConfigResponse,
    ResponseRequiredRequest,
)
from app.llm.llm import LlmClient  
# from app.llm.llm_with_func_calling import LlmClient

from app.api import api_router
from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn
from app.dg_component.cookie_utils import create_cookie_dict
from ddtrace import tracer, patch_all

patch_all()

@asynccontextmanager
async def lifespan(app: FastAPI) -> Iterator[None]:
    limiter = anyio.to_thread.current_default_thread_limiter()
    limiter.total_tokens = 1000
    yield
    
log = get_logger(__name__)
redis_manager = RedisManager()

load_dotenv(override=True)

DISABLE_DOCS = os.getenv("DISABLE_DOCS", "True")

if DISABLE_DOCS == "True" or DISABLE_DOCS == "true":
    app = FastAPI(title="Coach",root_path="/dgcb",docs_url=None, redoc_url=None)
else:
    app = FastAPI(title="Coach",root_path="/dgcb")


print(f"AZURE_GPT_4O_BASE_URL: {os.getenv('AZURE_GPT_4O_BASE_URL')}")
print(f"AZURE_GPT_4O_API_VERSION: {os.getenv('AZURE_GPT_4O_API_VERSION')}")
print(f"AZURE_GPT_4O_DEPLOYMENT_NAME: {os.getenv('AZURE_GPT_4O_DEPLOYMENT_NAME')}")

app.include_router(api_router, prefix="/api")

executor = ThreadPoolExecutor(max_workers=5)  # Adjust max_workers based on your requirements

# Allow all origins, or you can specify particular origins
origins = [
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@tracer.wrap(name="dd_trace.health_check",service="degreed-coach-builder")
@app.get("/healthcheck")
async def health_check(custom_header: str = Header(default=None)):
    custom_header_value = "gethealth"
    # Check custom header value
    if custom_header == custom_header_value:
        log_info(log, "Status: Ok")
        return {"status": "Ok"}
    else:
        log_error(log, "Custom header validation failed with HTTP Error Code 503")
        raise HTTPException(status_code=503, detail="Custom header validation failed")
    
@tracer.wrap(name="dd_trace.readiness_check",service="degreed-coach-builder")
@app.get("/readiness")
async def readiness_check():
    log_info(log, "Status: Ready")
    return {"status": "Ready"}

@tracer.wrap(name="dd_trace.login",service="degreed-coach-builder")
@app.post("/login")
async def login(user: dict, use_email: bool = False, identity_cookie_exists: bool = True,
                password_override: str = None):
    """
    Handle user login and session management.

    Args:
        user (dict): The user credentials.
        use_email (bool): Flag to use email for login. Default is False.
        identity_cookie_exists (bool): Flag to check if identity cookie exists. Default is True.
        password_override (str): Optional password override.

    Returns:
        dict: A dictionary containing the login result and access token.
    """
    timeout = httpx.Timeout(120.0, read=None)

    client_session = httpx.AsyncClient(verify=False, timeout=timeout)
    first_key, first_credentials = next(iter(user["credentials"].items()))  # Adjust as per your setup
    username = user["credentials"][first_key]["email"] if use_email else user["credentials"][first_key]["username"]
    password = password_override if password_override else user["credentials"][first_key]["password"]

    # Fetch CSRF token and return URL
    await client_session.get(f"{BASE_URL['host']}/account/login", timeout=timeout)
    csrf_token = None
    for c in client_session.cookies.jar:
        if c.name.startswith('antiforgery-request.v4'):
            csrf_token = c.value
            break

    if not csrf_token:
        log_error(log, "HTTP Status Code : 400, Error: CSRF Token not found")
        raise HTTPException(status_code=400, detail="CSRF Token not found")

    # Ideally, extract returnUrl from the login page's content or headers
    data = {
        "__RequestVerificationToken": csrf_token,
        "returnUrl": "/me",
        "username": username,
        "password": password
    }

    # Perform login
    r1 = await client_session.post(f"{BASE_URL['host']}/account/login", data=data, timeout=timeout)
    # Follow the redirect after login
    r2 = await client_session.get(f"{BASE_URL['host']}/{username}/dashboard", timeout=timeout)

    auth_token = ""
    if r2.status_code in (302, 200):
        for c in client_session.cookies.jar:
            if c.name.startswith('identity.v4'):
                auth_token = c.value
                break
            else:
                log_error(log, "Unable to get auth_token as there is no attribute of identity.v4 in the cookies!")

    if auth_token:
        cookies = create_cookie_dict(client_session.cookies)
        redis_manager.add_object(f"user_session_{auth_token}", json.dumps(cookies))
        log_info(log, "Login Successful")
        return {"message": "Login successful", "access_token": auth_token}
    else:
        log_info(log, "Login Not Successful as there was no auth_token")
        return {"message": "Login unsuccessful", "access_token": ""}
