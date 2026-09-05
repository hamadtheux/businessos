from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageFilter, ImageStat


RegionName = Literal[
    "upper_left",
    "upper_right",
    "center_left",
    "center_right",
    "lower_left",
    "lower_right",
    "top_band",
    "bottom_band",
]
TextSide = Literal["left", "right"]
Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class VisualRegionAnalysis:
    name: RegionName
    bounds: Box
    brightness: float
    local_contrast: float
    edge_density: float
    entropy: float
    complexity: float
    quiet_score: float


@dataclass(frozen=True, slots=True)
class VisualAnalysis:
    width: int
    height: int
    overall_complexity: float
    # These are low-level edge/contrast saliency proxies. They deliberately do
    # not claim semantic object or subject recognition.
    saliency_region: RegionName
    saliency_concentration: float
    regions: tuple[VisualRegionAnalysis, ...]

    def region(self, name: RegionName) -> VisualRegionAnalysis:
        for value in self.regions:
            if value.name == name:
                return value
        raise ValueError("visual region is unavailable")


def analyze_visual(image: Image.Image) -> VisualAnalysis:
    """Bounded Pillow-only visual analysis for deterministic layout selection."""
    normalized = image.convert("RGB")
    normalized.thumbnail((256, 256), Image.Resampling.BOX)
    gray = normalized.convert("L")
    width, height = gray.size
    regions = tuple(
        _analyze_region(gray, name, bounds)
        for name, bounds in _candidate_regions(width, height)
    )
    overall = _region_metrics(gray)[-2]
    subject_candidates = tuple(
        region
        for region in regions
        if region.name
        in {
            "upper_left",
            "upper_right",
            "center_left",
            "center_right",
            "lower_left",
            "lower_right",
        }
    )
    salient = max(
        subject_candidates,
        key=lambda value: (value.complexity, value.name),
    )
    average = sum(value.complexity for value in subject_candidates) / len(
        subject_candidates
    )
    # Uniform imagery has no defensible salient region. Only complexity above
    # the regional mean contributes to this proxy concentration.
    concentration = max(0.0, min(1.0, (salient.complexity - average) * 2.0))
    return VisualAnalysis(
        width=image.width,
        height=image.height,
        overall_complexity=round(overall, 4),
        saliency_region=salient.name,
        saliency_concentration=round(concentration, 4),
        regions=regions,
    )


def choose_text_side(
    analysis: VisualAnalysis,
    *,
    requested: TextSide | None,
) -> TextSide:
    left = _side_quiet_score(analysis, "left")
    right = _side_quiet_score(analysis, "right")
    # Respect art direction when the image actually delivered comparable
    # negative space. Override it when pixel evidence shows a materially safer
    # copy region on the other side.
    if requested is not None and abs(left - right) < 0.12:
        return requested
    return "left" if left >= right else "right"


def side_quiet_score(analysis: VisualAnalysis, side: TextSide) -> float:
    return _side_quiet_score(analysis, side)


def _side_quiet_score(analysis: VisualAnalysis, side: TextSide) -> float:
    names: tuple[RegionName, ...] = (
        ("upper_left", "center_left", "lower_left")
        if side == "left"
        else ("upper_right", "center_right", "lower_right")
    )
    weights = (0.25, 0.5, 0.25)
    return sum(
        analysis.region(name).quiet_score * weight
        for name, weight in zip(names, weights, strict=True)
    )


def _candidate_regions(
    width: int,
    height: int,
) -> tuple[tuple[RegionName, Box], ...]:
    half_width = max(1, width // 2)
    third_height = max(1, height // 3)
    return (
        ("upper_left", (0, 0, half_width, third_height)),
        ("upper_right", (half_width, 0, width, third_height)),
        ("center_left", (0, third_height, half_width, min(height, third_height * 2))),
        ("center_right", (half_width, third_height, width, min(height, third_height * 2))),
        ("lower_left", (0, min(height, third_height * 2), half_width, height)),
        ("lower_right", (half_width, min(height, third_height * 2), width, height)),
        ("top_band", (0, 0, width, max(1, round(height * 0.28)))),
        ("bottom_band", (0, min(height - 1, round(height * 0.72)), width, height)),
    )


def _analyze_region(
    image: Image.Image,
    name: RegionName,
    bounds: Box,
) -> VisualRegionAnalysis:
    crop = image.crop(bounds)
    brightness, contrast, edges, entropy, complexity, quiet = _region_metrics(crop)
    return VisualRegionAnalysis(
        name=name,
        bounds=bounds,
        brightness=round(brightness, 4),
        local_contrast=round(contrast, 4),
        edge_density=round(edges, 4),
        entropy=round(entropy, 4),
        complexity=round(complexity, 4),
        quiet_score=round(quiet, 4),
    )


def _region_metrics(
    image: Image.Image,
) -> tuple[float, float, float, float, float, float]:
    if image.width <= 0 or image.height <= 0:
        return 0.0, 0.0, 0.0, 0.0, 1.0, 0.0
    stats = ImageStat.Stat(image)
    brightness = stats.mean[0] / 255
    contrast = min(1.0, stats.stddev[0] / 64)
    entropy = min(1.0, image.entropy() / 8)
    edges_image = image.filter(ImageFilter.FIND_EDGES)
    if edges_image.width > 4 and edges_image.height > 4:
        edges_image = edges_image.crop(
            (2, 2, edges_image.width - 2, edges_image.height - 2)
        )
    edges = min(1.0, ImageStat.Stat(edges_image).mean[0] / 96)
    complexity = min(1.0, edges * 0.50 + contrast * 0.30 + entropy * 0.20)
    quiet = max(0.0, 1.0 - complexity)
    return brightness, contrast, edges, entropy, complexity, quiet
