"""Real local production-flow acceptance against the running frontend proxy.

This intentionally creates durable QA records. Run it only against a disposable
or local development database. It never calls an external connector or performs
an external write.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

import httpx


BOOLEAN_ENTITLEMENTS = (
    "ai_command_center",
    "ai_agents",
    "website_chatbot",
    "automations",
    "advanced_automations",
    "marketing_cmo",
    "campaigns",
    "competitor_intelligence",
    "trend_intelligence",
    "scheduling",
    "integrations",
    "advanced_analytics",
    "reports",
)

INTEGER_ENTITLEMENTS = {
    "max_active_workflows": 100,
    "max_integrations": 20,
    "max_chatbot_sessions_month": 100,
    "max_chatbot_messages_month": 1_000,
    "max_ai_executions_month": 100,
    "max_ai_input_tokens_month": 1_000_000,
    "max_ai_output_tokens_month": 250_000,
    "max_automation_runs_month": 1_000,
}

PRIMARY_ROUTES = (
    "/dashboard",
    "/command-center",
    "/conversations",
    "/orders",
    "/customers",
    "/crm",
    "/catalog",
    "/chatbot",
    "/marketing",
    "/marketing/content",
    "/marketing/calendar",
    "/marketing/campaigns",
    "/marketing/social",
    "/competitors",
    "/trends",
    "/agents",
    "/automations",
    "/approvals",
    "/opportunities",
    "/analytics",
    "/daily-report",
    "/integrations",
    "/business-brain",
    "/audit",
    "/settings",
    "/billing",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("AIBOS_SMOKE_BASE_URL", "http://127.0.0.1:5174"),
        help="Running frontend origin whose /api path proxies to the API.",
    )
    parser.add_argument(
        "--admin-api-url",
        default=os.getenv("AIBOS_SMOKE_ADMIN_API_URL", "http://127.0.0.1:8003"),
        help="Local API process configured with the smoke admin identity.",
    )
    return parser.parse_args()


def _require_loopback(value: str, label: str) -> str:
    normalized = value.rstrip("/")
    hostname = urlparse(normalized).hostname
    if hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"{label} must be a loopback URL, received {hostname!r}")
    return normalized


def _expect(response: httpx.Response, *statuses: int) -> httpx.Response:
    if response.status_code not in statuses:
        body = response.text.replace("\n", " ")[:500]
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {body}"
        )
    return response


def _login(
    client: httpx.Client,
    api_url: str,
    *,
    email: str,
    password: str,
) -> str:
    response = _expect(
        client.post(
            f"{api_url}/auth/login",
            json={"email": email, "password": password},
        ),
        200,
    )
    return response.json()["access_token"]


def _authorize(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def main() -> None:
    args = _arguments()
    frontend = _require_loopback(args.base_url, "base-url")
    admin_origin = _require_loopback(args.admin_api_url, "admin-api-url")
    api_url = f"{frontend}/api/v1"
    admin_api_url = f"{admin_origin}/api/v1"

    admin_email = os.getenv("AIBOS_SMOKE_ADMIN_EMAIL", "").strip()
    admin_password = os.getenv("AIBOS_SMOKE_ADMIN_PASSWORD", "")
    if not admin_email or not admin_password:
        raise RuntimeError(
            "AIBOS_SMOKE_ADMIN_EMAIL and AIBOS_SMOKE_ADMIN_PASSWORD are required"
        )

    run_id = uuid4().hex[:12]
    email = f"runtime-flow-{run_id}@example.com"
    password = f"RuntimeFlow!{run_id}Aa9"
    business_id = str(uuid4())

    today = date.today()
    period_start = today - timedelta(days=30)
    period_end = today
    calendar_start = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    calendar_end = (
        datetime.now(UTC) + timedelta(days=30)
    ).isoformat().replace("+00:00", "Z")

    with httpx.Client(timeout=45, follow_redirects=False) as client:
        _expect(
            client.post(
                f"{api_url}/auth/register",
                json={
                    "email": email,
                    "password": password,
                    "first_name": "Runtime",
                    "last_name": "Acceptance",
                },
            ),
            201,
        )
        print("register: 201")

        token = _login(
            client,
            api_url,
            email=email,
            password=password,
        )
        print("login: 200 with refresh cookie")

        headers = _authorize(token)
        onboarding = _expect(
            client.post(
                f"{api_url}/businesses",
                headers=headers,
                json={
                    "business_id": business_id,
                    "name": f"Runtime Product Flow {run_id}",
                    "business_type": "e-commerce",
                    "timezone": "UTC",
                    "currency": "USD",
                    "locale": "en",
                },
            ),
            201,
        )
        assert onboarding.json()["business"]["id"] == business_id
        print("business onboarding: 201")

        with httpx.Client(timeout=30) as admin_client:
            admin_token = _login(
                admin_client,
                admin_api_url,
                email=admin_email,
                password=admin_password,
            )
            admin_headers = _authorize(admin_token)
            for key in BOOLEAN_ENTITLEMENTS:
                _expect(
                    admin_client.post(
                        f"{admin_api_url}/platform/billing/businesses/"
                        f"{business_id}/entitlement-overrides",
                        headers=admin_headers,
                        json={
                            "entitlement_key": key,
                            "boolean_value": True,
                            "reason": "Local full-product runtime acceptance",
                        },
                    ),
                    200,
                )
            for key, value in INTEGER_ENTITLEMENTS.items():
                _expect(
                    admin_client.post(
                        f"{admin_api_url}/platform/billing/businesses/"
                        f"{business_id}/entitlement-overrides",
                        headers=admin_headers,
                        json={
                            "entitlement_key": key,
                            "integer_value": value,
                            "reason": "Local full-product runtime acceptance",
                        },
                    ),
                    200,
                )
        print("internal entitlements: enabled")

        refresh = _expect(client.post(f"{api_url}/auth/refresh"), 200)
        token = refresh.json()["access_token"]
        headers = _authorize(token)
        print("reload/session refresh: 200")

        businesses = _expect(
            client.get(f"{api_url}/businesses", headers=headers),
            200,
        ).json()
        selected = next(
            (item for item in businesses if item["id"] == business_id),
            None,
        )
        if selected is None:
            raise RuntimeError("The onboarded business was absent from the list")
        _expect(
            client.get(
                f"{api_url}/businesses/{business_id}/branding",
                headers=headers,
            ),
            200,
        )
        print("business list and selection: 200")

        for route in PRIMARY_ROUTES:
            page = _expect(client.get(f"{frontend}{route}"), 200)
            if '<div id="root"></div>' not in page.text:
                raise RuntimeError(f"Frontend fallback was not served for {route}")
        print(f"frontend route fallbacks: {len(PRIMARY_ROUTES)} passed")

        business_path = f"{api_url}/businesses/{business_id}"
        api_checks = {
            "dashboard": (
                f"{business_path}/analytics/core?period_start={period_start}"
                f"&period_end={period_end}"
            ),
            "command center": f"{business_path}/commands",
            "conversations": f"{business_path}/conversations",
            "orders": f"{business_path}/orders",
            "customers": f"{business_path}/customers",
            "crm": f"{business_path}/crm/leads",
            "catalog": f"{business_path}/catalog",
            "chatbot": f"{business_path}/chatbot",
            "marketing plans": f"{business_path}/marketing/plans",
            "marketing content": f"{business_path}/marketing/content",
            "marketing calendar": (
                f"{business_path}/marketing/calendar?start_at="
                f"{calendar_start}&end_at={calendar_end}"
            ),
            "marketing campaigns": f"{business_path}/marketing/campaigns",
            "marketing competitors": f"{business_path}/marketing/competitors",
            "marketing trends": f"{business_path}/marketing/trends",
            "agents": f"{business_path}/agents",
            "automations": f"{business_path}/automations/workflows",
            "approvals": f"{business_path}/approvals",
            "opportunities": f"{business_path}/opportunities",
            "analytics": (
                f"{business_path}/marketing/analytics?period_start="
                f"{period_start}&period_end={period_end}"
            ),
            "daily report": f"{business_path}/reports",
            "integrations": f"{business_path}/integrations/registry",
            "business brain": f"{business_path}/brain/manifest",
            "audit": f"{business_path}/audit",
            "settings": f"{business_path}/branding",
            "billing": f"{business_path}/billing",
            "processing": f"{business_path}/processing/health",
        }
        for label, url in api_checks.items():
            _expect(client.get(url, headers=headers), 200)
            print(f"{label}: 200")

        connections = _expect(
            client.get(
                f"{business_path}/integrations/connections",
                headers=headers,
            ),
            200,
        ).json()
        if connections:
            raise RuntimeError("Fresh smoke tenant unexpectedly has provider connections")
        print("optional providers: configuration states only; no connections")
        print("RESULT: full local production-flow acceptance passed")


if __name__ == "__main__":
    main()
