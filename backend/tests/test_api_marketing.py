from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

os.environ.setdefault("AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test")
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.ai_agent import get_ai_agent_provider  # noqa: E402
from app.api.dependencies.business import BusinessAccessContext, get_business_access  # noqa: E402
from app.api.v1.marketing import _mutate as marketing_mutate  # noqa: E402
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.marketing import MarketingStateError  # noqa: E402
from app.main import app  # noqa: E402
from app.models.marketing import Campaign, MarketingContent, MarketingPlan  # noqa: E402
from app.services.marketing import (  # noqa: E402
    _register_creative_storage_compensation,
)


BUSINESS_ID = UUID("61000000-0000-0000-0000-000000000001")
OTHER_BUSINESS_ID = UUID("62000000-0000-0000-0000-000000000002")
USER_ID = UUID("63000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 8, 23, 12, tzinfo=UTC)


class MarketingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeSession()
        self.membership_role = "member"
        self.original = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def override_access(business_id: UUID):
            if business_id != BUSINESS_ID:
                raise HTTPException(404, "Business not found.")
            return BusinessAccessContext(user=SimpleNamespace(id=USER_ID), business=SimpleNamespace(id=business_id, status="active", currency="USD", timezone="UTC"), membership=SimpleNamespace(business_id=business_id, user_id=USER_ID, status="active", role=self.membership_role))

        self.override_access = override_access
        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_business_access] = override_access
        app.dependency_overrides[get_ai_agent_provider] = lambda: SimpleNamespace(provider_name="test")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original)

    def test_openapi_exposes_authenticated_tenant_marketing_domain(self) -> None:
        paths = {path: value for path, value in app.openapi()["paths"].items() if "/marketing/" in path}
        self.assertGreaterEqual(len(paths), 30)
        for path, operations in paths.items():
            with self.subTest(path=path):
                self.assertTrue(all(operation["security"] for operation in operations.values()))

    async def test_authentication_and_cross_tenant_access_are_denied(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(self._url("campaigns"))
        self.assertEqual(response.status_code, 401)
        app.dependency_overrides[get_business_access] = self.override_access
        response = await self.client.get(self._url("campaigns", OTHER_BUSINESS_ID))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_campaign_list_passes_bounded_filters_and_business(self) -> None:
        with patch("app.api.v1.marketing.service.list_campaigns", new=AsyncMock(return_value=([_campaign()], 1))) as service:
            response = await self.client.get(self._url("campaigns?page=2&page_size=10&search=Summer&status=draft"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["page"], 2)
        self.assertEqual(service.await_args.kwargs["search"], "Summer")

    async def test_campaign_rejects_client_currency_and_derived_budget_total(self) -> None:
        base = {"name": "Summer", "objective": "Grow", "audience_definition": "Customers", "channels": ["instagram"], "planned_budget": "2000"}
        with patch("app.api.v1.marketing.service.create_campaign", new=AsyncMock()) as service:
            for extra in ({"currency": "EUR"}, {"total_allocated": "2000"}):
                response = await self.client.post(self._url("campaigns"), json={**base, **extra})
                self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()

    async def test_invalid_campaign_transition_is_safe_conflict(self) -> None:
        with patch("app.api.v1.marketing.service.change_campaign_status", new=AsyncMock(side_effect=MarketingStateError("private state"))):
            response = await self.client.post(self._url(f"campaigns/{uuid4()}/status"), json={"status": "active"})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private state", response.text)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_content_version_passes_validated_fields_and_tenant_then_commits(self) -> None:
        content_id = uuid4()
        version = _content(
            content_id=uuid4(),
            parent_content_id=content_id,
            root_content_id=content_id,
            title="Revised title",
            body="Revised body",
            cta=None,
            version=2,
        )
        with patch(
            "app.api.v1.marketing.service.create_content_version",
            new=AsyncMock(return_value=version),
        ) as service:
            response = await self.client.post(
                self._url(f"content/{content_id}/versions"),
                json={"title": "Revised title", "body": "Revised body", "cta": None},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["version"], 2)
        self.assertEqual(response.json()["status"], "draft")
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["content_id"], content_id)
        self.assertEqual(service.await_args.kwargs["actor_user_id"], USER_ID)
        data = service.await_args.kwargs["data"]
        self.assertEqual((data.title, data.body, data.cta), ("Revised title", "Revised body", None))
        self.assertEqual(self.session.commit_calls, 1)
        self.assertEqual(self.session.rollback_calls, 0)

    async def test_content_version_persistence_failure_is_safe_and_rolls_back(self) -> None:
        content_id = uuid4()
        with patch(
            "app.api.v1.marketing.service.create_content_version",
            new=AsyncMock(side_effect=SQLAlchemyError("private persistence detail")),
        ):
            response = await self.client.post(
                self._url(f"content/{content_id}/versions"),
                json={"title": "Revised title", "body": "Revised body", "cta": "Review"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private persistence detail", response.text)
        self.assertEqual(self.session.commit_calls, 0)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_member_content_generation_without_offer_remains_allowed(self) -> None:
        content = _content(
            content_id=uuid4(),
            parent_content_id=None,
            root_content_id=uuid4(),
            title="Generated title",
            body="Generated body",
            cta=None,
            version=1,
        )
        with patch(
            "app.api.v1.marketing.service.generate_content",
            new=AsyncMock(return_value=content),
        ) as service:
            response = await self.client.post(
                self._url("content/generate"),
                json={
                    "prompt": "Create an Instagram post",
                    "channel": "instagram",
                    "content_type": "social_post",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertIsNone(service.await_args.kwargs["offer_authorization_role"])
        self.assertEqual(self.session.commit_calls, 1)

    async def test_member_cannot_forge_content_offer_authorization(self) -> None:
        with patch(
            "app.api.v1.marketing.service.generate_content",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._url("content/generate"),
                json={
                    "prompt": "Create an Instagram post",
                    "channel": "instagram",
                    "content_type": "social_post",
                    "offer": "50% off",
                    "offer_authorized": True,
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "permission_missing")
        service.assert_not_awaited()
        self.assertEqual(self.session.commit_calls, 0)

    async def test_owner_and_admin_can_attest_content_offer(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                self.membership_role = role
                self.session = _FakeSession()
                content_id = uuid4()
                content = _content(
                    content_id=content_id,
                    parent_content_id=None,
                    root_content_id=content_id,
                    title="Authorized offer",
                    body="50% off",
                    cta="Explore",
                    version=1,
                )
                with patch(
                    "app.api.v1.marketing.service.generate_content",
                    new=AsyncMock(return_value=content),
                ) as service:
                    response = await self.client.post(
                        self._url("content/generate"),
                        json={
                            "prompt": "Create an Instagram post",
                            "channel": "instagram",
                            "content_type": "social_post",
                            "offer": "50% off",
                            "offer_authorized": True,
                        },
                    )

                self.assertEqual(response.status_code, 201)
                self.assertEqual(
                    service.await_args.kwargs["offer_authorization_role"],
                    role,
                )
                self.assertEqual(self.session.commit_calls, 1)

    async def test_owner_offer_without_attestation_remains_invalid(self) -> None:
        self.membership_role = "owner"
        with patch(
            "app.api.v1.marketing.service.generate_content",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._url("content/generate"),
                json={
                    "prompt": "Create an Instagram post",
                    "channel": "instagram",
                    "content_type": "social_post",
                    "offer": "50% off",
                    "offer_authorized": False,
                },
            )

        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()

    async def test_member_cannot_forge_campaign_offer_authorization(self) -> None:
        with patch(
            "app.api.v1.marketing.service.generate_campaign",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._url("campaigns/generate"),
                json={
                    "goal": "Promote the seasonal offer",
                    "channels": ["instagram"],
                    "offer": "50% off",
                    "offer_authorized": True,
                },
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "permission_missing")
        service.assert_not_awaited()
        self.assertEqual(self.session.commit_calls, 0)

    async def test_owner_and_admin_can_attest_campaign_offer(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                self.membership_role = role
                self.session = _FakeSession()
                campaign = _campaign()
                campaign.offer = "50% off"
                campaign.offer_source = "owner_authorized"
                campaign.offer_authorized = True
                with (
                    patch(
                        "app.api.v1.marketing.service.generate_campaign",
                        new=AsyncMock(return_value=campaign),
                    ) as service,
                    patch(
                        "app.api.v1.marketing.service.campaign_detail",
                        new=AsyncMock(return_value=_campaign_detail(campaign)),
                    ),
                ):
                    response = await self.client.post(
                        self._url("campaigns/generate"),
                        json={
                            "goal": "Promote the seasonal offer",
                            "channels": ["instagram"],
                            "offer": "50% off",
                            "offer_authorized": True,
                        },
                    )

                self.assertEqual(response.status_code, 201)
                self.assertEqual(
                    service.await_args.kwargs["offer_authorization_role"],
                    role,
                )
                self.assertEqual(self.session.commit_calls, 1)

    async def test_performance_rejects_client_derived_metrics(self) -> None:
        payload = {"campaign_id": str(uuid4()), "channel": "instagram", "period_start": "2026-08-01", "period_end": "2026-08-07", "impressions": 100, "clicks": 10, "ctr": "10"}
        with patch("app.api.v1.marketing.service.create_performance", new=AsyncMock()) as service:
            response = await self.client.post(self._url("performance"), json=payload)
        self.assertEqual(response.status_code, 422)
        service.assert_not_awaited()

    async def test_ai_plan_generation_uses_existing_provider_dependency_and_commits(self) -> None:
        plan = _plan()
        with patch("app.api.v1.marketing.service.generate_plan", new=AsyncMock(return_value=plan)) as service:
            response = await self.client.post(self._url("plans/generate"), json={"goal": "Summer", "target_audience": "Customers", "channels": ["instagram"], "budget_guidance": "2000"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["generated_by"], "ai")
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_mutation_materializes_mapped_response_before_commit(self) -> None:
        session = _TrackingAsyncSession()
        plan = _plan()

        async def operation():
            return plan

        result = await marketing_mutate(None, session, operation())

        self.assertIs(result, plan)
        self.assertEqual(session.calls, ["flush", "refresh", "commit"])

    async def test_failed_commit_compensates_pending_creative_storage(self) -> None:
        storage = SimpleNamespace(delete=AsyncMock())
        session = _CommitFailingSession()
        object_key = f"businesses/{BUSINESS_ID}/marketing/creatives/final.png"

        async def operation():
            _register_creative_storage_compensation(session, storage, object_key)
            return SimpleNamespace()

        with self.assertRaises(HTTPException) as raised:
            await marketing_mutate(None, session, operation())

        self.assertEqual(raised.exception.status_code, 503)
        storage.delete.assert_awaited_once_with(object_key)
        self.assertEqual(session.rollback_calls, 1)
        self.assertEqual(session.info, {})

    async def test_unexpected_mutation_failure_compensates_and_propagates(self) -> None:
        storage = SimpleNamespace(delete=AsyncMock())
        session = _CommitFailingSession()
        object_key = f"businesses/{BUSINESS_ID}/marketing/creatives/runtime.png"

        async def operation():
            _register_creative_storage_compensation(session, storage, object_key)
            raise RuntimeError("unexpected materialization failure")

        with self.assertRaisesRegex(
            RuntimeError,
            "unexpected materialization failure",
        ):
            await marketing_mutate(None, session, operation())

        storage.delete.assert_awaited_once_with(object_key)
        self.assertEqual(session.commit_calls, 0)
        self.assertEqual(session.rollback_calls, 1)
        self.assertEqual(session.info, {})

    async def test_calendar_rejects_naive_datetime(self) -> None:
        response = await self.client.post(self._url("calendar"), json={"content_id": str(uuid4()), "scheduled_for": "2026-08-24T10:00:00"})
        self.assertEqual(response.status_code, 422)

    async def test_prepare_campaign_action_uses_authenticated_tenant_and_actor(self) -> None:
        campaign_id = uuid4()
        action_id = uuid4()
        response_value = {
            "id": uuid4(), "business_id": BUSINESS_ID, "entity_type": "campaign",
            "entity_id": campaign_id, "channel": "meta", "connector_type": "meta_ads",
            "execution_id": uuid4(), "ai_action_id": action_id,
            "action_type": "create_meta_campaign", "action_status": "pending_approval",
            "policy_decision": "require_approval", "policy_reason_code": "human_approval_required",
            "approval_id": uuid4(), "approval_status": "pending",
            "connector_state": "provider_disabled",
            "connector_message": "Authenticated writes are not implemented.",
            "created_at": NOW, "updated_at": NOW,
        }
        with patch(
            "app.api.v1.marketing.prepare_campaign_action",
            new=AsyncMock(return_value=response_value),
        ) as service:
            response = await self.client.post(
                self._url(f"campaigns/{campaign_id}/prepare-action"), json={"channel": "meta"},
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["action_status"], "pending_approval")
        self.assertEqual(response.json()["connector_state"], "provider_disabled")
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["requested_by_user_id"], USER_ID)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_candidate_decision_is_tenant_scoped(self) -> None:
        candidate_id = uuid4()
        value = {
            "id": candidate_id, "business_id": BUSINESS_ID, "discovery_run_id": uuid4(),
            "competitor_id": None, "name": "Sourced candidate", "website_domain": None,
            "canonical_url": None, "source": "research_provider", "discovery_reason": "Overlap",
            "confidence": "0.700", "industry_relationship": None,
            "geographic_relationship": None, "status": "dismissed",
            "discovered_at": NOW, "last_seen_at": NOW, "created_at": NOW, "updated_at": NOW,
        }
        with patch(
            "app.api.v1.marketing.change_candidate_status", new=AsyncMock(return_value=value),
        ) as service:
            response = await self.client.post(
                self._url(f"competitor-candidates/{candidate_id}/status"),
                json={"status": "dismissed"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["actor_user_id"], USER_ID)

    @staticmethod
    def _url(path: str, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/marketing/{path}"


class _FakeSession:
    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


class _CommitFailingSession(_FakeSession):
    def __init__(self):
        super().__init__()
        self.info = {}

    async def commit(self):
        self.commit_calls += 1
        raise SQLAlchemyError("private commit failure")


class _TrackingAsyncSession(AsyncSession):
    """Minimal AsyncSession double for the pre-serialization boundary."""

    def __init__(self):
        self.calls: list[str] = []

    async def flush(self, objects=None):
        self.calls.append("flush")

    async def refresh(self, instance, attribute_names=None, with_for_update=None):
        self.calls.append("refresh")

    async def commit(self):
        self.calls.append("commit")

    async def rollback(self):
        self.calls.append("rollback")


def _campaign() -> Campaign:
    return Campaign(id=uuid4(), business_id=BUSINESS_ID, marketing_plan_id=None, audience_id=None, name="Summer", objective="Grow", description=None, offer=None, audience_definition="Customers", geographic_targeting=[], channels=["instagram"], start_date=None, end_date=None, planned_budget=Decimal("2000"), currency="USD", budget_mode="lifetime", status="draft", created_by_user_id=USER_ID, ai_generated=False, created_at=NOW, updated_at=NOW)


def _campaign_detail(campaign: Campaign) -> dict[str, object]:
    value = {
        column.name: getattr(campaign, column.name)
        for column in campaign.__table__.columns
    }
    value["channel_plans"] = []
    value["catalog_item_ids"] = []
    return value


def _plan() -> MarketingPlan:
    return MarketingPlan(id=uuid4(), business_id=BUSINESS_ID, audience_id=None, title="Summer", objective="Grow", target_audience="Customers", positioning="Grounded", key_message="Quality", offer=None, channels=["instagram"], budget_guidance=Decimal("2000"), currency="USD", period_start=None, period_end=None, content_strategy="Useful content", measurement_goals=["Conversions"], status="ready", generated_by="ai", created_by_user_id=USER_ID, created_at=NOW, updated_at=NOW)


def _content(
    *,
    content_id: UUID,
    parent_content_id: UUID | None,
    root_content_id: UUID,
    title: str,
    body: str,
    cta: str | None,
    version: int,
) -> MarketingContent:
    return MarketingContent(
        id=content_id,
        business_id=BUSINESS_ID,
        campaign_id=None,
        channel="instagram",
        content_type="social_post",
        title=title,
        body=body,
        cta=cta,
        language="en",
        status="draft",
        ai_generated=False,
        version=version,
        parent_content_id=parent_content_id,
        root_content_id=root_content_id,
        created_by_user_id=USER_ID,
        proposal_key=None,
        creative_brief="Trusted brief",
        generation_reasoning="Owner revision",
        recommended_for="Instagram",
        source_evidence=[],
        created_at=NOW,
        updated_at=NOW,
    )
