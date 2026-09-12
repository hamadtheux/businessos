from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from app.exceptions.marketing import MarketingValidationError
from app.services.marketing_media import MAX_MARKETING_VIDEO_BYTES


_FFPROBE_TIMEOUT_SECONDS = 20
_MAX_FFPROBE_OUTPUT_BYTES = 1_000_000
_SUPPORTED_SOURCE_EXTENSIONS = frozenset({"mp4", "webm"})


@dataclass(frozen=True, slots=True)
class ProbedMarketingVideo:
    width: int
    height: int
    duration_seconds: int
    video_codec: str
    audio_codec: str | None
    format_name: str | None


async def probe_marketing_video(
    content: bytes,
    *,
    extension: str,
) -> ProbedMarketingVideo:
    """
    Inspect already-bounded video bytes.

    This compatibility entry point writes the bytes to a server-owned temporary
    file and delegates to the file-backed probe used by background workers.
    """
    normalized_extension = extension.casefold().strip()
    if normalized_extension not in _SUPPORTED_SOURCE_EXTENSIONS:
        raise MarketingValidationError("marketing_media_unsupported")

    if not content or len(content) > MAX_MARKETING_VIDEO_BYTES:
        raise MarketingValidationError("marketing_media_unreadable")

    with TemporaryDirectory(prefix="aibos-marketing-video-") as temp_directory:
        source_path = Path(temp_directory) / f"source.{normalized_extension}"
        await asyncio.to_thread(source_path.write_bytes, content)
        return await probe_marketing_video_file(source_path)


async def probe_marketing_video_file(
    source_path: Path,
) -> ProbedMarketingVideo:
    """
    Inspect a server-owned local video file without loading it into Python memory.

    ffprobe is executed directly with an argument array and receives only the
    resolved server-side path. stderr is never surfaced to callers.
    """
    source_path = Path(source_path).resolve()

    if not source_path.is_file():
        raise MarketingValidationError("marketing_media_unreadable")

    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            (
                "format=duration,format_name:"
                "stream=index,codec_type,codec_name,width,height,duration:"
                "stream_tags=rotate:"
                "stream_side_data=rotation"
            ),
            str(source_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        # Missing/broken runtime dependency is infrastructure failure and must
        # remain distinguishable from invalid customer media.
        raise

    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_FFPROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise

    if process.returncode != 0:
        raise MarketingValidationError("marketing_media_unreadable")

    if not stdout or len(stdout) > _MAX_FFPROBE_OUTPUT_BYTES:
        raise MarketingValidationError("marketing_media_unreadable")

    return _parse_ffprobe_output(stdout)

def _parse_ffprobe_output(payload: bytes) -> ProbedMarketingVideo:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise MarketingValidationError("marketing_media_unreadable") from None

    if not isinstance(document, dict):
        raise MarketingValidationError("marketing_media_unreadable")

    raw_streams = document.get("streams")
    if not isinstance(raw_streams, list):
        raise MarketingValidationError("marketing_media_unreadable")

    streams = [item for item in raw_streams if isinstance(item, dict)]
    video_stream = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise MarketingValidationError("marketing_media_unreadable")

    width = _positive_dimension(video_stream.get("width"))
    height = _positive_dimension(video_stream.get("height"))

    if _display_rotation(video_stream) in {90, 270}:
        width, height = height, width

    video_codec = video_stream.get("codec_name")
    if (
        not isinstance(video_codec, str)
        or not video_codec.strip()
        or len(video_codec) > 64
    ):
        raise MarketingValidationError("marketing_media_unreadable")
    video_codec = video_codec.strip().casefold()

    audio_codec: str | None = None
    for stream in streams:
        if stream.get("codec_type") != "audio":
            continue

        candidate = stream.get("codec_name")
        if isinstance(candidate, str) and candidate.strip() and len(candidate) <= 64:
            audio_codec = candidate.strip().casefold()
        break

    raw_format = document.get("format")
    format_value = raw_format if isinstance(raw_format, dict) else {}

    duration = _duration_value(format_value.get("duration"))
    if duration is None:
        duration = _duration_value(video_stream.get("duration"))

    if duration is None or duration <= 0 or duration > 3600:
        raise MarketingValidationError("marketing_video_duration_required")

    # Round upward rather than truncating. This keeps platform classification
    # conservative: e.g. 180.1 seconds must not be treated as a 180-second Short.
    duration_seconds = math.ceil(duration)
    if not 1 <= duration_seconds <= 3600:
        raise MarketingValidationError("marketing_video_duration_required")

    format_name = format_value.get("format_name")
    if not isinstance(format_name, str) or not format_name.strip():
        normalized_format_name = None
    else:
        normalized_format_name = format_name.strip()[:128]

    return ProbedMarketingVideo(
        width=width,
        height=height,
        duration_seconds=duration_seconds,
        video_codec=video_codec,
        audio_codec=audio_codec,
        format_name=normalized_format_name,
    )


def _positive_dimension(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketingValidationError("marketing_media_unreadable")
    if not 1 <= value <= 20_000:
        raise MarketingValidationError("marketing_media_unreadable")
    return value


def _duration_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None

    try:
        duration = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not math.isfinite(duration):
        return None
    return duration


def _display_rotation(stream: dict[str, object]) -> int:
    candidates: list[object] = []

    tags = stream.get("tags")
    if isinstance(tags, dict):
        candidates.append(tags.get("rotate"))

    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if isinstance(item, dict):
                candidates.append(item.get("rotation"))

    for value in candidates:
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(numeric):
            continue

        normalized = int(round(numeric)) % 360
        if normalized in {0, 90, 180, 270}:
            return normalized

    return 0


@dataclass(frozen=True, slots=True)
class RenderedMarketingVideoVariant:
    key: str
    path: Path
    content_type: str
    extension: str
    width: int
    height: int
    aspect_ratio: str
    duration_seconds: int
    transformation: str


MARKETING_VIDEO_VARIANTS = (
    ("vertical_9_16", 1080, 1920, "9:16"),
    ("landscape_16_9", 1920, 1080, "16:9"),
)


async def render_marketing_video_variant(
    source_path: Path,
    output_directory: Path,
    *,
    key: str,
    width: int,
    height: int,
    aspect_ratio: str,
    duration_seconds: int,
) -> RenderedMarketingVideoVariant:
    """
    Normalize one trusted source video into a provider-neutral MP4 derivative.

    Rendering is containment-only: the complete source frame remains visible
    and any unused canvas area is padded rather than cropped.
    """
    profile = next(
        (
            item
            for item in MARKETING_VIDEO_VARIANTS
            if item == (key, width, height, aspect_ratio)
        ),
        None,
    )
    if profile is None:
        raise MarketingValidationError("marketing_video_profile_invalid")

    if (
        not isinstance(duration_seconds, int)
        or isinstance(duration_seconds, bool)
        or not 1 <= duration_seconds <= 3600
    ):
        raise MarketingValidationError("marketing_video_duration_required")

    source_path = source_path.resolve()
    output_directory = output_directory.resolve()

    if not source_path.is_file():
        raise MarketingValidationError("marketing_media_unreadable")

    await asyncio.to_thread(
        output_directory.mkdir,
        parents=True,
        exist_ok=True,
    )

    output_path = output_directory / f"{key}.mp4"

    # Give long videos proportionally more time while still placing a hard
    # ceiling on a stuck encoder. The durable worker lease is renewed
    # independently while this bounded subprocess is active.
    timeout_seconds = max(
        120,
        min(7200, duration_seconds * 3),
    )

    video_filter = (
        f"scale={width}:{height}:"
        "force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1"
    )

    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-y",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-sn",
            "-dn",
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        raise

    try:
        _stdout, _stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()
        output_path.unlink(missing_ok=True)
        raise

    if process.returncode != 0:
        output_path.unlink(missing_ok=True)
        raise MarketingValidationError("marketing_video_render_failed")

    try:
        stat = await asyncio.to_thread(output_path.stat)
    except OSError:
        raise MarketingValidationError("marketing_video_render_failed") from None

    if stat.st_size <= 0:
        output_path.unlink(missing_ok=True)
        raise MarketingValidationError("marketing_video_render_failed")

    return RenderedMarketingVideoVariant(
        key=key,
        path=output_path,
        content_type="video/mp4",
        extension="mp4",
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
        transformation="contain_no_crop",
    )
