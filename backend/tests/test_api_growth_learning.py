from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException

os.environ.setdefault(
    "AIBOS_DATABASE_URL", "postgresql+asyncpg://database.invalid/test"
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.api.dependencies.business import (  # noqa: E402
    BusinessAccessContext,
    get_business_access,
)
from app.db.session import get_db_session  # noqa: E402
from app.exceptions.growth_learning import GrowthLearningStateError  # noqa: E402
from app.main import app  # noqa: E402


BUSINESS_ID = UUID("73000000-0000-0000-0000-000000000003")
OTHER_BUSINESS_ID = UUID("74000000-0000-0000-0000-000000000004")
USER_ID = UUID("75000000-0000-0000-0000-000000000005")
NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


class GrowthLearningApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.session = _FakeSession()
        self.original = app.dependency_overrides.copy()

        async def override_session():
            yield self.session

        async def owner_access(business_id: UUID):
            if business_id != BUSINESS_ID:
                raise HTTPException(404, "Business not found.")
            return _access("owner")

        self.owner_access = owner_access
        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_business_access] = owner_access
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original)

    def test_openapi_exposes_tenant_auth_and_no_fake_outcome_inputs(self) -> None:
        document = app.openapi()
        paths = {
            path: operations
            for path, operations in document["paths"].items()
            if "/growth/" in path
        }
        self.assertEqual(len(paths), 8)
        for operations in paths.values():
            self.assertTrue(
                all(operation["security"] for operation in operations.values())
            )
        create_schema = document["components"]["schemas"]["GrowthExperimentCreate"]
        properties = create_schema["properties"]
        self.assertNotIn("currency", properties)
        self.assertNotIn("predicted_uplift", properties)
        self.assertNotIn("result", properties)
        self.assertNotIn("confidence", properties)
        self.assertFalse(
            {"api_key", "access_token", "refresh_token", "provider_credentials"}
            & set(properties)
        )

    async def test_authentication_cross_tenant_and_member_writes_are_denied(self) -> None:
        del app.dependency_overrides[get_business_access]
        response = await self.client.get(self._url("experiments"))
        self.assertEqual(response.status_code, 401)

        app.dependency_overrides[get_business_access] = self.owner_access
        response = await self.client.get(
            self._url("experiments", business_id=OTHER_BUSINESS_ID)
        )
        self.assertEqual(response.status_code, 404)

        async def member_access(business_id: UUID):
            self.assertEqual(business_id, BUSINESS_ID)
            return _access("member")

        app.dependency_overrides[get_business_access] = member_access
        with patch(
            "app.api.v1.growth_learning.service.create_growth_experiment",
            new=AsyncMock(),
        ) as service:
            response = await self.client.post(
                self._url("experiments"), json=_create_payload()
            )
        self.assertEqual(response.status_code, 403)
        service.assert_not_awaited()

    async def test_owner_creates_tenant_scoped_definition_and_commits(self) -> None:
        value = _experiment()
        with patch(
            "app.api.v1.growth_learning.service.create_growth_experiment",
            new=AsyncMock(return_value=value),
        ) as service:
            response = await self.client.post(
                self._url("experiments"), json=_create_payload()
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "draft")
        self.assertEqual(service.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(service.await_args.kwargs["actor_user_id"], USER_ID)
        self.assertEqual(self.session.commit_calls, 1)

    async def test_invalid_control_and_client_claims_fail_before_service(self) -> None:
        for mutate in (
            lambda payload: payload["variants"][1].update({"is_control": True}),
            lambda payload: payload.update({"predicted_uplift": "0.5"}),
            lambda payload: payload.update({"currency": "EUR"}),
        ):
            payload = _create_payload()
            mutate(payload)
            with patch(
                "app.api.v1.growth_learning.service.create_growth_experiment",
                new=AsyncMock(),
            ) as service:
                response = await self.client.post(
                    self._url("experiments"), json=payload
                )
            self.assertEqual(response.status_code, 422)
            service.assert_not_awaited()

    async def test_lifecycle_conflict_is_safe_and_rolls_back(self) -> None:
        with patch(
            "app.api.v1.growth_learning.service.start_growth_experiment",
            new=AsyncMock(side_effect=GrowthLearningStateError("private state")),
        ):
            response = await self.client.post(
                self._url(f"experiments/{uuid4()}/start")
            )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private state", response.text)
        self.assertEqual(self.session.rollback_calls, 1)

    async def test_list_is_bounded_and_evaluation_uses_authenticated_actor(self) -> None:
        value = _experiment(status="evaluated", result=_result())
        with patch(
            "app.api.v1.growth_learning.service.list_growth_experiments",
            new=AsyncMock(return_value=([value], 1)),
        ) as listing:
            response = await self.client.get(
                self._url("experiments?page=2&page_size=10&status=evaluated")
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 1)
        self.assertEqual(listing.await_args.kwargs["page"], 2)
        self.assertEqual(listing.await_args.kwargs["page_size"], 10)
        self.assertEqual(listing.await_args.kwargs["status"], "evaluated")

        result = _result()
        with patch(
            "app.api.v1.growth_learning.service.evaluate_growth_experiment",
            new=AsyncMock(return_value=result),
        ) as evaluation:
            response = await self.client.post(
                self._url(f"experiments/{result['experiment_id']}/evaluate")
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["classification"], "observed_directional_difference"
        )
        self.assertEqual(evaluation.await_args.kwargs["business_id"], BUSINESS_ID)
        self.assertEqual(evaluation.await_args.kwargs["actor_user_id"], USER_ID)

    @staticmethod
    def _url(path: str, business_id: UUID = BUSINESS_ID) -> str:
        return f"/api/v1/businesses/{business_id}/growth/{path}"


class _FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self):
        self.commit_calls += 1

    async def rollback(self):
        self.rollback_calls += 1


def _access(role: str) -> BusinessAccessContext:
    return BusinessAccessContext(
        user=type("User", (), {"id": USER_ID})(),
        business=type(
            "Business",
            (),
            {
                "id": BUSINESS_ID,
                "status": "active",
                "currency": "USD",
                "timezone": "UTC",
            },
        )(),
        membership=type(
            "Membership",
            (),
            {
                "business_id": BUSINESS_ID,
                "user_id": USER_ID,
                "status": "active",
                "role": role,
            },
        )(),
    )


def _create_payload() -> dict[str, object]:
    return {
        "name": "Provider CTR comparison",
        "hypothesis": "The challenger may show a higher provider CTR.",
        "learning_key": "meta_creative_family",
        "experiment_type": "campaign",
        "primary_metric": "ctr",
        "attribution_classification": "provider_attributed",
        "evaluation_window_days": 14,
        "minimum_sample_size": 1_000,
        "variants": [
            {
                "variant_key": "control",
                "label": "Control",
                "is_control": True,
                "campaign_id": str(uuid4()),
            },
            {
                "variant_key": "challenger",
                "label": "Challenger",
                "is_control": False,
                "campaign_id": str(uuid4()),
            },
        ],
    }


def _experiment(
    *, status: str = "draft", result: dict[str, object] | None = None
) -> dict[str, object]:
    experiment_id = uuid4()
    variants = [
        {
            "id": uuid4(),
            "business_id": BUSINESS_ID,
            "experiment_id": experiment_id,
            "variant_key": "control",
            "label": "Control",
            "is_control": True,
            "campaign_id": uuid4(),
            "content_id": None,
            "created_at": NOW,
            "updated_at": NOW,
        },
        {
            "id": uuid4(),
            "business_id": BUSINESS_ID,
            "experiment_id": experiment_id,
            "variant_key": "challenger",
            "label": "Challenger",
            "is_control": False,
            "campaign_id": uuid4(),
            "content_id": None,
            "created_at": NOW,
            "updated_at": NOW,
        },
    ]
    if result is not None:
        result = result | {"experiment_id": experiment_id}
    return {
        "id": experiment_id,
        "business_id": BUSINESS_ID,
        "name": "Provider CTR comparison",
        "hypothesis": "The challenger may show a higher provider CTR.",
        "learning_key": "meta_creative_family",
        "experiment_type": "campaign",
        "status": status,
        "primary_metric": "ctr",
        "attribution_classification": "provider_attributed",
        "currency": "USD",
        "evaluation_window_days": 14,
        "minimum_sample_size": 1_000,
        "definition_version": 1,
        "source_opportunity_id": None,
        "source_ai_action_id": None,
        "created_by_user_id": USER_ID,
        "measurement_start": None if status == "draft" else NOW,
        "measurement_end": None if status == "draft" else NOW,
        "evaluation_cutoff": NOW if status == "evaluated" else None,
        "completed_at": NOW if status == "evaluated" else None,
        "canceled_at": None,
        "variants": variants,
        "result": result,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _result() -> dict[str, object]:
    experiment_id = uuid4()
    return {
        "id": uuid4(),
        "business_id": BUSINESS_ID,
        "experiment_id": experiment_id,
        "classification": "observed_directional_difference",
        "primary_metric": "ctr",
        "attribution_classification": "provider_attributed",
        "currency": "USD",
        "control_value": "2.000000",
        "directional_leader_value": "4.000000",
        "absolute_difference": "2.000000",
        "relative_difference": "1.000000",
        "evidence_quality": "0.980",
        "directional_leader_variant_id": uuid4(),
        "directional_leader_key": "challenger",
        "learning_memory_id": uuid4(),
        "measurement_start": NOW,
        "measurement_end": NOW,
        "evaluation_cutoff": NOW,
        "evaluated_at": NOW,
        "evaluation_revision": "a" * 64,
        "evidence": {
            "formula_version": "growth-evaluation-v1",
            "variant_metrics": [],
            "statistical_significance_test": None,
            "causal_claim_allowed": False,
        },
        "created_at": NOW,
        "updated_at": NOW,
    }
