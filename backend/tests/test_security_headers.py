from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.main import app, create_application  # noqa: E402


class SecurityHeadersTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_api_responses_include_strict_security_headers(self) -> None:
        response = await self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["X-Content-Type-Options"],
            "nosniff",
        )
        self.assertEqual(
            response.headers["Referrer-Policy"],
            "no-referrer",
        )
        self.assertEqual(
            response.headers["X-Frame-Options"],
            "DENY",
        )
        self.assertEqual(
            response.headers["Content-Security-Policy"],
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.assertTrue(response.headers["X-Request-ID"])
        self.assertNotIn(
            "Strict-Transport-Security",
            response.headers,
        )

    async def test_production_responses_include_hsts(self) -> None:
        with patch("app.main.settings.environment", "production"):
            response = await self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Strict-Transport-Security"],
            "max-age=31536000; includeSubDomains",
        )

    async def test_unhandled_errors_are_sanitized_and_keep_boundary_headers(self) -> None:
        isolated_app = create_application()

        @isolated_app.get("/phase7-test-unhandled-error")
        async def explode():
            raise RuntimeError("private provider response and password=private")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=isolated_app,
                raise_app_exceptions=False,
            ),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/phase7-test-unhandled-error")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {"detail": {
                "code": "internal_server_error",
                "message": "The request could not be completed.",
            }},
        )
        self.assertNotIn("private provider", response.text)
        self.assertTrue(response.headers["X-Request-ID"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_declared_oversized_request_is_rejected_before_routing(self) -> None:
        response = await self.client.post(
            "/api/v1/auth/login",
            headers={"Content-Length": str(3 * 1024 * 1024)},
            content=b"{}",
        )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"]["code"], "request_too_large")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(response.headers["X-Request-ID"])


if __name__ == "__main__":
    unittest.main()
