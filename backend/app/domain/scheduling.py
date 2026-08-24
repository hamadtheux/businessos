from enum import StrEnum


class AvailabilityExceptionKind(StrEnum):
    UNAVAILABLE = "unavailable"
    AVAILABLE_OVERRIDE = "available_override"


class AppointmentStatus(StrEnum):
    CONFIRMED = "confirmed"
    CANCELED = "canceled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class AppointmentSource(StrEnum):
    MANUAL = "manual"
    API = "api"
    AI = "ai"
    WEBSITE = "website"
    WHATSAPP = "whatsapp"
    IMPORT = "import"


ACTIVE_APPOINTMENT_STATUSES = frozenset({AppointmentStatus.CONFIRMED})
APPOINTMENT_SOURCES = frozenset(AppointmentSource)
TERMINAL_APPOINTMENT_STATUSES = frozenset(
    {
        AppointmentStatus.CANCELED,
        AppointmentStatus.COMPLETED,
        AppointmentStatus.NO_SHOW,
    }
)

APPOINTMENT_CANCELLATION_REASON_CODES = frozenset(
    {
        "customer_request",
        "provider_unavailable",
        "business_closed",
        "duplicate_booking",
        "scheduling_error",
        "other",
    }
)
