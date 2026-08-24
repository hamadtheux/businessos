from enum import StrEnum


class AutonomyMode(StrEnum):
    """Typed policy input for future tenant autonomy configuration."""

    MANUAL = "manual"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"
