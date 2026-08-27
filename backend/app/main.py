from functools import lru_cache
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.db.session import AsyncSessionFactory
from app.integrations.adapters import connector_adapters
from app.services.billing import BillingConfigurationError, BillingEntitlementError


_BACKEND_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _expected_schema_heads() -> frozenset[str]:
    configuration = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    return frozenset(ScriptDirectory.from_config(configuration).get_heads())


def create_application() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(BillingEntitlementError)
    async def billing_entitlement_error(_request: Request, error: BillingEntitlementError):
        return JSONResponse(
            status_code=403 if error.code == "feature_not_in_plan" else 409,
            content={"detail": {
                "code": error.code,
                "entitlement_key": error.entitlement_key,
                "current": error.current,
                "limit": error.limit,
                "upgrade_required": True,
            }},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @application.exception_handler(BillingConfigurationError)
    async def billing_configuration_error(_request: Request, _error: BillingConfigurationError):
        return JSONResponse(
            status_code=503,
            content={"detail": {
                "code": "temporarily_unavailable",
                "service": "billing_authorization",
                "message": "Billing authorization is temporarily unavailable. Please try again.",
            }},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @application.middleware("http")
    async def prevent_private_business_response_caching(request, call_next):
        response = await call_next(request)

        business_root = f"{settings.api_v1_prefix}/businesses/"
        path = request.url.path

        if path.startswith(business_root):
            tenant_path = path.removeprefix(business_root).split("/")

            # Every authenticated business sub-resource is tenant-private.
            # Keep this structural so newly added domains cannot accidentally
            # become cacheable merely because a name was omitted here.
            private_resource = len(tenant_path) >= 2 and bool(tenant_path[1])

            if private_resource:
                response.headers["Cache-Control"] = "no-store"
                response.headers["Pragma"] = "no-cache"

        return response

    application.include_router(
        api_router,
        prefix=settings.api_v1_prefix,
    )

    if settings.storage_backend == "local":
        settings.storage_local_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        application.mount(
            f"{settings.api_v1_prefix}/media",
            StaticFiles(
                directory=settings.storage_local_directory,
                check_dir=False,
            ),
            name="local-media",
        )

    return application


app = create_application()


@app.get("/", tags=["System"])
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
    }


@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "service": settings.app_name,
    }


@app.get("/health/live", tags=["System"])
async def liveness_check() -> dict[str, str]:
    return {"status": "alive", "service": settings.app_name}


@app.get("/health/ready", tags=["System"])
async def readiness_check():
    database_connected = False
    schema_ready = False
    try:
        async with AsyncSessionFactory() as session:
            database_connected = (await session.scalar(text("SELECT 1"))) == 1
            if database_connected:
                installed_heads = frozenset(
                    str(value)
                    for value in (
                        await session.scalars(
                            text("SELECT version_num FROM alembic_version")
                        )
                    ).all()
                )
                schema_ready = (
                    bool(installed_heads)
                    and installed_heads == _expected_schema_heads()
                )
    except Exception:
        schema_ready = False
    required_ready = database_connected and schema_ready
    optional = {
        "ai_runtime": (
            "ready" if settings.openai_api_key_value else "configuration_required"
        ),
        "oauth_connectors": (
            "ready"
            if any(
                connector_adapters.is_configured(connector)
                for connector in (
                    "gmail", "google_calendar", "google_ads", "meta_ads",
                    "facebook", "instagram", "whatsapp_business",
                )
            )
            else "configuration_required"
        ),
        "secure_credential_store": (
            "ready"
            if settings.integration_credential_backend != "disabled"
            else "configuration_required"
        ),
        "external_connector_writes": (
            "enabled" if settings.external_connector_writes_enabled else "disabled"
        ),
        "billing_provider": (
            "ready" if settings.billing_provider != "disabled" else "configuration_required"
        ),
    }
    content = {
        "status": "ready" if required_ready else "not_ready",
        "required": {
            "database": "ready" if database_connected else "not_ready",
            "schema": "ready" if schema_ready else "not_ready",
        },
        "optional": optional,
    }
    return JSONResponse(
        status_code=200 if required_ready else 503,
        content=content,
        headers={"Cache-Control": "no-store"},
    )
