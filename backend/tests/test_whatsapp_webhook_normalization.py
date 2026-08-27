from __future__ import annotations

import os
import unittest

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.integration import IntegrationProviderUnavailableError  # noqa: E402
from app.integrations.oauth_adapters import (  # noqa: E402
    _normalize_whatsapp_status_events,
)


WAMID = "wamid.HBgMNTU1MjM0NTY3ODkwFQIAERgSQUJDREVGRw=="


def _meta_payload(statuses: list[dict[str, object]]) -> dict[str, object]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": "999999999",
                            },
                            "statuses": statuses,
                        },
                    }
                ],
            }
        ],
    }


class WhatsAppWebhookNormalizationTests(unittest.TestCase):
    def test_real_meta_statuses_array_fans_out_every_evidenced_transition(
        self,
    ) -> None:
        payload = _meta_payload(
            [
                {
                    "id": WAMID,
                    "status": "sent",
                    "timestamp": "1787779200",
                    "recipient_id": "923001234567",
                    "conversation": {
                        "id": "conversation-provider-value",
                        "origin": {"type": "service"},
                    },
                    "pricing": {
                        "billable": True,
                        "pricing_model": "CBP",
                        "category": "service",
                    },
                },
                {
                    "id": WAMID,
                    "status": "delivered",
                    "timestamp": "1787779260",
                    "recipient_id": "923001234567",
                },
                {
                    "id": WAMID,
                    "status": "read",
                    "timestamp": "1787779320",
                    "recipient_id": "923001234567",
                },
                {
                    "id": WAMID,
                    "status": "failed",
                    "timestamp": "1787779380",
                    "recipient_id": "923001234567",
                    "errors": [
                        {
                            "code": 131047,
                            "title": "Provider failure detail",
                        }
                    ],
                },
            ]
        )

        events = _normalize_whatsapp_status_events(payload)

        self.assertEqual(len(events), 4)
        self.assertEqual(
            [event.event_type for event in events],
            ["message_status_updated"] * 4,
        )
        self.assertEqual(
            [event.safe_payload["delivery_status"] for event in events],
            ["sent", "delivered", "read", "failed"],
        )

        for event in events:
            self.assertEqual(
                event.safe_payload["external_message_reference"],
                WAMID,
            )
            self.assertTrue(
                str(event.safe_payload["external_message_reference"]).endswith(
                    "=="
                )
            )

            # The normalized contract deliberately excludes raw recipient,
            # pricing, conversation, and provider-error details.
            self.assertEqual(
                set(event.safe_payload),
                {
                    "external_message_reference",
                    "delivery_status",
                },
            )

        self.assertEqual(
            len({event.external_event_id for event in events}),
            4,
        )
        self.assertEqual(
            [event.occurred_at.timestamp() for event in events],
            [
                1787779200.0,
                1787779260.0,
                1787779320.0,
                1787779380.0,
            ],
        )

    def test_same_provider_evidence_has_stable_dedupe_identity(self) -> None:
        payload = _meta_payload(
            [
                {
                    "id": WAMID,
                    "status": "delivered",
                    "timestamp": "1787779260",
                }
            ]
        )

        first = _normalize_whatsapp_status_events(payload)
        second = _normalize_whatsapp_status_events(payload)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(
            first[0].external_event_id,
            second[0].external_event_id,
        )

    def test_same_message_can_emit_distinct_delivery_transitions(self) -> None:
        events = _normalize_whatsapp_status_events(
            _meta_payload(
                [
                    {
                        "id": WAMID,
                        "status": "sent",
                        "timestamp": "1787779200",
                    },
                    {
                        "id": WAMID,
                        "status": "delivered",
                        "timestamp": "1787779260",
                    },
                ]
            )
        )

        self.assertEqual(len(events), 2)
        self.assertNotEqual(
            events[0].external_event_id,
            events[1].external_event_id,
        )

    def test_unknown_provider_status_fails_closed(self) -> None:
        with self.assertRaises(IntegrationProviderUnavailableError):
            _normalize_whatsapp_status_events(
                _meta_payload(
                    [
                        {
                            "id": WAMID,
                            "status": "mystery_state",
                            "timestamp": "1787779200",
                        }
                    ]
                )
            )

    def test_missing_or_invalid_message_reference_fails_closed(self) -> None:
        for statuses in (
            [
                {
                    "status": "delivered",
                    "timestamp": "1787779200",
                }
            ],
            [
                {
                    "id": "",
                    "status": "delivered",
                    "timestamp": "1787779200",
                }
            ],
        ):
            with (
                self.subTest(statuses=statuses),
                self.assertRaises(IntegrationProviderUnavailableError),
            ):
                _normalize_whatsapp_status_events(
                    _meta_payload(statuses)
                )

    def test_evidenced_status_with_invalid_timestamp_fails_closed(self) -> None:
        with self.assertRaises(IntegrationProviderUnavailableError):
            _normalize_whatsapp_status_events(
                _meta_payload(
                    [
                        {
                            "id": WAMID,
                            "status": "delivered",
                            "timestamp": "not-a-provider-timestamp",
                        }
                    ]
                )
            )

    def test_more_than_100_canonical_status_events_fails_closed(self) -> None:
        statuses = [
            {
                "id": f"wamid.{index}",
                "status": "delivered",
                "timestamp": str(1787779200 + index),
            }
            for index in range(101)
        ]

        with self.assertRaises(IntegrationProviderUnavailableError):
            _normalize_whatsapp_status_events(_meta_payload(statuses))

    def test_present_but_invalid_status_container_fails_closed(self) -> None:
        for invalid_statuses in ([], None, "not-a-status-array"):
            payload = _meta_payload([])
            value = payload["entry"][0]["changes"][0]["value"]
            value["statuses"] = invalid_statuses

            with (
                self.subTest(statuses=invalid_statuses),
                self.assertRaises(IntegrationProviderUnavailableError),
            ):
                _normalize_whatsapp_status_events(payload)

    def test_more_than_100_entries_fails_closed(self) -> None:
        payload = _meta_payload(
            [
                {
                    "id": WAMID,
                    "status": "delivered",
                    "timestamp": "1787779200",
                }
            ]
        )
        payload["entry"] = payload["entry"] * 101

        with self.assertRaises(IntegrationProviderUnavailableError):
            _normalize_whatsapp_status_events(payload)

    def test_more_than_100_changes_fails_closed(self) -> None:
        payload = _meta_payload(
            [
                {
                    "id": WAMID,
                    "status": "delivered",
                    "timestamp": "1787779200",
                }
            ]
        )
        entry = payload["entry"][0]
        entry["changes"] = entry["changes"] * 101

        with self.assertRaises(IntegrationProviderUnavailableError):
            _normalize_whatsapp_status_events(payload)

    def test_irrelevant_non_status_whatsapp_payload_returns_no_status_events(
        self,
    ) -> None:
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "messages": [
                                    {
                                        "id": "wamid.inbound",
                                        "from": "923001234567",
                                        "timestamp": "1787779200",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

        self.assertEqual(
            _normalize_whatsapp_status_events(payload),
            (),
        )


if __name__ == "__main__":
    unittest.main()
