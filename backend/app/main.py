from functools import lru_cache
import logging
from pathlib import Path
import re
from secrets import token_hex
from time import monotonic

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, request_id_context
from app.db.session import AsyncSessionFactory
from app.integrations.adapters import connector_adapters
from app.services.billing import BillingConfigurationError, BillingEntitlementError


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
_BUSINESS_PATH = re.compile(r"/businesses/([0-9a-fA-F-]{36})(?:/|$)")
logger = logging.getLogger("aibos.api")


@lru_cache(maxsize=1)
def _expected_schema_heads() -> frozenset[str]:
    configuration = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    return frozenset(ScriptDirectory.from_config(configuration).get_heads())


def create_application() -> FastAPI:
    configure_logging(role="api")
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_hosts,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
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
    async def production_response_boundary(request, call_next):
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID.fullmatch(supplied_request_id)
            else token_hex(16)
        )
        request.state.request_id = request_id
        context_token = request_id_context.set(request_id)
        started = monotonic()
        status_code = 500
        try:
            size_error = _content_length_error(request)
            response = size_error if size_error is not None else await call_next(request)
            status_code = response.status_code
        except Exception as error:
            # Catch inside the user-middleware boundary so even unexpected
            # failures retain request/security headers. Never log exception
            # text, bodies, credentials, or private business content.
            logger.error(
                "unhandled_application_error",
                extra={
                    "request_id": request_id,
                    "exception_type": type(error).__name__,
                },
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": {
                    "code": "internal_server_error",
                    "message": "The request could not be completed.",
                }},
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        finally:
            duration_ms = round((monotonic() - started) * 1_000, 2)
            business_match = _BUSINESS_PATH.search(request.url.path)
            logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "business_id": business_match.group(1) if business_match else None,
                },
            )
            request_id_context.reset(context_token)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

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


def _content_length_error(request: Request) -> JSONResponse | None:
    """Reject declared oversized bodies before routing or reading content.

    The production edge remains responsible for bounded chunked bodies. This
    application check is a second fail-closed layer for direct API traffic and
    keeps oversized requests out of Pydantic, upload, and provider handlers.
    """
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": {
                "code": "invalid_content_length",
                "message": "The request could not be completed.",
            }},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    if length < 0:
        return JSONResponse(
            status_code=400,
            content={"detail": {
                "code": "invalid_content_length",
                "message": "The request could not be completed.",
            }},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    if length <= settings.request_max_bytes:
        return None
    return JSONResponse(
        status_code=413,
        content={"detail": {
            "code": "request_too_large",
            "message": "The request exceeds the allowed size.",
        }},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


app = create_application()


@app.get("/", tags=["System"])
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "status": "running",
    }


@app.get("/version", tags=["System"])
async def version_check() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
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
            settings.external_connector_write_mode
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
