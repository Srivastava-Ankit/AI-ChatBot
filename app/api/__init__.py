from fastapi import APIRouter
from app.api.sse import router as sse_router
from app.api.morning_summary import router as morning_summary_router
from app.api.post_process import router as post_process_router
from app.api.test import router as test_router
from app.api.realtime import router as realtime_router

api_router = APIRouter()

api_router.include_router(sse_router, prefix="/sse", tags=["SSE"])
api_router.include_router(morning_summary_router, prefix="/morning_summary", tags=["Morning Summary"])
api_router.include_router(post_process_router, prefix="/post_process", tags=["Post Process"])
api_router.include_router(test_router, prefix="/test", tags=["Test"])
api_router.include_router(realtime_router, prefix="/realtime", tags=["Realtime"])