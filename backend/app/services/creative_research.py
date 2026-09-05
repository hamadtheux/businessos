from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from collections import Counter, OrderedDict, defaultdict, deque
from datetime import UTC, datetime
from ipaddress import ip_address
from time import monotonic
from typing import Literal, Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.business_industries import get_business_industry


logger = logging.getLogger("aibos.creative_research")

ResearchIndustry = Literal[
    "agriculture",
    "commerce",
    "healthcare",
    "professional services",
    "real estate",
    "retail",
    "technology",
    "small business",
]
ResearchObjective = Literal[
    "brand awareness",
    "event promotion",
    "lead generation",
    "product launch",
    "promotional offer",
]
ResearchFormat = Literal[
    "display banner",
    "landscape ad",
    "social square",
    "story vertical",
]
ResearchStyle = Literal[
    "bold commercial",
    "minimal editorial",
    "premium modern",
    "product led",
    "trust focused",
]

_PUBLIC_CHANNELS = frozenset(
    {"facebook", "instagram", "linkedin", "other", "tiktok"}
)
_TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "source",
    }
)
_CLONE_LANGUAGE = re.compile(
    r"\b(copy (?:this|the|source|design|layout|artwork)|clone|duplicate|replicate|reproduce|pixel[- ]perfect|exactly like)\b",
    re.IGNORECASE,
)


class ResearchSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CreativeResearchRequest(ResearchSchema):
    queries: tuple[str, ...] = Field(min_length=1, max_length=5)
    industry: ResearchIndustry
    channel: str = Field(min_length=1, max_length=24)
    campaign_objective: ResearchObjective
    creative_format: ResearchFormat
    style_family: ResearchStyle
    max_results: int = Field(ge=4, le=15)
    cache_key: str = Field(min_length=16, max_length=64)

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        normalized = value.casefold()
        if normalized not in _PUBLIC_CHANNELS:
            return "other"
        return normalized

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 220 for value in values):
            raise ValueError("research queries must be bounded")
        return values


class CreativeInspirationInsight(ResearchSchema):
    source_domain: str = Field(min_length=1, max_length=253)
    source_url: str = Field(min_length=8, max_length=1024)
    title: str = Field(min_length=1, max_length=240)
    relevance_score: int = Field(ge=0, le=100)
    layout_pattern: str = Field(min_length=1, max_length=160)
    hierarchy_pattern: str = Field(min_length=1, max_length=160)
    visual_style: str = Field(min_length=1, max_length=160)
    focal_point_strategy: str = Field(min_length=1, max_length=200)
    negative_space_strategy: str = Field(min_length=1, max_length=200)
    typography_character: str = Field(min_length=1, max_length=160)
    color_relationship: str = Field(min_length=1, max_length=160)
    cta_treatment: str = Field(min_length=1, max_length=160)
    offer_treatment: str = Field(min_length=1, max_length=160)
    imagery_style: str = Field(min_length=1, max_length=180)
    composition_density: str = Field(min_length=1, max_length=120)
    platform_fit: str = Field(min_length=1, max_length=160)
    reusable_design_principles: tuple[str, ...] = Field(
        min_length=1,
        max_length=6,
    )

    @field_validator(
        "layout_pattern",
        "hierarchy_pattern",
        "visual_style",
        "focal_point_strategy",
        "negative_space_strategy",
        "typography_character",
        "color_relationship",
        "cta_treatment",
        "offer_treatment",
        "imagery_style",
        "composition_density",
        "platform_fit",
    )
    @classmethod
    def reject_clone_language(cls, value: str) -> str:
        if _CLONE_LANGUAGE.search(value):
            raise ValueError("inspiration must contain abstract design principles")
        return value

    @field_validator("reusable_design_principles")
    @classmethod
    def validate_principles(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 180
            or _CLONE_LANGUAGE.search(value)
            for value in values
        ):
            raise ValueError("inspiration principles must be bounded and original")
        return values


class CreativeResearchResult(ResearchSchema):
    provider: str = Field(min_length=1, max_length=64)
    references: tuple[CreativeInspirationInsight, ...] = Field(
        default_factory=tuple,
        max_length=15,
    )


class CreativeResearchBundle(ResearchSchema):
    research_query_themes: tuple[str, ...] = Field(max_length=5)
    reference_count: int = Field(ge=0, le=15)
    reference_domains: tuple[str, ...] = Field(max_length=15)
    dominant_patterns: tuple[str, ...] = Field(max_length=6)
    emerging_patterns: tuple[str, ...] = Field(max_length=6)
    recommended_visual_directions: tuple[str, ...] = Field(max_length=6)
    avoid_patterns: tuple[str, ...] = Field(max_length=8)
    originality_constraints: tuple[str, ...] = Field(max_length=6)
    selected_reference_insights: tuple[CreativeInspirationInsight, ...] = Field(
        max_length=8,
    )
    research_timestamp: datetime
    research_fingerprint: str = Field(min_length=16, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    degraded: bool = False
    cache_hit: bool = False


class PublicCreativeResearchContext(ResearchSchema):
    """Closed public vocabulary produced by the private-to-public classifier."""

    industry: ResearchIndustry
    channel: str
    campaign_objective: ResearchObjective
    creative_format: ResearchFormat
    style_family: ResearchStyle

    @field_validator("channel")
    @classmethod
    def validate_public_channel(cls, value: str) -> str:
        normalized = value.casefold()
        if normalized not in _PUBLIC_CHANNELS:
            raise ValueError("public research channel is not allowlisted")
        return normalized


@runtime_checkable
class CreativeResearchProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    async def search(
        self,
        request: CreativeResearchRequest,
    ) -> CreativeResearchResult: ...


class CreativeResearchProviderError(RuntimeError):
    """A safe research-provider failure with no raw provider response."""


class CreativeResearchProviderUnavailable(CreativeResearchProviderError):
    pass


class UnavailableCreativeResearchProvider:
    provider_name = "unconfigured"

    async def search(
        self,
        request: CreativeResearchRequest,
    ) -> CreativeResearchResult:
        del request
        raise CreativeResearchProviderUnavailable(
            "Creative research provider is unavailable"
        )


class CreativeResearchCache:
    """
    Bounded process-local cache for generic public research only.

    Keys are generated exclusively from whitelisted public dimensions. There is
    deliberately no business_id, owner copy, Business Brain text, CRM data, or
    tenant-specific strategy content in this cache.
    """

    def __init__(self, *, ttl_seconds: int, max_entries: int = 256) -> None:
        if not 60 <= ttl_seconds <= 604_800:
            raise ValueError("creative research cache TTL is invalid")
        if not 1 <= max_entries <= 2048:
            raise ValueError("creative research cache size is invalid")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._values: OrderedDict[
            str,
            tuple[float, CreativeResearchBundle],
        ] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> CreativeResearchBundle | None:
        async with self._lock:
            cached = self._values.get(key)
            if cached is None:
                return None
            expires_at, bundle = cached
            if expires_at <= monotonic():
                self._values.pop(key, None)
                return None
            self._values.move_to_end(key)
            return bundle.model_copy(update={"cache_hit": True})

    async def put(self, key: str, bundle: CreativeResearchBundle) -> None:
        async with self._lock:
            self._values[key] = (
                monotonic() + self._ttl_seconds,
                bundle.model_copy(update={"cache_hit": False}),
            )
            self._values.move_to_end(key)
            while len(self._values) > self._max_entries:
                self._values.popitem(last=False)


class CreativeResearchEngine:
    def __init__(
        self,
        *,
        provider: CreativeResearchProvider,
        cache: CreativeResearchCache,
        timeout_seconds: float,
        max_results: int,
    ) -> None:
        if not 1 <= timeout_seconds <= 60:
            raise ValueError("creative research timeout is invalid")
        if not 4 <= max_results <= 15:
            raise ValueError("creative research result limit is invalid")
        self._provider = provider
        self._cache = cache
        self._timeout_seconds = timeout_seconds
        self._max_results = max_results

    async def research(
        self,
        context: PublicCreativeResearchContext,
    ) -> CreativeResearchBundle:
        request = build_research_request(context, max_results=self._max_results)
        cached = await self._cache.get(request.cache_key)
        if cached is not None:
            return cached

        safe_provider = _safe_provider_name(self._provider.provider_name)
        logger.info(
            "creative_research_started provider=%s",
            safe_provider,
            extra={"provider": safe_provider},
        )
        try:
            result = await asyncio.wait_for(
                self._provider.search(request),
                timeout=self._timeout_seconds,
            )
            bundle = synthesize_research_bundle(request, result)
        except Exception:
            # Research is an enhancement. Any provider/normalization failure
            # degrades to the internal library without exposing exception text
            # or making visual generation unavailable.
            bundle = degraded_research_bundle(
                request,
                provider=safe_provider,
            )
            logger.warning(
                "creative_research_degraded provider=%s",
                safe_provider,
                extra={"provider": safe_provider},
            )
            return bundle

        await self._cache.put(request.cache_key, bundle)
        logger.info(
            "creative_research_succeeded provider=%s result_count=%d domain_count=%d",
            safe_provider,
            bundle.reference_count,
            len(bundle.reference_domains),
            extra={
                "provider": safe_provider,
                "result_count": bundle.reference_count,
                "domain_count": len(bundle.reference_domains),
            },
        )
        return bundle


def derive_public_research_context(
    *,
    business_type: str,
    channel: str,
    asset_type: str,
    strategy_text: str,
    visual_text: str,
) -> PublicCreativeResearchContext:
    """
    Convert tenant-private context into a closed vocabulary before search.

    The free text is inspected only for classification. It is never copied,
    interpolated, hashed, cached, logged, or returned in a search request.
    """
    return PublicCreativeResearchContext(
        industry=_generalize_industry(business_type),
        channel=_generalize_channel(channel),
        campaign_objective=_classify_objective(strategy_text),
        creative_format=_generalize_format(asset_type),
        style_family=_classify_style(visual_text),
    )


def build_research_request(
    context: PublicCreativeResearchContext,
    *,
    max_results: int,
) -> CreativeResearchRequest:
    if not 4 <= max_results <= 15:
        raise ValueError("creative research result limit is invalid")
    dimensions = (
        context.industry,
        context.channel,
        context.campaign_objective,
        context.creative_format,
        context.style_family,
    )
    base = " ".join(dimensions)
    queries = (
        f"{base} contemporary advertising design",
        f"site:dribbble.com {base} campaign design",
        f"site:behance.net {base} brand campaign",
        f"Awwwards CSS Design Awards {context.industry} {context.style_family} campaign",
        f"{base} Meta Ad Library TikTok Creative Center Canva inspiration",
    )
    cache_key = hashlib.sha256("\x1f".join(dimensions).encode()).hexdigest()
    return CreativeResearchRequest(
        queries=queries,
        industry=context.industry,
        channel=context.channel,
        campaign_objective=context.campaign_objective,
        creative_format=context.creative_format,
        style_family=context.style_family,
        max_results=max_results,
        cache_key=cache_key,
    )


def normalize_references(
    references: tuple[CreativeInspirationInsight, ...]
    | list[CreativeInspirationInsight],
    *,
    max_results: int,
    permitted_source_urls: set[str] | None = None,
) -> tuple[CreativeInspirationInsight, ...]:
    """Validate, deduplicate, and select references with domain diversity."""
    normalized: list[CreativeInspirationInsight] = []
    seen_urls: set[str] = set()
    seen_campaigns: set[str] = set()
    allowed: set[str] | None = None
    if permitted_source_urls is not None:
        allowed = set()
        for value in permitted_source_urls:
            try:
                allowed.add(sanitize_reference_url(value))
            except ValueError:
                continue
    for reference in references:
        try:
            url = sanitize_reference_url(reference.source_url)
        except ValueError:
            continue
        if allowed is not None and url not in allowed:
            continue
        domain = urlsplit(url).hostname or ""
        campaign_key = re.sub(r"[^a-z0-9]+", " ", reference.title.casefold()).strip()
        campaign_fingerprint = f"{domain}:{campaign_key[:120]}"
        if url in seen_urls or campaign_fingerprint in seen_campaigns:
            continue
        seen_urls.add(url)
        seen_campaigns.add(campaign_fingerprint)
        normalized.append(
            reference.model_copy(
                update={"source_url": url, "source_domain": domain}
            )
        )

    normalized.sort(key=lambda item: (-item.relevance_score, item.source_url))
    by_domain: dict[str, deque[CreativeInspirationInsight]] = defaultdict(deque)
    for reference in normalized:
        by_domain[reference.source_domain].append(reference)
    ordered_domains = sorted(
        by_domain,
        key=lambda domain: (
            -by_domain[domain][0].relevance_score,
            domain,
        ),
    )
    per_domain_limit = max(2, math.ceil(max_results / 4))
    selected: list[CreativeInspirationInsight] = []
    selected_per_domain: Counter[str] = Counter()
    while ordered_domains and len(selected) < max_results:
        next_domains: list[str] = []
        for domain in ordered_domains:
            if len(selected) >= max_results:
                break
            if selected_per_domain[domain] >= per_domain_limit:
                continue
            queue = by_domain[domain]
            if queue:
                selected.append(queue.popleft())
                selected_per_domain[domain] += 1
            if queue and selected_per_domain[domain] < per_domain_limit:
                next_domains.append(domain)
        ordered_domains = next_domains
    return tuple(selected)


def synthesize_research_bundle(
    request: CreativeResearchRequest,
    result: CreativeResearchResult,
) -> CreativeResearchBundle:
    references = normalize_references(
        result.references,
        max_results=request.max_results,
    )
    selected = references[:8]
    pattern_counts = Counter(
        value.casefold()
        for reference in selected
        for value in (
            reference.layout_pattern,
            reference.hierarchy_pattern,
            reference.visual_style,
        )
    )
    ranked_patterns = [
        value
        for value, _count in sorted(
            pattern_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    principles = _unique_bounded(
        principle
        for reference in selected
        for principle in reference.reusable_design_principles
    )
    fingerprint_material = "\x1f".join(
        [request.cache_key, *(reference.source_url for reference in selected)]
    )
    return CreativeResearchBundle(
        research_query_themes=request.queries,
        reference_count=len(references),
        reference_domains=tuple(
            dict.fromkeys(reference.source_domain for reference in references)
        ),
        dominant_patterns=tuple(ranked_patterns[:4]),
        emerging_patterns=tuple(ranked_patterns[4:8]),
        recommended_visual_directions=tuple(principles[:6]),
        avoid_patterns=(
            "literal duplicate offer typography",
            "generic template composition",
            "weak hierarchy and arbitrary decoration",
            "clutter inside the reserved copy zone",
        ),
        originality_constraints=(
            "Use only abstract design principles from references",
            "Do not reproduce a source layout, illustration, logo, or brand system",
            "Combine several signals into an original brand-specific direction",
        ),
        selected_reference_insights=selected,
        research_timestamp=datetime.now(UTC),
        research_fingerprint=hashlib.sha256(
            fingerprint_material.encode()
        ).hexdigest(),
        provider=_safe_provider_name(result.provider),
        degraded=False,
    )


def degraded_research_bundle(
    request: CreativeResearchRequest,
    *,
    provider: str,
) -> CreativeResearchBundle:
    return CreativeResearchBundle(
        research_query_themes=request.queries,
        reference_count=0,
        reference_domains=(),
        dominant_patterns=(),
        emerging_patterns=(),
        recommended_visual_directions=(),
        avoid_patterns=(
            "literal duplicate offer typography",
            "generic template composition",
            "clutter inside the reserved copy zone",
        ),
        originality_constraints=(
            "Use the internal abstract design pattern library",
            "Do not imitate any third-party campaign or brand system",
        ),
        selected_reference_insights=(),
        research_timestamp=datetime.now(UTC),
        research_fingerprint=hashlib.sha256(
            f"{request.cache_key}:degraded".encode()
        ).hexdigest(),
        provider=_safe_provider_name(provider),
        degraded=True,
    )


def sanitize_reference_url(value: str) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 2048:
        raise ValueError("reference URL is invalid")
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local"))
        or parsed.port not in {None, 443}
    ):
        raise ValueError("reference URL is unsafe")
    try:
        if not ip_address(hostname.strip("[]")).is_global:
            raise ValueError("reference URL is unsafe")
    except ValueError as exc:
        if str(exc) == "reference URL is unsafe":
            raise
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=False)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in _TRACKING_QUERY_KEYS
        )
    )
    normalized = urlunsplit(
        ("https", hostname, parsed.path or "/", query, "")
    )
    if len(normalized) > 1024:
        raise ValueError("reference URL is too long")
    return normalized


def _generalize_industry(value: str) -> ResearchIndustry:
    definition = get_business_industry(value)
    if definition is not None:
        group_mapping: dict[str, ResearchIndustry] = {
            "agriculture": "agriculture",
            "commerce": "commerce",
            "healthcare": "healthcare",
            "professional_services": "professional services",
            "real_estate": "real estate",
            "other": "small business",
        }
        return group_mapping[definition.group]
    normalized = value.casefold()
    if any(term in normalized for term in ("saas", "software", "technology", " ai ")):
        return "technology"
    if any(term in normalized for term in ("retail", "shop", "store")):
        return "retail"
    return "small business"


def _generalize_channel(value: str) -> str:
    normalized = value.strip().casefold()
    return normalized if normalized in _PUBLIC_CHANNELS else "other"


def _generalize_format(value: str) -> ResearchFormat:
    return {
        "display_banner": "display banner",
        "landscape_ad": "landscape ad",
        "story_reel": "story vertical",
    }.get(value.strip().casefold(), "social square")  # type: ignore[return-value]


def _classify_objective(value: str) -> ResearchObjective:
    normalized = value.casefold()
    if any(term in normalized for term in ("discount", "sale", "offer", "%", "save ")):
        return "promotional offer"
    if any(term in normalized for term in ("launch", "new product", "introduc")):
        return "product launch"
    if any(term in normalized for term in ("lead", "book", "consult", "contact")):
        return "lead generation"
    if any(term in normalized for term in ("event", "webinar", "conference")):
        return "event promotion"
    return "brand awareness"


def _classify_style(value: str) -> ResearchStyle:
    normalized = value.casefold()
    if any(term in normalized for term in ("minimal", "editorial", "restrained")):
        return "minimal editorial"
    if any(term in normalized for term in ("bold", "energetic", "vibrant")):
        return "bold commercial"
    if any(term in normalized for term in ("trust", "authority", "safe", "clinical")):
        return "trust focused"
    if any(term in normalized for term in ("product", "commerce", "catalog")):
        return "product led"
    return "premium modern"


def _unique_bounded(values) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = " ".join(str(value).split())
        key = normalized.casefold()
        if not normalized or key in seen or _CLONE_LANGUAGE.search(normalized):
            continue
        seen.add(key)
        selected.append(normalized[:180])
    return selected


def _safe_provider_name(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", normalized):
        return "unknown"
    return normalized
