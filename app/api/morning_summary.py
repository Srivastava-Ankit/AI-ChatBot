from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.llm.tools.tools import MorningSummary

from app.log_manager import get_logger, log_debug, log_info, log_error, log_warn

router = APIRouter()
log = get_logger(__name__)

@router.get("/morning-summary", tags=["Morning Summary"])
async def morning_summary(coach_id: str, user_id: str):
    """
    Get the morning summary for the user.

    Returns:
        JSONResponse: The morning summary for the user.
    """
    log_info(log, f"Generating morning summary for user {user_id} and coach {coach_id}")
    morning_summary = MorningSummary()

    summary = await morning_summary.morning_summary(user_id=user_id, coach_id=coach_id)
    log_info(log, f"Morning summary generated for user {user_id} and coach {coach_id}")
    return JSONResponse(content=summary, status_code=200)
