from __future__ import annotations

import json
import logging
import os
import unittest

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.core.logging import (  # noqa: E402
    JsonLogFormatter,
    SecretRedactionFilter,
    redact_text,
    request_id_context,
)


class LogRedactionTests(unittest.TestCase):
    def test_common_credentials_and_cookies_are_redacted(self) -> None:
        raw = (
            "Authorization: Basic dXNlcjpzZWNyZXQ= "
            "Cookie: aibos_refresh=refresh-private; csrf=csrf-private\n"
            "access_token=access-private refresh_token=refresh-private "
            "password=private-password api_key=sk-proj-privatevalue123"
        )

        redacted = redact_text(raw)

        for private in (
            "dXNlcjpzZWNyZXQ=",
            "refresh-private",
            "csrf-private",
            "access-private",
            "private-password",
            "sk-proj-privatevalue123",
        ):
            self.assertNotIn(private, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_mapping_style_authorization_and_cookie_headers_are_redacted(self) -> None:
        raw = (
            '{"Authorization": "Bearer bearer-private-token", '
            '"Cookie": "aibos_refresh=refresh-private; csrf=csrf-private"}'
        )

        redacted = redact_text(raw)

        self.assertNotIn("bearer-private-token", redacted)
        self.assertNotIn("refresh-private", redacted)
        self.assertNotIn("csrf-private", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_filter_preserves_numeric_interpolation_arguments(self) -> None:
        record = logging.LogRecord(
            "aibos.test",
            logging.INFO,
            __file__,
            1,
            "processed %d items with token=%s",
            (3, "private-token"),
            None,
        )

        self.assertTrue(SecretRedactionFilter().filter(record))
        self.assertEqual(record.getMessage(), "processed 3 items with token=[REDACTED]")

    def test_json_formatter_keeps_safe_context_and_omits_exception_text(self) -> None:
        try:
            raise RuntimeError("password=private-error-detail")
        except RuntimeError:
            record = logging.getLogger("aibos.test").makeRecord(
                "aibos.test",
                logging.ERROR,
                __file__,
                1,
                "provider_failed api_key=sk-privatevalue123",
                (),
                exc_info=os.sys.exc_info(),
                extra={"provider": "openai", "job_id": "job-safe"},
            )

        token = request_id_context.set("request-safe")
        try:
            payload = json.loads(JsonLogFormatter().format(record))
        finally:
            request_id_context.reset(token)

        self.assertEqual(payload["request_id"], "request-safe")
        self.assertEqual(payload["provider"], "openai")
        self.assertEqual(payload["exception_type"], "RuntimeError")
        serialized = json.dumps(payload)
        self.assertNotIn("private-error-detail", serialized)
        self.assertNotIn("sk-privatevalue123", serialized)


if __name__ == "__main__":
    unittest.main()
