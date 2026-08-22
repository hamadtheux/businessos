from fastapi import APIRouter

from app.api.v1.ai_agents import router as ai_agents_router
from app.api.v1.auth import router as auth_router
from app.api.v1.business_brain import router as business_brain_router
from app.api.v1.business_brain_manifest import router as business_brain_manifest_router
from app.api.v1.business_memory import router as business_memory_router
from app.api.v1.businesses import router as businesses_router
from app.api.v1.catalog import router as catalog_router


api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(businesses_router)
api_router.include_router(catalog_router)
api_router.include_router(business_brain_router)
api_router.include_router(business_brain_manifest_router)
api_router.include_router(business_memory_router)
api_router.include_router(ai_agents_router)


@api_router.get("/status", tags=["System"])
async def api_status() -> dict[str, str]:
    """
    Verify that the versioned API layer is available.
    """
    return {
        "status": "available",
        "api_version": "v1",
    }