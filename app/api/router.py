"""Top-level API router composition."""

from fastapi import APIRouter

from app.api.deletion.routes import router as deletion_router
from app.api.interview.routes import router as interview_router
from app.api.memory.routes import router as memory_router
from app.api.prep.routes import router as prep_router
from app.api.reports.routes import router as reports_router
from app.api.runtime.routes import router as runtime_router


router = APIRouter(prefix="/api")
router.include_router(runtime_router)
router.include_router(prep_router)
router.include_router(interview_router)
router.include_router(deletion_router)
router.include_router(memory_router)
router.include_router(reports_router)


__all__ = ["router"]
