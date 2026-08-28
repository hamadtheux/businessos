from fastapi import APIRouter

from app.api.v1.ai_agents import router as ai_agents_router
from app.api.v1.ai_workforce import router as ai_workforce_router
from app.api.v1.auth import router as auth_router
from app.api.v1.approvals import router as approvals_router
from app.api.v1.automations import router as automations_router
from app.api.v1.business_brain import router as business_brain_router
from app.api.v1.business_brain_manifest import router as business_brain_manifest_router
from app.api.v1.business_memory import router as business_memory_router
from app.api.v1.businesses import router as businesses_router
from app.api.v1.catalog import router as catalog_router
from app.api.v1.commerce import router as commerce_router, webhook_router as commerce_webhook_router
from app.api.v1.chatbot import router as chatbot_router
from app.api.v1.scheduling import router as scheduling_router
from app.api.v1.operations import router as operations_router
from app.api.v1.marketing import router as marketing_router
from app.api.v1.integrations import router as integrations_router
from app.api.v1.processing import router as processing_router
from app.api.v1.billing import router as billing_router
from app.api.v1.growth_learning import router as growth_learning_router


api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(businesses_router)
api_router.include_router(catalog_router)
api_router.include_router(commerce_router)
api_router.include_router(commerce_webhook_router)
api_router.include_router(chatbot_router)
api_router.include_router(business_brain_router)
api_router.include_router(business_brain_manifest_router)
api_router.include_router(business_memory_router)
api_router.include_router(ai_agents_router)
api_router.include_router(ai_workforce_router)
api_router.include_router(approvals_router)
api_router.include_router(automations_router)
api_router.include_router(scheduling_router)
api_router.include_router(operations_router)
api_router.include_router(marketing_router)
api_router.include_router(integrations_router)
api_router.include_router(processing_router)
api_router.include_router(billing_router)
api_router.include_router(growth_learning_router)


@api_router.get("/status", tags=["System"])
async def api_status() -> dict[str, str]:
    """
    Verify that the versioned API layer is available.
    """
    return {
        "status": "available",
        "api_version": "v1",
    }
