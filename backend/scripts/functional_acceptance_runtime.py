"""Durable local functional acceptance for the golden QA commerce tenant.

This script intentionally creates clearly labeled records through the running
HTTP API.  It only accepts loopback URLs and never dispatches an external
connector action.  A configured development AI provider is exercised because
the product contract requires real, persisted AI output when a key exists.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx


QA_BUSINESS_ID = UUID("a11b0a50-2026-4824-9000-000000000001")
QA_BUSINESS_NAME = "[QA-ACCEPTANCE] Golden Commerce"
QA_LABEL = "[QA-ACCEPTANCE]"
QA_EMAIL = "golden-commerce-owner@example.com"
ADMIN_EMAIL = "qa-platform-admin@example.com"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("AIBOS_QA_BASE_URL", "http://127.0.0.1:5174"),
    )
    parser.add_argument(
        "--admin-api-url",
        default=os.getenv("AIBOS_QA_ADMIN_API_URL", "http://127.0.0.1:8003"),
    )
    return parser.parse_args()


def _loopback(value: str, label: str) -> str:
    normalized = value.rstrip("/")
    if urlparse(normalized).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"{label} must be a loopback URL")
    return normalized


def _expect(response: httpx.Response, *statuses: int) -> httpx.Response:
    if response.status_code not in statuses:
        body = response.text.replace("\n", " ")[:1_000]
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {body}"
        )
    return response


def _register_or_login(
    client: httpx.Client,
    api_url: str,
    *,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
) -> tuple[str, bool]:
    registration = client.post(
        f"{api_url}/auth/register",
        json={
            "email": email,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    _expect(registration, 201, 409)
    login = _expect(
        client.post(
            f"{api_url}/auth/login",
            json={"email": email, "password": password},
        ),
        200,
    ).json()
    return login["access_token"], registration.status_code == 201


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    raise RuntimeError("Expected an API list response")


def _one(values: list[dict[str, Any]], predicate, label: str) -> dict[str, Any]:
    result = next((item for item in values if predicate(item)), None)
    if result is None:
        raise RuntimeError(f"Missing expected {label}")
    return result


def main() -> None:
    args = _arguments()
    frontend = _loopback(args.base_url, "base-url")
    admin_origin = _loopback(args.admin_api_url, "admin-api-url")
    api_url = f"{frontend}/api/v1"
    admin_api_url = f"{admin_origin}/api/v1"
    qa_password = os.getenv("AIBOS_QA_PASSWORD", "")
    admin_password = os.getenv("AIBOS_QA_ADMIN_PASSWORD", "")
    if not qa_password or not admin_password:
        raise RuntimeError("AIBOS_QA_PASSWORD and AIBOS_QA_ADMIN_PASSWORD are required")

    today = date.today()
    period_start = today - timedelta(days=30)
    schedule_at = datetime.now(UTC) + timedelta(days=3)
    evidence: dict[str, Any] = {
        "qa_business": {
            "id": str(QA_BUSINESS_ID),
            "name": QA_BUSINESS_NAME,
            "owner_email": QA_EMAIL,
        },
        "external_writes": "disabled",
    }

    with httpx.Client(timeout=150, follow_redirects=False) as client:
        token, registered = _register_or_login(
            client,
            api_url,
            email=QA_EMAIL,
            password=qa_password,
            first_name="Golden",
            last_name="QA Owner",
        )
        headers = _headers(token)
        businesses = _expect(client.get(f"{api_url}/businesses", headers=headers), 200).json()
        business = next(
            (item for item in businesses if item["id"] == str(QA_BUSINESS_ID)),
            None,
        )
        business_created = False
        if business is None:
            onboarding = _expect(
                client.post(
                    f"{api_url}/businesses",
                    headers=headers,
                    json={
                        "business_id": str(QA_BUSINESS_ID),
                        "name": QA_BUSINESS_NAME,
                        "business_type": "e-commerce",
                        "timezone": "Asia/Karachi",
                        "currency": "PKR",
                        "locale": "en",
                        "website_url": "https://qa-golden-commerce.invalid",
                        "location": "Karachi, Pakistan",
                        "description": (
                            f"{QA_LABEL} Local reusable commerce tenant for persisted "
                            "functional acceptance only."
                        ),
                        "brand_voice": "Clear, practical, warm, and evidence-based.",
                        "avoid_keywords": ["guaranteed", "miracle", "risk-free"],
                        "branding": {
                            "primary_color": "#176B3F",
                            "secondary_color": "#E6F5E9",
                            "accent_color": "#E96825",
                        },
                    },
                ),
                201,
            ).json()
            business = onboarding["business"]
            business_created = True
        evidence["onboarding"] = {
            "user_registered": registered,
            "business_created": business_created,
            "business_id": business["id"],
        }

        with httpx.Client(timeout=60) as admin_client:
            admin_token, _ = _register_or_login(
                admin_client,
                admin_api_url,
                email=ADMIN_EMAIL,
                password=admin_password,
                first_name="QA",
                last_name="Platform Admin",
            )
            billing = _expect(
                admin_client.put(
                    f"{admin_api_url}/platform/billing/businesses/"
                    f"{QA_BUSINESS_ID}/subscription",
                    headers=_headers(admin_token),
                    json={
                        "plan_code": "pro",
                        "billing_interval": "month",
                        "trial_days": 0,
                        "reason": "Local golden tenant functional production acceptance",
                    },
                ),
                200,
            ).json()
        if billing["plan_code"] != "pro" or billing["subscription_status"] != "active":
            raise RuntimeError("Server-authoritative Pro assignment did not persist")
        evidence["billing"] = {
            "plan": billing["plan_code"],
            "status": billing["subscription_status"],
            "provider_configured": billing["provider_configured"],
        }

        refreshed = _expect(client.post(f"{api_url}/auth/refresh"), 200).json()
        token = refreshed["access_token"]
        headers = _headers(token)
        base = f"{api_url}/businesses/{QA_BUSINESS_ID}"

        branding = _expect(
            client.put(
                f"{base}/branding",
                headers=headers,
                json={
                    "primary_color": "#176B3F",
                    "secondary_color": "#E6F5E9",
                    "accent_color": "#E96825",
                },
            ),
            200,
        ).json()
        evidence["branding"] = {
            "primary_color": branding["primary_color"],
            "persisted": True,
        }

        catalog = _expect(client.get(f"{base}/catalog", headers=headers), 200).json()
        if not catalog:
            csv_data = (
                "type,name,description,sku,price,status\n"
                "product,[QA-ACCEPTANCE] Focus Tea,Calming green tea blend for focused work,QA-TEA-001,1850.00,active\n"
                "product,[QA-ACCEPTANCE] Desk Reset Kit,Notebook timer and planning cards for a weekly reset,QA-KIT-002,4200.00,active\n"
                "product,[QA-ACCEPTANCE] Momentum Planner,Undated ninety-day commerce planning workbook,QA-PLAN-003,2750.00,active\n"
            ).encode()
            files = {"file": ("qa-golden-catalog.csv", csv_data, "text/csv")}
            preview = _expect(
                client.post(f"{base}/catalog/import/preview", headers=headers, files=files),
                200,
            ).json()
            if preview["valid_rows"] != 3 or preview["invalid_rows"] != 0:
                raise RuntimeError(f"Catalog import preview was not valid: {preview}")
            files = {"file": ("qa-golden-catalog.csv", csv_data, "text/csv")}
            _expect(client.post(f"{base}/catalog/import", headers=headers, files=files), 201)
            catalog = _expect(client.get(f"{base}/catalog", headers=headers), 200).json()
        if len(catalog) < 3 or not all(QA_LABEL in item["name"] for item in catalog[:3]):
            raise RuntimeError("The QA catalog was not persisted")
        evidence["products"] = [
            {"id": item["id"], "name": item["name"], "sku": item["sku"], "price": item["price"]}
            for item in catalog
            if QA_LABEL in item["name"]
        ]

        knowledge = _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
        required_knowledge = (
            (
                "faq",
                f"{QA_LABEL} Delivery and returns",
                "Karachi orders are prepared within one business day. Local delivery is normally two to three business days. Unused items may be returned within fourteen days after review by support.",
            ),
            (
                "brand",
                f"{QA_LABEL} Customer promise",
                "Recommend only catalog products that fit the stated need. Never promise guaranteed productivity or business results. Quote prices in PKR from the current catalog.",
            ),
        )
        existing_titles = {item["title"] for item in knowledge}
        for category, title, content in required_knowledge:
            if title not in existing_titles:
                _expect(
                    client.post(
                        f"{base}/brain/knowledge",
                        headers=headers,
                        json={
                            "category": category,
                            "title": title,
                            "content": content,
                            "status": "active",
                        },
                    ),
                    201,
                )
        knowledge = _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
        manifest_before = _expect(client.get(f"{base}/brain/manifest", headers=headers), 200).json()
        if manifest_before["source_count"] < 7:
            raise RuntimeError(f"Business Brain is unexpectedly sparse: {manifest_before}")

        customer_page = _expect(
            client.get(f"{base}/customers?page_size=100", headers=headers), 200
        ).json()
        customer = next(
            (item for item in _items(customer_page) if item.get("email") == "sana.qa@example.com"),
            None,
        )
        if customer is None:
            customer = _expect(
                client.post(
                    f"{base}/customers",
                    headers=headers,
                    json={
                        "display_name": f"{QA_LABEL} Sana Buyer",
                        "first_name": "Sana",
                        "last_name": "Buyer",
                        "email": "sana.qa@example.com",
                        "phone": "+923001112233",
                        "source": "qa_acceptance",
                        "tags": ["qa-acceptance", "repeat-buyer"],
                        "company": "QA Studio",
                        "notes": "Golden tenant customer; safe local acceptance record.",
                    },
                ),
                201,
            ).json()

        leads_page = _expect(
            client.get(f"{base}/crm/leads?page_size=100", headers=headers), 200
        ).json()
        lead = next(
            (item for item in _items(leads_page) if item.get("email") == "sana.qa@example.com"),
            None,
        )
        if lead is None:
            lead = _expect(
                client.post(
                    f"{base}/crm/leads",
                    headers=headers,
                    json={
                        "customer_id": customer["id"],
                        "display_name": f"{QA_LABEL} Sana Focus Bundle",
                        "company": "QA Studio",
                        "email": customer["email"],
                        "phone": customer["phone"],
                        "stage": "new",
                        "source": "qa_acceptance",
                        "priority": "high",
                        "qualification_state": "unqualified",
                        "estimated_value": "6950.00",
                        "currency": "PKR",
                        "expected_close_date": (today + timedelta(days=7)).isoformat(),
                        "notes": "Customer asked about a Focus Tea and Desk Reset Kit bundle.",
                    },
                ),
                201,
            ).json()
        if lead["qualification_state"] != "qualified":
            lead = _expect(
                client.post(
                    f"{base}/crm/leads/{lead['id']}/qualification",
                    headers=headers,
                    json={"qualification_state": "qualified"},
                ),
                200,
            ).json()
        if lead["stage"] == "new":
            lead = _expect(
                client.post(
                    f"{base}/crm/leads/{lead['id']}/stage",
                    headers=headers,
                    json={"stage": "qualified"},
                ),
                200,
            ).json()
        if lead["customer_id"] != customer["id"]:
            raise RuntimeError("CRM lead lost its customer relationship")
        evidence["customer_crm"] = {
            "customer_id": customer["id"],
            "lead_id": lead["id"],
            "stage": lead["stage"],
            "qualification_state": lead["qualification_state"],
            "relationship_persisted": True,
        }

        orders_page = _expect(
            client.get(f"{base}/orders?page_size=100", headers=headers), 200
        ).json()
        order = next(
            (item for item in _items(orders_page) if item["customer_id"] == customer["id"]),
            None,
        )
        if order is None:
            first, second = evidence["products"][:2]
            order = _expect(
                client.post(
                    f"{base}/orders",
                    headers=headers,
                    json={
                        "customer_id": customer["id"],
                        "source": "qa_acceptance",
                        "currency": "PKR",
                        "notes": f"{QA_LABEL} Persisted commerce acceptance order.",
                        "lines": [
                            {
                                "catalog_item_id": first["id"],
                                "description": first["name"],
                                "quantity": 1,
                                "unit_price": first["price"],
                            },
                            {
                                "catalog_item_id": second["id"],
                                "description": second["name"],
                                "quantity": 1,
                                "unit_price": second["price"],
                            },
                        ],
                    },
                ),
                201,
            ).json()
        if order["status"] == "draft":
            order = _expect(
                client.post(
                    f"{base}/orders/{order['id']}/status",
                    headers=headers,
                    json={"status": "confirmed"},
                ),
                200,
            ).json()
        if order["status"] == "confirmed":
            order = _expect(
                client.post(
                    f"{base}/orders/{order['id']}/status",
                    headers=headers,
                    json={"status": "processing"},
                ),
                200,
            ).json()
        evidence["product_order"] = {
            "order_id": order["id"],
            "order_number": order["order_number"],
            "status": order["status"],
            "total": order["total"],
            "customer_id": order["customer_id"],
        }

        workflows = _expect(
            client.get(f"{base}/automations/workflows?page_size=100", headers=headers), 200
        ).json()
        workflow_name = f"{QA_LABEL} New lead evidence workflow"
        workflow = next(
            (item for item in _items(workflows) if item["name"] == workflow_name),
            None,
        )
        if workflow is None:
            workflow = _expect(
                client.post(
                    f"{base}/automations/workflows",
                    headers=headers,
                    json={
                        "name": workflow_name,
                        "description": "Create an evidence-linked opportunity and an internal notification for a newly created QA lead.",
                        "trigger_type": "lead_created",
                        "timezone": "Asia/Karachi",
                    },
                ),
                201,
            ).json()
            trigger = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "trigger",
                        "name": "Lead created",
                        "configuration": {"kind": "trigger", "trigger_type": "lead_created"},
                        "position_x": 0,
                        "position_y": 0,
                        "order_index": 0,
                    },
                ),
                201,
            ).json()
            opportunity_node = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "internal_operation",
                        "name": "Create evidence-linked opportunity",
                        "configuration": {
                            "kind": "internal_operation",
                            "operation": "create_opportunity",
                            "parameters": {
                                "title": f"{QA_LABEL} Follow up qualified bundle interest",
                                "description": "A real QA customer and linked CRM lead expressed interest in two persisted catalog products. Review the qualified lead and prepare a value-based follow-up; no message has been sent.",
                                "category": "sales_opportunity",
                                "source": "automation",
                                "priority": "high",
                                "estimated_value": "6950.00",
                                "currency": "PKR",
                                "customer_id": customer["id"],
                                "lead_id": lead["id"],
                            },
                        },
                        "position_x": 260,
                        "position_y": 0,
                        "order_index": 1,
                    },
                ),
                201,
            ).json()
            notification_node = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "internal_operation",
                        "name": "Notify the owner",
                        "configuration": {
                            "kind": "internal_operation",
                            "operation": "create_notification",
                            "parameters": {
                                "category": "automation",
                                "title": f"{QA_LABEL} New lead workflow completed",
                                "message": "A real lead-created trigger produced an internal sales opportunity and this notification.",
                                "priority": "high",
                                "related_entity_type": "crm_lead",
                                "related_entity_id": lead["id"],
                            },
                        },
                        "position_x": 520,
                        "position_y": 0,
                        "order_index": 2,
                    },
                ),
                201,
            ).json()
            end = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/nodes",
                    headers=headers,
                    json={
                        "node_type": "end",
                        "name": "Complete",
                        "configuration": {"kind": "end", "outcome": "success"},
                        "position_x": 780,
                        "position_y": 0,
                        "order_index": 3,
                    },
                ),
                201,
            ).json()
            for source, target in (
                (trigger["node_key"], opportunity_node["node_key"]),
                (opportunity_node["node_key"], notification_node["node_key"]),
                (notification_node["node_key"], end["node_key"]),
            ):
                _expect(
                    client.post(
                        f"{base}/automations/workflows/{workflow['id']}/edges",
                        headers=headers,
                        json={"source_node_key": source, "target_node_key": target},
                    ),
                    201,
                )
            validation = _expect(
                client.get(
                    f"{base}/automations/workflows/{workflow['id']}/validation",
                    headers=headers,
                ),
                200,
            ).json()
            if not validation["valid"]:
                raise RuntimeError(f"Automation graph is invalid: {validation}")
            workflow = _expect(
                client.post(
                    f"{base}/automations/workflows/{workflow['id']}/status",
                    headers=headers,
                    json={"status": "active"},
                ),
                200,
            ).json()

        automation_lead_email = "automation.qa@example.com"
        leads_page = _expect(
            client.get(f"{base}/crm/leads?page_size=100", headers=headers), 200
        ).json()
        automation_lead = next(
            (item for item in _items(leads_page) if item.get("email") == automation_lead_email),
            None,
        )
        if automation_lead is None:
            automation_lead = _expect(
                client.post(
                    f"{base}/crm/leads",
                    headers=headers,
                    json={
                        "customer_id": customer["id"],
                        "display_name": f"{QA_LABEL} Automation Trigger Lead",
                        "email": automation_lead_email,
                        "stage": "new",
                        "source": "qa_automation",
                        "priority": "medium",
                        "qualification_state": "unqualified",
                        "estimated_value": "4200.00",
                        "currency": "PKR",
                        "notes": "Created after workflow activation to produce a real lead_created event.",
                    },
                ),
                201,
            ).json()
        events_page = _expect(
            client.get(f"{base}/automations/events?page_size=100", headers=headers), 200
        ).json()
        event = _one(
            _items(events_page),
            lambda item: item["event_type"] == "lead_created"
            and item["entity_id"] == automation_lead["id"],
            "lead_created automation event",
        )
        if event["status"] == "pending":
            processed = _expect(
                client.post(
                    f"{base}/automations/events/{event['id']}/process",
                    headers=headers,
                ),
                200,
            ).json()
            run_ids = processed["created_run_ids"]
        else:
            runs_page = _expect(
                client.get(
                    f"{base}/automations/runs?workflow_id={workflow['id']}&page_size=100",
                    headers=headers,
                ),
                200,
            ).json()
            run_ids = [item["id"] for item in _items(runs_page)]
        if not run_ids:
            raise RuntimeError("Processing the lead event created no workflow run")
        run_id = run_ids[0]
        run = _expect(
            client.post(f"{base}/automations/runs/{run_id}/advance", headers=headers),
            200,
        ).json()
        if run["status"] != "succeeded":
            raise RuntimeError(f"Internal automation did not succeed: {run}")
        node_runs = _expect(
            client.get(
                f"{base}/automations/runs/{run_id}/nodes?page_size=100",
                headers=headers,
            ),
            200,
        ).json()
        evidence["automation"] = {
            "workflow_id": workflow["id"],
            "event_id": event["id"],
            "run_id": run_id,
            "status": run["status"],
            "steps": [
                {"name": item["node_name"], "status": item["status"], "summary": item["result_summary"]}
                for item in _items(node_runs)
            ],
        }

        baseline_agent = _expect(
            client.post(
                f"{base}/agents/execute",
                headers=headers,
                json={
                    "role": "business_manager",
                    "task": "Summarize the current QA business using cited catalog and policy facts only. Analysis only; do not propose or execute actions.",
                },
            ),
            200,
        ).json()
        context_entry_title = f"{QA_LABEL} Current operating priority"
        knowledge = _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
        context_entry = next(
            (item for item in knowledge if item["title"] == context_entry_title),
            None,
        )
        context_content = (
            "Current QA priority: follow up qualified bundle interest, complete the "
            "internal content review, and do not dispatch external provider actions. "
            f"Acceptance refresh: {datetime.now(UTC).isoformat()}."
        )
        if context_entry is None:
            context_entry = _expect(
                client.post(
                    f"{base}/brain/knowledge",
                    headers=headers,
                    json={
                        "category": "operations",
                        "title": context_entry_title,
                        "content": context_content,
                        "status": "active",
                    },
                ),
                201,
            ).json()
        else:
            context_entry = _expect(
                client.patch(
                    f"{base}/brain/knowledge/{context_entry['id']}",
                    headers=headers,
                    json={"content": context_content},
                ),
                200,
            ).json()
        manifest_after = _expect(client.get(f"{base}/brain/manifest", headers=headers), 200).json()
        if manifest_after["revision"] == manifest_before["revision"]:
            raise RuntimeError("Business Brain revision did not change after a real source update")

        agent_tasks = {
            "cmo": "Propose an internal weekly content focus for the persisted QA catalog. Analysis only; do not publish or propose actions.",
            "sales": "Explain a safe follow-up approach for the Focus Tea and Desk Reset Kit interest. Analysis only; do not send messages or propose actions.",
            "support": "Draft a factual support answer about delivery and returns using Business Brain. Analysis only; do not contact anyone or propose actions.",
            "operations": "Identify one internal operational priority from the current business context. Analysis only; do not dispatch or propose actions.",
            "analytics": "Summarize what can and cannot be concluded from the current QA business sources. Analysis only; do not propose actions.",
        }
        agent_results = {
            "business_manager": {
                "status": baseline_agent["output"]["status"],
                "summary": baseline_agent["output"]["summary"],
                "context_revision": baseline_agent["context_revision"],
                "source_count": baseline_agent["business_brain_source_count"],
            }
        }
        for role, task in agent_tasks.items():
            result = _expect(
                client.post(
                    f"{base}/agents/execute",
                    headers=headers,
                    json={"role": role, "task": task},
                ),
                200,
            ).json()
            agent_results[role] = {
                "status": result["output"]["status"],
                "summary": result["output"]["summary"],
                "context_revision": result["context_revision"],
                "source_count": result["business_brain_source_count"],
            }
        if agent_results["analytics"]["context_revision"] == baseline_agent["context_revision"]:
            raise RuntimeError("Later AI execution did not receive the revised Business Brain")
        config = _expect(
            client.patch(
                f"{base}/agents/analytics",
                headers=headers,
                json={
                    "custom_instructions": (
                        "For this QA tenant, separate observed counts from hypotheses and cite "
                        "the available Business Brain records."
                    )
                },
            ),
            200,
        ).json()
        persisted_config = _expect(
            client.get(f"{base}/agents/analytics", headers=headers), 200
        ).json()
        if persisted_config["custom_instructions"] != config["custom_instructions"]:
            raise RuntimeError("Agent configuration did not persist")
        evidence["business_brain"] = {
            "before": manifest_before,
            "after": manifest_after,
            "knowledge_records": [
                {"id": item["id"], "title": item["title"], "category": item["category"]}
                for item in _expect(client.get(f"{base}/brain/knowledge", headers=headers), 200).json()
                if QA_LABEL in item["title"]
            ],
            "ai_context_revision_changed": True,
        }
        evidence["ai_agents"] = agent_results

        commands = [
            "What should I focus on today?",
            "Create a marketing plan for my products.",
            "Find sales opportunities.",
            "Summarize my customers and CRM pipeline.",
            "Suggest content for this week.",
        ]
        command_history = _expect(
            client.get(f"{base}/commands?page_size=100", headers=headers), 200
        ).json()
        command_results = []
        for command in commands:
            value = next(
                (
                    item
                    for item in _items(command_history)
                    if item["command"] == command
                    and item["status"] in {"completed", "needs_approval"}
                    and item["summary"]
                ),
                None,
            )
            last_response: httpx.Response | None = None
            for _ in range(3):
                if value is not None:
                    value = _expect(
                        client.get(
                            f"{base}/commands/{value['id']}", headers=headers
                        ),
                        200,
                    ).json()
                    break
                last_response = client.post(
                    f"{base}/commands",
                    headers=headers,
                    json={"command": command, "trigger_source": "command_center"},
                )
                if last_response.status_code == 201:
                    candidate = last_response.json()
                    if (
                        candidate["status"] in {"completed", "needs_approval"}
                        and candidate["summary"]
                    ):
                        value = candidate
                        break
                elif last_response.status_code != 503:
                    _expect(last_response, 201)
            if value is None:
                detail = last_response.text[:1_000] if last_response is not None else "no response"
                raise RuntimeError(
                    f"Command did not produce useful persisted output after bounded retries: "
                    f"{command!r}: {detail}"
                )
            command_results.append({
                "id": value["id"],
                "command": command,
                "status": value["status"],
                "primary_role": value["route"]["primary_role"],
                "summary": value["summary"],
                "executions": len(value["executions"]),
            })
        evidence["commands"] = command_results

        audience_name = f"{QA_LABEL} Karachi focus buyers"
        audience_page = _expect(
            client.get(f"{base}/marketing/audiences?page_size=100", headers=headers), 200
        ).json()
        audience = next(
            (item for item in _items(audience_page) if item["name"] == audience_name),
            None,
        )
        if audience is None:
            audience = _expect(
                client.post(
                    f"{base}/marketing/audiences",
                    headers=headers,
                    json={
                        "name": audience_name,
                        "countries": ["PK"],
                        "regions": ["Karachi"],
                        "min_age": 21,
                        "max_age": 55,
                        "languages": ["en"],
                        "customer_lifecycle": ["qualified_lead", "existing_customer"],
                        "crm_stages": ["qualified"],
                        "interests": ["focus", "planning", "work routines"],
                        "existing_customer_segment": "QA acceptance contacts",
                        "segment_description": "Internal hypothesis grounded in the persisted customer, CRM lead, and order records.",
                    },
                ),
                201,
            ).json()
        plan_title = f"{QA_LABEL} Thirty-day product plan"
        plan_page = _expect(
            client.get(f"{base}/marketing/plans?page_size=100", headers=headers), 200
        ).json()
        plan = next(
            (item for item in _items(plan_page) if item["title"] == plan_title), None
        )
        if plan is None:
            plan = _expect(
                client.post(
                    f"{base}/marketing/plans/generate",
                    headers=headers,
                    json={
                        "goal": "Create an internal thirty-day plan to grow qualified interest in the three persisted QA products without external ad execution.",
                        "title": plan_title,
                        "target_audience": "Existing QA customers and qualified planning-product leads in Karachi.",
                        "channels": ["instagram", "email", "website"],
                        "budget_guidance": "0.00",
                        "period_start": today.isoformat(),
                        "period_end": (today + timedelta(days=30)).isoformat(),
                    },
                ),
                201,
            ).json()
        campaign_name = f"{QA_LABEL} Focus bundle campaign"
        campaign_page = _expect(
            client.get(f"{base}/marketing/campaigns?page_size=100", headers=headers), 200
        ).json()
        campaign = next(
            (item for item in _items(campaign_page) if item["name"] == campaign_name),
            None,
        )
        if campaign is None:
            campaign = _expect(
                client.post(
                    f"{base}/marketing/campaigns/generate",
                    headers=headers,
                    json={
                        "goal": "Prepare an internal product-bundle campaign proposal using the persisted catalog, customer, CRM, and order aggregates.",
                        "name": campaign_name,
                        "audience_definition": "Karachi contacts with observed interest in focus and planning products; unobserved details remain hypotheses.",
                        "channels": ["instagram", "email"],
                        "planned_budget": "0.00",
                        "budget_mode": "lifetime",
                        "start_date": today.isoformat(),
                        "end_date": (today + timedelta(days=21)).isoformat(),
                    },
                ),
                201,
            ).json()
        else:
            campaign = _expect(
                client.get(
                    f"{base}/marketing/campaigns/{campaign['id']}", headers=headers
                ),
                200,
            ).json()
        hypothesis = _expect(
            client.get(
                f"{base}/marketing/campaigns/{campaign['id']}/audience-intelligence",
                headers=headers,
            ),
            200,
        ).json()
        content_title = f"{QA_LABEL} Focus bundle review draft"
        edited_title = f"{QA_LABEL} Focus bundle — reviewed copy"
        content_page = _expect(
            client.get(f"{base}/marketing/content?page_size=100", headers=headers), 200
        ).json()
        edited = next(
            (item for item in _items(content_page) if item["title"] == edited_title),
            None,
        )
        if edited is None:
            content = next(
                (item for item in _items(content_page) if item["title"] == content_title),
                None,
            )
            last_content_response: httpx.Response | None = None
            for _ in range(3):
                if content is not None:
                    break
                last_content_response = client.post(
                    f"{base}/marketing/content/generate",
                    headers=headers,
                    json={
                        "prompt": "Write a review-ready Instagram post for the Focus Tea and Desk Reset Kit. Use current catalog facts and avoid guaranteed outcomes.",
                        "campaign_id": campaign["id"],
                        "channel": "instagram",
                        "content_type": "social_post",
                        "title": content_title,
                        "language": "en",
                    },
                )
                if last_content_response.status_code == 201:
                    content = last_content_response.json()
                elif last_content_response.status_code != 503:
                    _expect(last_content_response, 201)
            if content is None:
                raise RuntimeError(
                    "AI content did not produce a persisted draft after bounded retries: "
                    + (last_content_response.text[:1_000] if last_content_response else "no response")
                )
            edited = _expect(
                client.post(
                    f"{base}/marketing/content/{content['id']}/versions",
                    headers=headers,
                    json={
                        "title": edited_title,
                        "body": content["body"] + "\n\nQA review note: current catalog prices apply; no outcome is guaranteed.",
                        "cta": "Review the current catalog before ordering.",
                    },
                ),
                201,
            ).json()
        for status_value in ("review", "approved"):
            if edited["status"] == status_value or edited["status"] in {"approved", "scheduled"}:
                continue
            edited = _expect(
                client.post(
                    f"{base}/marketing/content/{edited['id']}/status",
                    headers=headers,
                    json={"status": status_value},
                ),
                200,
            ).json()
        schedules = _expect(
            client.get(f"{base}/marketing/calendar", headers=headers), 200
        ).json()
        schedule = next(
            (item for item in schedules if item["content_id"] == edited["id"]), None
        )
        if schedule is None:
            schedule = _expect(
                client.post(
                    f"{base}/marketing/calendar",
                    headers=headers,
                    json={
                        "content_id": edited["id"],
                        "scheduled_for": schedule_at.isoformat().replace("+00:00", "Z"),
                    },
                ),
                201,
            ).json()
        approved_page = _expect(
            client.get(f"{base}/approvals?status=approved&limit=100", headers=headers),
            200,
        ).json()
        approval = next(
            (
                item
                for item in _items(approved_page)
                if edited_title in (item.get("action") or {}).get("description", "")
            ),
            None,
        )
        if approval is None:
            proposal = _expect(
                client.post(
                    f"{base}/marketing/content/{edited['id']}/prepare-publish",
                    headers=headers,
                    json={"channel": "instagram"},
                ),
                201,
            ).json()
            if proposal["approval_status"] != "pending" or proposal["connector_state"] == "ready_after_approval":
                raise RuntimeError("Governed content proposal did not remain connector-safe")
            approval_before = _expect(
                client.get(f"{base}/approvals/{proposal['approval_id']}", headers=headers), 200
            ).json()
            approval = _expect(
                client.post(
                    f"{base}/approvals/{proposal['approval_id']}/approve",
                    headers=headers,
                    json={"decision_note": "QA content and safe payload reviewed; keep external dispatch unavailable."},
                ),
                200,
            ).json()
        else:
            approval_before = {"status": "pending"}
        if approval["status"] != "approved" or approval["action"]["status"] not in {"ready", "queued"}:
            raise RuntimeError("Approval did not ready or durably queue the AIAction")

        trend = _expect(
            client.post(
                f"{base}/marketing/trends",
                headers=headers,
                json={
                    "title": f"{QA_LABEL} Bundle interest in persisted CRM and order data",
                    "category": "customer_demand",
                    "description": "The golden tenant contains a qualified bundle lead and a processing two-product order. This is a local QA signal only, not a market-wide claim.",
                    "source": "manual",
                    "source_reference": f"crm-lead:{lead['id']};order:{order['id']}",
                    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "relevance_score": "0.900",
                    "confidence": "0.850",
                },
            ),
            201,
        ).json()
        trend = _expect(
            client.post(
                f"{base}/marketing/trends/{trend['id']}/status",
                headers=headers,
                json={"status": "reviewed"},
            ),
            200,
        ).json()
        trend_opportunity = _expect(
            client.post(
                f"{base}/marketing/trends/{trend['id']}/opportunity",
                headers=headers,
                json={"priority": "high"},
            ),
            201,
        ).json()
        if (
            trend_opportunity["source_entity_id"] != trend["id"]
            or not trend_opportunity["reason"]
            or not trend_opportunity["provenance"]
        ):
            raise RuntimeError("Trend opportunity lost its evidence linkage")
        evidence["marketing"] = {
            "saved_audience_id": audience["id"],
            "plan": {"id": plan["id"], "status": plan["status"], "title": plan["title"]},
            "campaign": {
                "id": campaign["id"],
                "status": campaign["status"],
                "name": campaign["name"],
                "source_evidence": campaign["source_evidence"],
            },
            "audience_hypothesis": {
                "id": hypothesis["id"],
                "classification": hypothesis["classification"],
                "evidence": hypothesis["evidence"],
            },
            "content": {"id": edited["id"], "status": edited["status"], "title": edited["title"]},
            "calendar": {"id": schedule["id"], "status": schedule["status"], "scheduled_for": schedule["scheduled_for"]},
            "approval": {
                "id": approval["id"],
                "before": approval_before["status"],
                "after": approval["status"],
                "action_status": approval["action"]["status"],
                "payload_summary": approval["action"]["payload_summary"],
            },
            "opportunity": {
                "id": trend_opportunity["id"],
                "source": trend_opportunity["source"],
                "source_entity_type": trend_opportunity["source_entity_type"],
                "source_entity_id": trend_opportunity["source_entity_id"],
                "reason": trend_opportunity["reason"],
                "recommendation": trend_opportunity["recommendation"],
                "provenance": trend_opportunity["provenance"],
            },
        }

        chatbot = _expect(
            client.put(
                f"{base}/chatbot",
                headers=headers,
                json={
                    "enabled": True,
                    "display_name": "Golden Commerce Assistant",
                    "welcome_message": "Ask about the QA catalog, delivery, returns, or a current order.",
                    "placeholder_text": "Ask about a product",
                    "tone": "friendly",
                    "theme": "light",
                    "position": "bottom_right",
                    "launcher_style": "bubble",
                    "allowed_capabilities": [
                        "answer_business_questions",
                        "search_products_services",
                        "recommend_products_services",
                        "capture_lead",
                        "lookup_order_status",
                        "request_human_handoff",
                    ],
                    "allowed_domains": [],
                    "privacy_policy_url": None,
                    "consent_text": "I agree that this local QA business may follow up.",
                    "require_lead_consent": True,
                    "default_locale": "en",
                    "border_radius": 18,
                },
            ),
            200,
        ).json()
        hosted = _expect(
            client.post(f"{base}/chatbot/deployments/hosted", headers=headers), 201
        ).json()
        if ":5174/hosted.html" not in (hosted["hosted_url"] or ""):
            raise RuntimeError(f"Hosted assistant URL points at the wrong local app: {hosted}")
        public_config = _expect(
            client.get(
                f"{api_url}/public/hosted-widgets/{chatbot['widget_public_id']}/config"
            ),
            200,
        ).json()
        public_session = _expect(
            client.post(
                f"{api_url}/public/hosted-widgets/{chatbot['widget_public_id']}/sessions"
            ),
            201,
        ).json()
        public_headers = {"Authorization": f"Bearer {public_session['session_token']}"}
        public_answer = _expect(
            client.post(
                f"{api_url}/public/widgets/{chatbot['widget_public_id']}/sessions/messages",
                headers=public_headers,
                json={"message": "What is the price of the Focus Tea and what is your return policy?"},
            ),
            200,
        ).json()
        if not any(item["name"].endswith("Focus Tea") for item in public_answer["products"]):
            raise RuntimeError("Hosted chatbot did not return the real catalog product")
        conversations = _expect(
            client.get(f"{base}/conversations?page_size=100", headers=headers), 200
        ).json()
        conversation = _one(
            _items(conversations),
            lambda item: item["channel"] == "website" and "Focus Tea" in (item["latest_message"] or ""),
            "hosted chatbot conversation",
        )
        conversation_detail = _expect(
            client.get(f"{base}/conversations/{conversation['id']}", headers=headers), 200
        ).json()
        if len(conversation_detail["messages"]) < 2:
            raise RuntimeError("Hosted conversation message history did not persist")
        order_lookup = _expect(
            client.post(
                f"{api_url}/public/widgets/{chatbot['widget_public_id']}/sessions/order-status",
                headers=public_headers,
                json={
                    "order_reference": order["order_number"],
                    "email": customer["email"],
                    "phone": None,
                },
            ),
            200,
        ).json()
        evidence["chatbot"] = {
            "config_status": chatbot["lifecycle_status"],
            "hosted_url": hosted["hosted_url"],
            "public_business_name": public_config["business_name"],
            "answer": public_answer["message"],
            "catalog_results": public_answer["products"],
            "conversation_id": conversation["id"],
            "message_count": len(conversation_detail["messages"]),
            "order_lookup": order_lookup,
        }

        report = _expect(
            client.post(
                f"{base}/reports/generate",
                headers=headers,
                json={
                    "report_type": "daily_operations",
                    "period_start": period_start.isoformat(),
                    "period_end": today.isoformat(),
                },
            ),
            201,
        ).json()
        analytics = _expect(
            client.get(
                f"{base}/analytics/core?period_start={period_start}&period_end={today}",
                headers=headers,
            ),
            200,
        ).json()
        marketing_analytics = _expect(
            client.get(
                f"{base}/marketing/analytics?period_start={period_start}&period_end={today}",
                headers=headers,
            ),
            200,
        ).json()
        if analytics["customers"] < 1 or analytics["orders"] < 1 or analytics["ai_executions"] < 6:
            raise RuntimeError(f"Core analytics did not reflect QA activity: {analytics}")
        report_metrics = report["metrics"]
        if report_metrics.get("customers", 0) < 1 or report_metrics.get("orders", 0) < 1:
            raise RuntimeError(f"Daily report did not reflect QA records: {report}")
        evidence["dashboard_analytics_report"] = {
            "core": analytics,
            "marketing": marketing_analytics,
            "daily_report": {
                "id": report["id"],
                "summary": report["summary"],
                "metrics": report_metrics,
            },
        }

        notifications = _expect(
            client.get(f"{base}/notifications?page_size=100", headers=headers), 200
        ).json()
        notification_items = _items(notifications)
        if not notification_items:
            raise RuntimeError("No persisted notifications were produced")
        chosen_notification = notification_items[0]
        _expect(
            client.post(
                f"{base}/notifications/{chosen_notification['id']}/read",
                headers=headers,
            ),
            200,
        )
        notifications_after = _expect(
            client.get(f"{base}/notifications?page_size=100", headers=headers), 200
        ).json()
        persisted_read = _one(
            _items(notifications_after),
            lambda item: item["id"] == chosen_notification["id"],
            "read notification",
        )
        if not persisted_read["read"]:
            raise RuntimeError("Notification read state did not persist")
        audits = _expect(
            client.get(f"{base}/audit?page_size=100", headers=headers), 200
        ).json()
        audit_items = _items(audits)
        required_audit_prefixes = (
            "customer.",
            "crm_lead.",
            "approval.",
            "automation.",
            "chatbot.",
            "marketing.",
        )
        missing_audits = [
            prefix
            for prefix in required_audit_prefixes
            if not any(item["event_type"].startswith(prefix) for item in audit_items)
        ]
        if missing_audits:
            raise RuntimeError(f"Missing required audit groups: {missing_audits}")
        evidence["notifications"] = {
            "count": len(notification_items),
            "categories": sorted({item["category"] for item in notification_items}),
            "read_state_persisted": True,
        }
        evidence["audit"] = {
            "count": len(audit_items),
            "event_types": sorted({item["event_type"] for item in audit_items}),
        }

        integrations = _expect(
            client.get(f"{base}/integrations/connections", headers=headers), 200
        ).json()
        processing = _expect(
            client.get(f"{base}/processing/health", headers=headers), 200
        ).json()
        evidence["external_configuration"] = {
            "integration_connections": len(integrations),
            "billing_provider_configured": billing["provider_configured"],
            "external_connector_writes": "disabled",
            "processing": processing,
        }

    with httpx.Client(timeout=60) as relogin_client:
        relogin = _expect(
            relogin_client.post(
                f"{api_url}/auth/login",
                json={"email": QA_EMAIL, "password": qa_password},
            ),
            200,
        ).json()
        reload_headers = _headers(relogin["access_token"])
        persisted_businesses = _expect(
            relogin_client.get(f"{api_url}/businesses", headers=reload_headers), 200
        ).json()
        if not any(item["id"] == str(QA_BUSINESS_ID) for item in persisted_businesses):
            raise RuntimeError("QA business did not persist across a new login")
        persisted_catalog = _expect(
            relogin_client.get(
                f"{api_url}/businesses/{QA_BUSINESS_ID}/catalog",
                headers=reload_headers,
            ),
            200,
        ).json()
        persisted_order = _expect(
            relogin_client.get(
                f"{api_url}/businesses/{QA_BUSINESS_ID}/orders/{evidence['product_order']['order_id']}",
                headers=reload_headers,
            ),
            200,
        ).json()
        evidence["relogin_persistence"] = {
            "business": True,
            "catalog_count": len(persisted_catalog),
            "order_status": persisted_order["status"],
        }

    print(json.dumps(evidence, indent=2, sort_keys=True))
    print("RESULT: golden QA functional acceptance data and workflows persisted")


if __name__ == "__main__":
    main()
