from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings


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

    @application.middleware("http")
    async def prevent_private_business_response_caching(request, call_next):
        response = await call_next(request)

        business_root = f"{settings.api_v1_prefix}/businesses/"
        path = request.url.path

        if path.startswith(business_root):
            tenant_path = path.removeprefix(business_root).split("/")

            private_resource = (
                len(tenant_path) >= 2
                and tenant_path[1]
                in {
                    "catalog",
                    "brain",
                    "memory",
                    "agents",
                }
            )

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