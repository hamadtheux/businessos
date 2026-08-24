from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch


os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.main import readiness_check  # noqa: E402


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_database_schema_is_ready(self) -> None:
        response = await self._readiness(installed_heads=["head-current"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response)["required"], {
            "database": "ready",
            "schema": "ready",
        })

    async def test_connected_but_outdated_schema_is_not_ready(self) -> None:
        response = await self._readiness(installed_heads=["head-old"])

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self._body(response)["required"], {
            "database": "ready",
            "schema": "not_ready",
        })

    async def test_unreachable_database_is_not_ready(self) -> None:
        with (
            patch("app.main.AsyncSessionFactory", new=_Factory(fail=True)),
            patch("app.main._expected_schema_heads", return_value=frozenset({"head-current"})),
        ):
            response = await readiness_check()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self._body(response)["required"], {
            "database": "not_ready",
            "schema": "not_ready",
        })

    async def _readiness(self, *, installed_heads: list[str]):
        with (
            patch("app.main.AsyncSessionFactory", new=_Factory(installed_heads)),
            patch("app.main._expected_schema_heads", return_value=frozenset({"head-current"})),
        ):
            return await readiness_check()

    @staticmethod
    def _body(response) -> dict[str, object]:
        return json.loads(response.body)


class _Factory:
    def __init__(self, installed_heads: list[str] | None = None, *, fail: bool = False):
        self.session = _Session(installed_heads or [], fail=fail)

    def __call__(self):
        return _Context(self.session)


class _Context:
    def __init__(self, session: "_Session") -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class _Session:
    def __init__(self, installed_heads: list[str], *, fail: bool) -> None:
        self.installed_heads = installed_heads
        self.fail = fail

    async def scalar(self, _statement):
        if self.fail:
            raise OSError("database unavailable")
        return 1

    async def scalars(self, _statement):
        return _Scalars(self.installed_heads)


class _Scalars:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    def all(self):
        return self.values


if __name__ == "__main__":
    unittest.main()
