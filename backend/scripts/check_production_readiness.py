from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


async def _check_database(settings) -> tuple[bool, bool]:
    from app.db.session import AsyncSessionFactory, engine

    alembic = AlembicConfig(str(_BACKEND_ROOT / "alembic.ini"))
    expected = frozenset(ScriptDirectory.from_config(alembic).get_heads())
    database_connected = False
    schema_current = False
    try:
        async with AsyncSessionFactory() as session:
            database_connected = (await session.scalar(text("SELECT 1"))) == 1
            installed = frozenset(
                str(value)
                for value in (
                    await session.scalars(text("SELECT version_num FROM alembic_version"))
                ).all()
            )
            schema_current = bool(installed) and installed == expected
    finally:
        await engine.dispose()
    return database_connected, schema_current


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate production configuration and required database readiness."
    )
    parser.add_argument(
        "--configuration-only",
        action="store_true",
        help="Validate fail-fast production settings without opening a database connection.",
    )
    args = parser.parse_args()

    try:
        from app.core.config import settings
    except Exception as exc:
        _emit({
            "status": "not_ready",
            "configuration": "invalid",
            "failure_type": type(exc).__name__,
        })
        return 1

    if settings.environment != "production":
        _emit({
            "status": "not_ready",
            "configuration": "invalid",
            "failure_code": "production_environment_required",
        })
        return 1

    result: dict[str, object] = {
        "status": "ready" if args.configuration_only else "checking",
        "configuration": "valid",
        "external_connector_write_mode": settings.external_connector_write_mode,
        "optional": {
            "openai": "configured" if settings.openai_api_key_value else "not_configured",
            "oauth": "configured" if settings.integration_oauth_callback_url else "not_configured",
            "credential_store": settings.integration_credential_backend,
        },
    }
    if args.configuration_only:
        _emit(result)
        return 0

    try:
        database_connected, schema_current = await _check_database(settings)
    except Exception as exc:
        _emit({
            **result,
            "status": "not_ready",
            "database": "not_ready",
            "schema": "not_ready",
            "failure_type": type(exc).__name__,
        })
        return 1

    ready = database_connected and schema_current
    _emit({
        **result,
        "status": "ready" if ready else "not_ready",
        "database": "ready" if database_connected else "not_ready",
        "schema": "ready" if schema_current else "not_ready",
    })
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
