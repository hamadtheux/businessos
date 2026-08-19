from fastapi import APIRouter


api_router = APIRouter()


@api_router.get("/status", tags=["System"])
async def api_status() -> dict[str, str]:
    """
    Verify that the versioned API layer is available.
    """
    return {
        "status": "available",
        "api_version": "v1",
    }
