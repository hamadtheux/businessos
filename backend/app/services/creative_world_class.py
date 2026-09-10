from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal


WorldClassFailure = Literal[
    "generic_lifestyle_stock_scene",
    "decorative_abstraction_as_story",
    "no_business_specific_mechanism",
    "no_product_service_mechanism",
    "no_customer_cause_effect",
    "replaceable_brand_idea",
    "weak_marketing_mechanism",
    "weak_visual_proof",
    "generic_productivity_metaphor",
]


CreativeStoryMode = Literal["offering_proof", "brand_offer"]


_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)

_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "this",
        "to",
        "use",
        "using",
        "with",
        "your",
    }
)


_GENERIC_LIFESTYLE_MARKERS: Final[tuple[str, ...]] = (
    "coffee cup",
    "coffee mug",
    "desk",
    "desk accessories",
    "glasses",
    "headphones",
    "laptop on desk",
    "minimal desk",
    "notebook",
    "office desk",
    "pen",
    "plant",
    "premium workspace",
    "stacked books",
    "stationery",
    "stylish office",
    "work desk",
    "workspace",
)


_GENERIC_PRODUCTIVITY_MARKERS: Final[tuple[str, ...]] = (
    "clear the clutter",
    "focus on what matters",
    "make room",
    "make space",
    "more focus",
    "peace of mind",
    "productivity",
    "simplify your day",
    "work smarter",
    "work with clarity",
)


_DECORATIVE_MARKERS: Final[tuple[str, ...]] = (
    "abstract",
    "blob",
    "circle",
    "circles",
    "concentric",
    "decorative geometry",
    "floating shapes",
    "geometric",
    "gradient",
    "gradients",
    "orb",
    "orbs",
    "ring",
    "rings",
    "shape",
    "shapes",
    "swirl",
    "swirls",
    "digital network",
    "floating nodes",
    "glowing nodes",
    "hologram",
    "holographic",
    "wave",
    "waves",
)

_GENERIC_NETWORK_MARKERS: Final[tuple[str, ...]] = (
    "digital network",
    "floating nodes",
    "glowing nodes",
    "hologram",
    "holographic network",
)


_OFFERING_MECHANISM_MARKERS: Final[tuple[str, ...]] = (
    "agent",
    "agents",
    "automation",
    "automated",
    "before and after",
    "coordinate",
    "coordinates",
    "customer journey",
    "demonstrate",
    "demonstrates",
    "handoff",
    "handoffs",
    "in use",
    "operation",
    "operational",
    "orchestrate",
    "orchestrates",
    "outcome",
    "outcomes",
    "process",
    "service in action",
    "system",
    "systems",
    "transform",
    "transformation",
    "workflow",
    "workflows",
)

_BRAND_OFFER_MECHANISM_MARKERS: Final[tuple[str, ...]] = (
    "before and after",
    "campaign mechanism",
    "consequence",
    "contrast",
    "customer tension",
    "occasion",
    "reveal",
    "transition",
    "transformation",
    "unexpected",
    "visual reveal",
    "visual tension",
)


_CUSTOMER_CAUSALITY_MARKERS: Final[tuple[str, ...]] = (
    "because",
    "causes",
    "consequence",
    "creates",
    "delivers",
    "enables",
    "helps",
    "leads to",
    "reduces",
    "removes",
    "results in",
    "so that",
    "solves",
    "turns",
    "without",
)


_VISUAL_PROOF_MARKERS: Final[tuple[str, ...]] = (
    "before and after",
    "contrast",
    "customer interaction",
    "customer moment",
    "demonstrate",
    "in action",
    "in use",
    "operation",
    "outcome",
    "product in use",
    "service in action",
    "transformation",
    "workflow",
)

_BRAND_OFFER_VISUAL_PROOF_MARKERS: Final[tuple[str, ...]] = (
    "before and after",
    "campaign moment",
    "consequence",
    "contrast",
    "occasion",
    "reveal",
    "transition",
    "transformation",
    "unexpected",
    "visual proof",
    "visual reveal",
    "visual tension",
)


_COMMERCIAL_IDEA_MARKERS: Final[tuple[str, ...]] = (
    "campaign mechanism",
    "customer tension",
    "demonstration",
    "proof",
    "problem",
    "solution",
    "transformation",
    "unexpected",
    "visual reveal",
    "visual tension",
)


@dataclass(frozen=True, slots=True)
class WorldClassCreativeAssessment:
    business_specificity: int
    product_service_mechanism: int
    customer_causality: int
    marketing_idea_strength: int
    visual_proof: int
    differentiation: int
    commercial_readiness: int

    stock_lifestyle_risk: int
    decorative_abstraction_risk: int
    replaceable_brand_risk: int

    hard_failures: tuple[WorldClassFailure, ...]
    approved: bool

    @property
    def minimum_positive_dimension(self) -> int:
        return min(
            self.business_specificity,
            self.product_service_mechanism,
            self.customer_causality,
            self.marketing_idea_strength,
            self.visual_proof,
            self.differentiation,
            self.commercial_readiness,
        )


def _tokens(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()

    return frozenset(
        token
        for token in _TOKEN.findall(value.casefold())
        if len(token) > 2 and token not in _STOPWORDS
    )


def _contains_phrase(value: str, phrase: str) -> bool:
    value_tokens = tuple(_TOKEN.findall(value.casefold()))
    phrase_tokens = tuple(_TOKEN.findall(phrase.casefold()))

    if not phrase_tokens:
        return False

    size = len(phrase_tokens)

    return any(
        value_tokens[index : index + size] == phrase_tokens
        for index in range(len(value_tokens) - size + 1)
    )


def _contains_any(value: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(value, phrase) for phrase in phrases)


def _count_matches(value: str, phrases: tuple[str, ...]) -> int:
    return sum(_contains_phrase(value, phrase) for phrase in phrases)


def _normalized_relevance_tokens(value: str) -> frozenset[str]:
    """
    Normalize grounded subject terms for product/category relevance checks.

    This intentionally stays conservative and token-based. It does not use
    substring matching, so a word such as "offering" can never make "ring"
    appear relevant by accident.
    """

    normalized: set[str] = set()

    for token in _tokens(value):
        normalized.add(token)

        # Small deterministic singular normalization is enough for pairs such
        # as ring/rings, book/books and plant/plants while keeping the matcher
        # predictable and bounded.
        if len(token) > 3 and token.endswith("s"):
            normalized.add(token[:-1])

    return frozenset(normalized)


def _marker_is_grounded_subject(
    marker: str,
    *,
    subject_focus: str,
) -> bool:
    """
    Return True when a supposedly generic object is actually part of the
    authoritative supported hero/product focus.

    Examples:
    - a coffee cup may be legitimate for a coffee product;
    - a ring may be legitimate for a jewelry campaign;
    - a desk may be legitimate for a furniture campaign.

    Contextual relevance only removes the generic-object penalty. It does NOT
    grant quality approval; mechanism, causality, differentiation, commercial
    readiness and the mandatory final semantic critic still apply.
    """

    marker_tokens = _normalized_relevance_tokens(marker)
    subject_tokens = _normalized_relevance_tokens(subject_focus)

    return bool(marker_tokens and marker_tokens.intersection(subject_tokens))


def _count_contextually_irrelevant_matches(
    value: str,
    phrases: tuple[str, ...],
    *,
    subject_focus: str,
) -> int:
    """
    Count only generic/decorative markers that are not grounded in the supported
    subject focus.

    The deterministic pre-render gate should stop obviously irrelevant stock
    shorthand, not reject a business merely because its actual product happens
    to share a noun with a generic-stock blacklist.
    """

    count = 0

    for phrase in phrases:
        if not _contains_phrase(value, phrase):
            continue

        if _marker_is_grounded_subject(
            phrase,
            subject_focus=subject_focus,
        ):
            continue

        count += 1

    return count


def _overlap(first: frozenset[str], second: frozenset[str]) -> float:
    if not first or not second:
        return 0.0

    return len(first & second) / max(1, len(second))


def _bounded(value: float) -> int:
    return max(0, min(100, round(value)))


# Design instructions and quality labels are not evidence about a business or a
# scene. Remove them before checking the relationships between the brief, hero,
# advertising idea and visible consequence. This is part of the existing gate,
# not an additional score or a raised quality threshold.
_DIRECTION_LANGUAGE = frozenset("""
    campaign specific grounded audience brand branded owned category context
    tension contrast reveal consequence transition transformation unexpected
    visual proof mechanism moment occasion before after cause outcome because
    creates turns helps delivers without consequence supported credible directly
    demonstrate demonstrates demonstration describe describes represents represent
    compatibility field schema offering idea meaning meaningful distinctive
    confident confidence premium modern cinematic editorial quiet clear clarity
    focus controlled commercial strategic quality value generic relevant
    connect connects connect directly immediate immediately unmistakable
    customer customers business businesses owner owners small real only use
    scene story hero subject focal launch day decision signals choices path
    marketing intention intended atmosphere mood audience grounded
    coordinate coordinates coordinated coordinating route routes routed routing
    handoff handoffs connect connects connected connecting
    converge converges converged converging
    assign assigns assigned assigning
    distribute distributes distributed distributing
    organize organizes organized organizing
    synchronize synchronizes synchronized synchronizing
    prioritize prioritizes prioritized prioritizing
    surface surfaces surfaced surfacing
    track tracks tracked tracking
    respond responds responded responding
    flow flows flowed flowing
    move moves moved moving
    pass passes passed passing
    arrange arranges arranged arranging
    check checks checked checking
    collect collects collected collecting
    gather gathers gathered gathering
    group groups grouped grouping
    handle handles handled handling
    place places placed placing
    review reviews reviewed reviewing
    sort sorts sorted sorting
    write writes writing wrote written
""".split())

# Observable actions, rather than promises such as "transforms the campaign".
# These verbs only establish executability when their clause contains a subject
# grounded in the brief and shared with the hero; they confer no quality points.
_SCENE_ACTION = re.compile(
    r"\b(?:block(?:s|ed|ing)?|open(?:s|ed|ing)?|clos(?:e|es|ed|ing)|"
    r"hold(?:s|ing)?|held|pull(?:s|ed|ing)?|push(?:es|ed|ing)?|"
    r"stack(?:s|ed|ing)?|pil(?:e|es|ed|ing)|cover(?:s|ed|ing)?|"
    r"spill(?:s|ed|ing)?|pour(?:s|ed|ing)?|cast(?:s|ing)?|"
    r"cut(?:s|ting)?|split(?:s|ting)?|stretch(?:es|ed|ing)?|"
    r"fold(?:s|ed|ing)?|balanc(?:e|es|ed|ing)|weigh(?:s|ed|ing)?|"
    r"hang(?:s|ing)?|hung|lift(?:s|ed|ing)?|carry|carries|carrying|"
    r"reach(?:es|ed|ing)?|cross(?:es|ed|ing)?|pass(?:es|ed|ing)?|"
    r"touch(?:es|ed|ing)?|meet(?:s|ing)?|separat(?:e|es|ed|ing)|"
    r"replac(?:e|es|ed|ing)|fill(?:s|ed|ing)?|empty|empties|"
    r"stop(?:s|ped|ping)?|scatter(?:s|ed|ing)?|squeez(?:e|es|ed|ing)|"
    r"arrang(?:e|es|ed|ing)|check(?:s|ed|ing)?|collect(?:s|ed|ing)?|"
    r"gather(?:s|ed|ing)?|group(?:s|ed|ing)?|handl(?:e|es|ed|ing)|"
    r"plac(?:e|es|ed|ing)|review(?:s|ed|ing)?|sort(?:s|ed|ing)?|"
    r"writ(?:e|es|ing)|wrote|written)\b",
    re.IGNORECASE,
)


# A brand campaign can also communicate a real business relationship without
# relying on a literal physical action. These verbs describe observable
# operational relationships, but they are removed from evidence-token scoring
# above so their mere presence cannot self-certify quality.
_OPERATIONAL_ACTION = re.compile(
    r"\b(?:coordinate(?:s|d|ing)?|route(?:s|d|ing)?|handoff(?:s)?|"
    r"connect(?:s|ed|ing)?|converge(?:s|d|ing)?|assign(?:s|ed|ing)?|"
    r"distribute(?:s|d|ing)?|organize(?:s|d|ing)?|"
    r"synchroniz(?:e|es|ed|ing)|prioritiz(?:e|es|ed|ing)|"
    r"surface(?:s|d|ing)?|track(?:s|ed|ing)?|respond(?:s|ed|ing)?|"
    r"flow(?:s|ed|ing)?|move(?:s|d|ing)?|pass(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)


def creative_story_evidence_tokens(value: str) -> frozenset[str]:
    """Scene/brief terms without self-authored quality or presentation labels."""
    return _normalized_relevance_tokens(value) - _DIRECTION_LANGUAGE


def _grounded_brand_story_evidence(
    *, business_context: str, campaign_goal: str, audience: str,
    campaign_angle: str, marketing_idea: str, customer_care_reason: str,
    hero_subject: str, product_story: str,
    authoritative_evidence_segments: tuple[frozenset[str], ...] = (),
) -> tuple[int, int, int] | None:
    """Require a connected executable brand scene without keyword laundering.

    Two independently valid forms are supported:

    1. A physical observable scene, preserving the original strict behavior.
    2. A grounded operational relationship for businesses such as SaaS,
       professional services, automation, platforms, and other non-physical
       categories.

    Generated quality labels and mechanism verbs are never grounding evidence.
    The operational path must connect multiple independent campaign-authorized
    subject terms through multiple observable relationships.
    """
    context = creative_story_evidence_tokens(business_context)
    campaign_anchor = creative_story_evidence_tokens(
        f"{campaign_goal} {campaign_angle}"
    )
    brief = (
        context
        | campaign_anchor
        | creative_story_evidence_tokens(audience)
    )

    hero = creative_story_evidence_tokens(hero_subject)
    idea = creative_story_evidence_tokens(marketing_idea)
    care = creative_story_evidence_tokens(customer_care_reason)
    story = creative_story_evidence_tokens(product_story)

    shared_subject = hero & story & brief
    action_clauses = re.split(r"[.;]", product_story)

    physical_actions = {
        match.group().casefold()
        for match in _SCENE_ACTION.finditer(product_story)
    }
    operational_actions = {
        match.group().casefold()
        for match in _OPERATIONAL_ACTION.finditer(product_story)
    }

    common_grounding = (
        bool(idea & shared_subject)
        and bool(care & brief & (idea | story))
    )

    physical_connected = (
        common_grounding
        and len(shared_subject) >= 2
        and bool(shared_subject & context)
        and len(physical_actions) >= 2
        and any(
            _SCENE_ACTION.search(clause)
            and len(
                creative_story_evidence_tokens(clause)
                & shared_subject
            ) >= 2
            and len(
                creative_story_evidence_tokens(clause)
                - shared_subject
            ) >= 2
            for clause in action_clauses
        )
    )

    operational_connected = (
        common_grounding
        and len(shared_subject) >= 3
        and len(idea & shared_subject) >= 2
        and len(operational_actions) >= 2
        # Operational subjects must be present in the separately assembled,
        # tenant-scoped Business Brain. Strategy prose cannot authorize them.
        # Terms must co-occur in one positive source segment; a global union of
        # unrelated sources cannot manufacture a capability.
        and max(
            (
                len(shared_subject & segment)
                for segment in authoritative_evidence_segments
            ),
            default=0,
        ) >= 3
        and any(
            _OPERATIONAL_ACTION.search(clause)
            and len(
                creative_story_evidence_tokens(clause)
                & shared_subject
            ) >= 3
            and len(
                creative_story_evidence_tokens(clause)
                - shared_subject
            ) >= 2
            for clause in action_clauses
        )
    )

    if not physical_connected and not operational_connected:
        return None

    actions = (
        physical_actions
        if physical_connected
        else operational_actions
    )

    # These values feed the existing score formulas. Cap each relationship
    # signal so repeating five business-function names cannot manufacture
    # perfect scores.
    return (
        min(2, len(actions)),
        min(2, len(shared_subject)),
        min(2, len(idea & shared_subject)),
    )


def assess_world_class_creative(
    *,
    business_context: str,
    campaign_goal: str,
    audience: str,
    subject_focus: str,
    campaign_angle: str,
    marketing_idea: str,
    customer_care_reason: str,
    hero_subject: str,
    hero_relevance: str,
    product_story: str,
    visual_metaphor: str,
    scroll_stopping_hook: str,
    story_mode: CreativeStoryMode = "offering_proof",
    authoritative_evidence_segments: tuple[frozenset[str], ...] = (),
) -> WorldClassCreativeAssessment:
    """
    Deterministically reject attractive-but-generic advertising concepts.

    ``offering_proof`` means a tenant-scoped campaign selection authoritatively
    establishes a real catalog product/service. Visible offering proof is therefore
    mandatory.

    ``brand_offer`` means no campaign-selected catalog offering is authoritative.
    The engine must not invent one. The compatibility field
    ``product_service_mechanism`` then measures the strength of the grounded
    campaign/brand mechanism instead.

    Numeric quality floors remain unchanged in both modes.
    """

    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")

    strategy_text = " ".join(
        (
            business_context,
            campaign_goal,
            audience,
            subject_focus,
            campaign_angle,
        )
    )

    concept_text = " ".join(
        (
            marketing_idea,
            customer_care_reason,
            hero_subject,
            hero_relevance,
            product_story,
            visual_metaphor,
            scroll_stopping_hook,
        )
    )

    full_text = f"{strategy_text} {concept_text}"

    strategy_tokens = _tokens(strategy_text)
    concept_tokens = _tokens(concept_text)
    subject_tokens = _tokens(subject_focus)
    business_tokens = _tokens(business_context)
    if story_mode == "brand_offer":
        # Generated subject_focus and design vocabulary cannot boost grounding.
        strategy_tokens = creative_story_evidence_tokens(
            f"{business_context} {campaign_goal} {audience} {campaign_angle}"
        )
        concept_tokens = creative_story_evidence_tokens(concept_text)
        subject_tokens = creative_story_evidence_tokens(f"{audience} {campaign_angle}")
        business_tokens = creative_story_evidence_tokens(business_context)

    strategy_overlap = _overlap(concept_tokens, strategy_tokens)
    subject_overlap = _overlap(concept_tokens, subject_tokens)
    business_overlap = _overlap(concept_tokens, business_tokens)

    # In offering mode, only the selected offering focus may exempt a normally
    # generic prop. In brand mode, generated subject_focus is not authoritative;
    # only the caller's grounded business/category context may do so.
    grounded_subject_context = (
        subject_focus if story_mode == "offering_proof" else business_context
    )
    lifestyle_count = _count_contextually_irrelevant_matches(
        concept_text,
        _GENERIC_LIFESTYLE_MARKERS,
        subject_focus=grounded_subject_context,
    )
    productivity_count = _count_matches(
        full_text,
        _GENERIC_PRODUCTIVITY_MARKERS,
    )
    decorative_count = _count_contextually_irrelevant_matches(
        concept_text,
        _DECORATIVE_MARKERS,
        subject_focus=grounded_subject_context,
    )
    mechanism_markers = (
        _OFFERING_MECHANISM_MARKERS
        if story_mode == "offering_proof"
        else _BRAND_OFFER_MECHANISM_MARKERS
    )
    mechanism_count = _count_matches(
        concept_text,
        mechanism_markers,
    )
    causality_count = _count_matches(
        f"{customer_care_reason} {product_story} {marketing_idea}",
        _CUSTOMER_CAUSALITY_MARKERS,
    )
    proof_markers = (
        _VISUAL_PROOF_MARKERS
        if story_mode == "offering_proof"
        else _BRAND_OFFER_VISUAL_PROOF_MARKERS
    )
    proof_count = _count_matches(
        f"{hero_subject} {hero_relevance} {product_story} {scroll_stopping_hook}",
        proof_markers,
    )
    commercial_idea_count = _count_matches(
        f"{marketing_idea} {scroll_stopping_hook} {visual_metaphor}",
        _COMMERCIAL_IDEA_MARKERS,
    )

    grounded_brand_story = True
    if story_mode == "brand_offer":
        evidence = _grounded_brand_story_evidence(
            business_context=business_context, campaign_goal=campaign_goal,
            audience=audience, campaign_angle=campaign_angle,
            marketing_idea=marketing_idea, customer_care_reason=customer_care_reason,
            hero_subject=hero_subject, product_story=product_story,
            authoritative_evidence_segments=authoritative_evidence_segments,
        )
        grounded_brand_story = evidence is not None
        if evidence is None:
            mechanism_count = causality_count = proof_count = commercial_idea_count = 0
        else:
            mechanism_count, proof_count, commercial_idea_count = evidence
            causality_count = mechanism_count

    generic_lifestyle_without_mechanism = (
        lifestyle_count >= 2
        and mechanism_count == 0
        and proof_count == 0
    )

    generic_productivity_without_mechanism = (
        productivity_count >= 1
        and mechanism_count == 0
        and subject_overlap < 0.18
    )

    decorative_without_story = (
        decorative_count >= 2
        and (
            (mechanism_count == 0 and proof_count == 0)
            or _contains_any(concept_text, _GENERIC_NETWORK_MARKERS)
        )
    )

    business_specificity = _bounded(
        28
        + strategy_overlap * 34
        + subject_overlap * 38
        + business_overlap * 22
        + min(12, mechanism_count * 5)
        - lifestyle_count * 8
        - decorative_count * 6
    )

    if story_mode == "offering_proof":
        product_service_mechanism = _bounded(
            24
            + subject_overlap * 34
            + min(40, mechanism_count * 11)
            + min(14, proof_count * 5)
            - lifestyle_count * 7
        )
    else:
        # Compatibility field, different semantic meaning:
        # strength of a grounded campaign/brand mechanism when there is no
        # authoritative selected product/service to depict.
        product_service_mechanism = _bounded(
            30
            + strategy_overlap * 30
            + business_overlap * 22
            + min(24, mechanism_count * 7)
            + min(20, commercial_idea_count * 7)
            + min(16, proof_count * 5)
            - lifestyle_count * 7
            - decorative_count * 6
        )

    customer_causality = _bounded(
        30
        + min(42, causality_count * 14)
        + min(18, mechanism_count * 5)
        + min(10, proof_count * 3)
    )

    marketing_idea_strength = _bounded(
        30
        + min(32, commercial_idea_count * 12)
        + min(24, mechanism_count * 7)
        + min(14, proof_count * 5)
        - productivity_count * 10
        - decorative_count * 5
    )

    visual_proof = _bounded(
        26
        + min(48, proof_count * 15)
        + min(24, mechanism_count * 6)
        - lifestyle_count * 8
        - decorative_count * 7
    )

    stock_lifestyle_risk = _bounded(
        18
        + lifestyle_count * 19
        + productivity_count * 12
        - mechanism_count * 10
        - proof_count * 9
    )

    decorative_abstraction_risk = _bounded(
        16
        + decorative_count * 20
        - mechanism_count * 11
        - proof_count * 9
    )

    replaceable_brand_risk = _bounded(
        88
        - strategy_overlap * 30
        - subject_overlap * 34
        - business_overlap * 18
        - min(20, mechanism_count * 6)
        - min(14, proof_count * 5)
        + lifestyle_count * 7
        + decorative_count * 6
    )

    differentiation = _bounded(
        88
        - replaceable_brand_risk * 0.62
        - stock_lifestyle_risk * 0.20
        - decorative_abstraction_risk * 0.18
        + min(20, commercial_idea_count * 7)
    )

    commercial_readiness = _bounded(
        business_specificity * 0.20
        + product_service_mechanism * 0.22
        + customer_causality * 0.12
        + marketing_idea_strength * 0.18
        + visual_proof * 0.16
        + differentiation * 0.12
        - stock_lifestyle_risk * 0.08
        - decorative_abstraction_risk * 0.07
    )

    failures: list[WorldClassFailure] = []
    if not grounded_brand_story:
        failures.extend(("no_business_specific_mechanism", "weak_visual_proof"))

    if generic_lifestyle_without_mechanism:
        failures.append("generic_lifestyle_stock_scene")

    if decorative_without_story:
        failures.append("decorative_abstraction_as_story")

    if business_specificity < 64:
        failures.append("no_business_specific_mechanism")

    if (
        story_mode == "offering_proof"
        and product_service_mechanism < 64
    ):
        failures.append("no_product_service_mechanism")

    if (
        story_mode == "brand_offer"
        and product_service_mechanism < 64
    ):
        # Same quality bar, but do not falsely claim a missing product.
        failures.append("weak_marketing_mechanism")

    if customer_causality < 58:
        failures.append("no_customer_cause_effect")

    if replaceable_brand_risk >= 58:
        failures.append("replaceable_brand_idea")

    if marketing_idea_strength < 62:
        failures.append("weak_marketing_mechanism")

    if visual_proof < 62:
        failures.append("weak_visual_proof")

    if generic_productivity_without_mechanism:
        failures.append("generic_productivity_metaphor")

    unique_failures = tuple(dict.fromkeys(failures))

    approved = (
        not unique_failures
        and business_specificity >= 70
        and product_service_mechanism >= 70
        and customer_causality >= 62
        and marketing_idea_strength >= 68
        and visual_proof >= 68
        and differentiation >= 62
        and commercial_readiness >= 68
        and stock_lifestyle_risk < 55
        and decorative_abstraction_risk < 55
        and replaceable_brand_risk < 55
    )

    return WorldClassCreativeAssessment(
        business_specificity=business_specificity,
        product_service_mechanism=product_service_mechanism,
        customer_causality=customer_causality,
        marketing_idea_strength=marketing_idea_strength,
        visual_proof=visual_proof,
        differentiation=differentiation,
        commercial_readiness=commercial_readiness,
        stock_lifestyle_risk=stock_lifestyle_risk,
        decorative_abstraction_risk=decorative_abstraction_risk,
        replaceable_brand_risk=replaceable_brand_risk,
        hard_failures=unique_failures,
        approved=approved,
    )


WORLD_CLASS_DIRECTOR_CONTRACT: Final[str] = """
WORLD-CLASS CREATIVE STANDARD:

You are not producing a decorative social-media template.
You are creating one campaign idea that a top global creative agency could defend.

Every concept must communicate, in one visual read:

1. WHO this business serves.
2. WHAT product or service is creating value.
3. HOW that value happens.
4. WHY the target customer cares.
5. WHAT visual proof makes the campaign specific to this business.

MANDATORY:

- Build the concept around a real product/service mechanism, workflow, usage moment,
  customer transformation, or operational outcome.
- The visual story must stop making sense if an unrelated company replaces the brand.
- The hero must carry commercial meaning, not merely aesthetic polish.
- Objects and environments must be causally relevant to the campaign idea.
- marketing_idea must describe the advertising mechanism, not visual decoration.
- customer_care_reason must state the customer tension, desire, or business consequence.
- product_story must show cause → mechanism → outcome.
- scroll_stopping_hook must be visually executable in one glance.
- Use premium art direction only after the commercial idea is strong.

AUTOMATICALLY REJECT:

- generic premium desk or workspace photography;
- coffee cups, books, glasses, plants, stationery, laptops, or office props used only
  to imply productivity;
- generic lifestyle scenes that could advertise almost any SaaS company;
- "make space", "focus on what matters", "work smarter", "peace of mind", or similar
  generic productivity metaphors without a specific product/service mechanism;
- gradients, rings, circles, waves, blobs, or geometry used as the central campaign idea;
- generic people smiling at a laptop with no visible business mechanism;
- empty luxury/minimalist styling used instead of an advertising idea;
- stock-photo compositions with brand copy placed on unused negative space;
- a hero subject that does not visibly prove the campaign promise;
- a concept that survives the swap-logo test unchanged.

QUALITY BAR:

If the idea would be acceptable for fifty unrelated brands, it is not acceptable here.
If the image would still work after removing the product/service story, it is not acceptable.
If aesthetic taste is doing the work that marketing strategy should do, it is not acceptable.
"""


WORLD_CLASS_BRAND_OFFER_DIRECTOR_CONTRACT: Final[str] = """
WORLD-CLASS CREATIVE STANDARD — BRAND/OFFER MODE:

You are not producing a decorative social-media template.
You are creating one campaign idea that a top global creative agency could defend.

No campaign-selected catalog product or service is authoritative in this mode.
Do not invent a product, service, package, app, application, interface, UI, feature,
integration, workflow, fulfillment process, fulfillment path, customer fact, or outcome
merely to make the visual easier to design.

Every concept must communicate, in one visual read:

1. WHO the grounded campaign is for, when that is actually known.
2. WHAT campaign promise, offer, occasion, tension, or brand idea is being expressed.
3. HOW the visual itself makes that campaign idea understandable.
4. WHY the intended viewer should care, using only grounded context.
5. WHAT visual proof makes the idea specific to this campaign and business.

MANDATORY:

- Build an actual advertising mechanism: tension, reveal, contrast, transition,
  consequence, occasion, transformation, or a grounded operational relationship.
- The visual story must stop making sense if an unrelated company replaces the brand.
- The hero must carry commercial meaning, not merely aesthetic polish.
- Objects and environments must be causally relevant to grounded campaign context.
- marketing_idea must describe the advertising mechanism, not visual decoration.
- customer_care_reason must state only a grounded viewer tension, desire, or consequence.
- The schema field product_story must describe the grounded campaign mechanism and
  cause -> mechanism -> outcome; it must NOT fabricate a product or service.
- scroll_stopping_hook must be visually executable in one glance.
- Use premium art direction only after the commercial idea is strong.

AUTOMATICALLY REJECT:

- invented products, services, interfaces, features, integrations, packages, or customer facts;
- generic premium desk or workspace photography;
- coffee cups, books, glasses, plants, stationery, laptops, or office props used only
  to imply productivity;
- generic lifestyle scenes that could advertise almost any company;
- "make space", "focus on what matters", "work smarter", "peace of mind", or similar
  generic productivity metaphors without a grounded campaign mechanism;
- gradients, rings, circles, waves, blobs, or geometry used as the central campaign idea;
- generic people smiling at a laptop with no grounded campaign meaning;
- empty luxury/minimalist styling used instead of an advertising idea;
- stock-photo compositions with brand copy placed on unused negative space;
- a hero subject that does not visibly support the grounded campaign promise;
- a concept that survives the swap-logo test unchanged.

QUALITY BAR:

If the idea would be acceptable for fifty unrelated brands, it is not acceptable here.
If the image would still work after removing the grounded campaign story, it is not acceptable.
If aesthetic taste is doing the work that marketing strategy should do, it is not acceptable.
"""


def world_class_director_contract(
    story_mode: CreativeStoryMode,
) -> str:
    """Return the server-owned Creative Director contract for the active story mode."""
    if story_mode == "offering_proof":
        return WORLD_CLASS_DIRECTOR_CONTRACT
    if story_mode == "brand_offer":
        return WORLD_CLASS_BRAND_OFFER_DIRECTOR_CONTRACT
    raise ValueError("Creative story mode is invalid")


WORLD_CLASS_RAW_VISUAL_CONTRACT: Final[str] = """
WORLD-CLASS RAW VISUAL STANDARD:

Create a campaign scene, not stock decoration.

The scene must visually prove the selected business-specific marketing idea through a
credible product/service mechanism, customer interaction, workflow, transformation, or
commercially meaningful outcome.

Every visible object must earn its place by supporting the campaign story.

Do not use unrelated generic objects as shorthand for value:
- generic premium office desks when the desk/office is not the supported offering;
- stacked books, coffee mugs, glasses, plants, pens, notebooks, laptops, or lifestyle
  props when they are not the actual supported product or a causally relevant usage
  element;
- abstract gradients, rings, waves, circles, or decorative geometry when they are not
  the supported product/category and are being used as a substitute for the campaign
  idea;
- generic productivity symbolism;
- meaningless luxury/minimal styling;
- anonymous stock photography that could advertise an unrelated company.

A product-category object is allowed when the grounded subject focus establishes that
the object itself is the supported product or is necessary to demonstrate real use.
That exception removes only the generic-object objection; the scene must still prove
a specific commercial mechanism and customer consequence.

Apply the swap-logo test before rendering:
if another unrelated business could use the same scene unchanged, redesign the scene.

The raw visual must make the business/category story understandable even before exact
headline, CTA, and logo are composited.
"""


WORLD_CLASS_BRAND_OFFER_RAW_VISUAL_CONTRACT: Final[str] = """
WORLD-CLASS RAW VISUAL STANDARD — BRAND/OFFER MODE:

Create a campaign scene, not stock decoration.

No campaign-selected catalog offering is authoritative.
Do not invent a product or service, package, app, application, fake interface, UI,
feature, integration, workflow, fulfillment process, fulfillment path, customer fact,
or unsupported outcome.

The raw visual must instead prove the grounded campaign idea through a credible
campaign-specific visual mechanism such as tension, contrast, reveal, transition,
occasion, consequence, or a grounded operational relationship supported by the
supplied direction.

Every visible object must earn its place by supporting the grounded campaign story.

Do not use unrelated generic objects as shorthand for value:
- generic premium office desks;
- stacked books, coffee mugs, glasses, plants, pens, notebooks, laptops, or lifestyle
  props when they are not causally relevant to the grounded campaign idea;
- abstract gradients, rings, waves, circles, or decorative geometry as a substitute
  for a campaign mechanism;
- generic productivity symbolism;
- meaningless luxury/minimal styling;
- anonymous stock photography that could advertise an unrelated company.

Apply the swap-logo test before rendering:
if another unrelated business could use the same underlying idea substantially
unchanged, redesign the scene.

The raw visual must support the business/category/campaign story even before exact
headline, CTA, offer copy, and logo are composited.
"""


def world_class_raw_visual_contract(
    story_mode: CreativeStoryMode,
) -> str:
    """Return the bounded raw-renderer policy for the active story mode."""
    if story_mode == "offering_proof":
        return WORLD_CLASS_RAW_VISUAL_CONTRACT
    if story_mode == "brand_offer":
        return WORLD_CLASS_BRAND_OFFER_RAW_VISUAL_CONTRACT
    raise ValueError("Creative story mode is invalid")


WORLD_CLASS_VISUAL_CRITIC_CONTRACT: Final[str] = """
WORLD-CLASS COMMERCIAL REVIEW:

Do not approve an image because it is merely clean, premium, realistic, or technically
well generated.

Reject the candidate when any of these are true:

- it looks like generic stock photography;
- the main visual is a desk, plant, books, coffee mug, glasses, laptop, stationery, or
  lifestyle environment with no specific product/service mechanism;
- the business could be replaced by an unrelated brand without changing the scene;
- the image communicates generic productivity instead of this company's actual value;
- the product/service mechanism is invisible;
- there is no cause-and-effect customer story;
- the campaign promise is explained only by copy rather than visually demonstrated;
- decorative abstraction carries more meaning than the product/service story;
- the image is aesthetically polished but commercially forgettable;
- the hero subject does not provide evidence for the marketing idea.

A world-class approval requires:

- business-specific visual proof;
- clear product/service relevance;
- an understandable commercial mechanism;
- credible customer consequence;
- strong art direction;
- distinctiveness;
- scroll-stopping visual logic;
- no swap-logo replaceability.

A technically attractive but generic image must be rejected.
"""


def world_class_regeneration_instruction(
    failures: tuple[WorldClassFailure, ...],
    *,
    story_mode: CreativeStoryMode = "offering_proof",
) -> str:
    """
    Produce server-owned correction language.

    Never forward private critic reasoning or arbitrary model prose.
    Never invent an offering when the campaign has no authoritative selection.
    """

    if story_mode not in {"offering_proof", "brand_offer"}:
        raise ValueError("Creative story mode is invalid")

    failure_set = set(failures)

    if story_mode == "brand_offer":
        if "generic_lifestyle_stock_scene" in failure_set:
            return (
                "Replace the generic lifestyle or desk scene with a campaign-specific "
                "visual mechanism tied to the grounded objective, audience, offer, "
                "category, or brand context. Remove decorative office props. Do not "
                "invent a product, service, package, app, interface, UI, feature, "
                "workflow, customer fact, or outcome."
            )

        if "generic_productivity_metaphor" in failure_set:
            return (
                "Replace the generic productivity metaphor with a specific grounded "
                "campaign tension, reveal, contrast, transition, occasion, or customer "
                "consequence. Do not invent a product, service, package, app, "
                "interface, UI, feature, workflow, integration, fulfillment process, "
                "fulfillment path, customer fact, or outcome."
            )

        if "decorative_abstraction_as_story" in failure_set:
            return (
                "Remove decorative abstraction as the central idea. Build one credible "
                "campaign-specific visual mechanism from grounded brand, objective, "
                "audience, category, or offer context. Do not invent a product, "
                "service, package, app, interface, UI, feature, workflow, integration, "
                "fulfillment process, fulfillment path, customer fact, or outcome."
            )

        if "replaceable_brand_idea" in failure_set:
            return (
                "Redesign the hero so the campaign idea feels intentionally owned by "
                "this business and would not work substantially unchanged for an "
                "unrelated brand. Use only grounded campaign context and do not invent "
                "a product, service, package, app, interface, UI, feature, workflow, "
                "integration, fulfillment process, fulfillment path, customer fact, "
                "or outcome."
            )

        return (
            "Strengthen the commercial campaign idea with one grounded visual mechanism, "
            "one viewer-relevant consequence or tension when supported, and one clear "
            "piece of visual proof. Reject generic stock composition and decorative "
            "filler. Do not invent a product, service, package, app, interface, UI, "
            "feature, workflow, integration, fulfillment process, fulfillment path, "
            "customer fact, or outcome."
        )

    if "generic_lifestyle_stock_scene" in failure_set:
        return (
            "Replace the generic lifestyle/desk scene with a business-specific commercial "
            "moment that visibly demonstrates the supported product or service doing useful "
            "work for its target customer. Remove decorative office props that do not carry "
            "campaign meaning."
        )

    if "generic_productivity_metaphor" in failure_set:
        return (
            "Replace the generic productivity metaphor with a specific cause-and-effect "
            "product/service story. The scene must demonstrate how the supported offering "
            "changes a real customer workflow or outcome."
        )

    if "decorative_abstraction_as_story" in failure_set:
        return (
            "Remove abstract decorative geometry as the central idea. Build one credible "
            "commercial scene around the supported product/service mechanism and customer "
            "outcome."
        )

    if "replaceable_brand_idea" in failure_set:
        return (
            "Redesign the hero so the scene would stop making sense for an unrelated brand. "
            "Use a distinctive business-specific mechanism, workflow, usage moment, or "
            "customer transformation."
        )

    if (
        "no_product_service_mechanism" in failure_set
        or "weak_visual_proof" in failure_set
    ):
        return (
            "Make the supported product or service visibly cause the campaign outcome. "
            "Show one clear mechanism, usage moment, workflow, transformation, or customer "
            "interaction instead of implying value through atmosphere."
        )

    return (
        "Strengthen the commercial idea. Show one business-specific product/service "
        "mechanism, one customer-relevant consequence, and one unmistakable visual proof. "
        "Reject generic stock composition and decorative filler."
    )
