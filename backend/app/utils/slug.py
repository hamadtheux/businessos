import re
import unicodedata
from uuid import UUID


_UNSAFE_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+", flags=re.ASCII)


def create_slug_base(value: str, *, max_length: int = 120) -> str:
    """Return a deterministic, URL-safe slug with a non-empty fallback."""
    if max_length < 1:
        raise ValueError("max_length must be positive")

    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", errors="ignore")
        .decode("ascii")
        .lower()
    )
    slug = _UNSAFE_SLUG_CHARACTERS.sub("-", ascii_value).strip("-")
    slug = slug[:max_length].rstrip("-")
    return slug or "business"[:max_length]


def add_uuid_slug_suffix(
    base_slug: str,
    business_id: UUID,
    *,
    max_length: int = 120,
    suffix_length: int = 12,
) -> str:
    """Add a compact deterministic UUID suffix without exceeding the limit."""
    if suffix_length < 1 or suffix_length > 32:
        raise ValueError("suffix_length must be between 1 and 32")
    if max_length <= suffix_length:
        raise ValueError("max_length must leave room for a slug prefix")

    suffix = business_id.hex[:suffix_length]
    prefix_limit = max_length - suffix_length - 1
    prefix = create_slug_base(base_slug, max_length=prefix_limit)
    return f"{prefix}-{suffix}"
