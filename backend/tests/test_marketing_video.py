from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

os.environ.setdefault(
    "AIBOS_DATABASE_URL",
    "postgresql+asyncpg://database.invalid/test",
)
os.environ.setdefault("AIBOS_AUTH_SECRET_KEY", "x" * 32)

from app.exceptions.marketing import MarketingValidationError  # noqa: E402
from app.services.marketing_video import (  # noqa: E402
    _parse_ffprobe_output,
    probe_marketing_video,
    probe_marketing_video_file,
    render_marketing_video_variant,
)


def _payload(
    *,
    width: int = 1920,
    height: int = 1080,
    duration: str = "30.200000",
    rotation: int | None = None,
) -> bytes:
    video: dict[str, object] = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": width,
        "height": height,
    }
    if rotation is not None:
        video["side_data_list"] = [{"rotation": rotation}]

    return json.dumps(
        {
            "streams": [
                video,
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                },
            ],
            "format": {
                "duration": duration,
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            },
        }
    ).encode()


class _Process:
    def __init__(
        self,
        *,
        stdout: bytes,
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


class MarketingVideoProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_parser_returns_authoritative_metadata(self) -> None:
        result = _parse_ffprobe_output(_payload())

        self.assertEqual(result.width, 1920)
        self.assertEqual(result.height, 1080)
        self.assertEqual(result.duration_seconds, 31)
        self.assertEqual(result.video_codec, "h264")
        self.assertEqual(result.audio_codec, "aac")
        self.assertIn("mp4", result.format_name or "")

    def test_rotation_swaps_display_dimensions(self) -> None:
        result = _parse_ffprobe_output(
            _payload(
                width=1920,
                height=1080,
                duration="15",
                rotation=90,
            )
        )

        self.assertEqual(result.width, 1080)
        self.assertEqual(result.height, 1920)

    def test_duration_rounds_up_for_conservative_platform_classification(self) -> None:
        result = _parse_ffprobe_output(
            _payload(duration="180.0001")
        )

        self.assertEqual(result.duration_seconds, 181)

    def test_duration_over_limit_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            MarketingValidationError,
            "marketing_video_duration_required",
        ):
            _parse_ffprobe_output(
                _payload(duration="3600.1")
            )

    def test_missing_video_stream_is_rejected(self) -> None:
        payload = json.dumps(
            {
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "audio",
                        "codec_name": "aac",
                    }
                ],
                "format": {"duration": "10"},
            }
        ).encode()

        with self.assertRaisesRegex(
            MarketingValidationError,
            "marketing_media_unreadable",
        ):
            _parse_ffprobe_output(payload)

    async def test_probe_invokes_ffprobe_without_shell(self) -> None:
        process = _Process(stdout=_payload(duration="20"))

        with patch(
            "app.services.marketing_video.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ) as create_process:
            result = await probe_marketing_video(
                b"safe-bounded-video-bytes",
                extension="mp4",
            )

        self.assertEqual(result.duration_seconds, 20)

        args = create_process.await_args.args
        self.assertEqual(args[0], "ffprobe")
        self.assertIn("-show_entries", args)
        self.assertTrue(str(args[-1]).endswith("source.mp4"))

    async def test_mov_probe_uses_ffprobe_as_final_container_authority(
        self,
    ) -> None:
        process = _Process(stdout=_payload(duration="20"))

        with patch(
            "app.services.marketing_video.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ) as create_process:
            result = await probe_marketing_video(
                b"safe-bounded-quicktime-bytes",
                extension="mov",
            )

        self.assertEqual(result.duration_seconds, 20)
        args = create_process.await_args.args
        self.assertEqual(args[0], "ffprobe")
        self.assertTrue(str(args[-1]).endswith("source.mov"))

    async def test_probe_rejects_ffprobe_decode_failure_without_leaking_stderr(
        self,
    ) -> None:
        process = _Process(
            stdout=b"",
            stderr=b"secret untrusted decoder details",
            returncode=1,
        )

        with patch(
            "app.services.marketing_video.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ):
            with self.assertRaisesRegex(
                MarketingValidationError,
                "marketing_media_unreadable",
            ) as error:
                await probe_marketing_video(
                    b"fake-video",
                    extension="mp4",
                )

        self.assertNotIn(
            "secret",
            str(error.exception),
        )


class MarketingVideoFileProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_probe_does_not_materialize_source_bytes(self) -> None:
        process = _Process(
            stdout=_payload(
                width=1080,
                height=1920,
                duration="25",
            )
        )

        with TemporaryDirectory(prefix="aibos-file-probe-test-") as tmp:
            source = Path(tmp) / "source.mp4"
            source.write_bytes(b"trusted-file-backed-video")

            with (
                patch(
                    "app.services.marketing_video.asyncio.create_subprocess_exec",
                    new=AsyncMock(return_value=process),
                ) as create_process,
                patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError(
                        "file-backed probe must not read source into memory"
                    ),
                ),
            ):
                result = await probe_marketing_video_file(source)

        self.assertEqual(result.width, 1080)
        self.assertEqual(result.height, 1920)
        self.assertEqual(result.duration_seconds, 25)

        args = create_process.await_args.args
        self.assertEqual(args[0], "ffprobe")
        self.assertEqual(args[-1], str(source.resolve()))


class MarketingVideoRendererTests(unittest.IsolatedAsyncioTestCase):
    async def test_renderer_builds_vertical_and_landscape_profiles(self) -> None:
        async def launch(*args, **kwargs):
            del kwargs
            output_path = Path(args[-1])
            output_path.write_bytes(b"deterministic-rendered-video")
            return _Process(stdout=b"", returncode=0)

        with TemporaryDirectory(prefix="aibos-render-test-") as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"trusted-source")
            output = root / "variants"

            with patch(
                "app.services.marketing_video.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=launch),
            ) as create_process:
                vertical = await render_marketing_video_variant(
                    source,
                    output,
                    key="vertical_9_16",
                    width=1080,
                    height=1920,
                    aspect_ratio="9:16",
                    duration_seconds=30,
                )

                landscape = await render_marketing_video_variant(
                    source,
                    output,
                    key="landscape_16_9",
                    width=1920,
                    height=1080,
                    aspect_ratio="16:9",
                    duration_seconds=30,
                )

            self.assertEqual(create_process.await_count, 2)

            self.assertEqual(
                (
                    vertical.key,
                    vertical.width,
                    vertical.height,
                    vertical.aspect_ratio,
                    vertical.content_type,
                    vertical.transformation,
                ),
                (
                    "vertical_9_16",
                    1080,
                    1920,
                    "9:16",
                    "video/mp4",
                    "contain_no_crop",
                ),
            )
            self.assertEqual(
                (
                    landscape.key,
                    landscape.width,
                    landscape.height,
                    landscape.aspect_ratio,
                    landscape.content_type,
                    landscape.transformation,
                ),
                (
                    "landscape_16_9",
                    1920,
                    1080,
                    "16:9",
                    "video/mp4",
                    "contain_no_crop",
                ),
            )

            self.assertTrue(vertical.path.is_file())
            self.assertTrue(landscape.path.is_file())

    async def test_renderer_rejects_unknown_profile_before_ffmpeg(self) -> None:
        with TemporaryDirectory(prefix="aibos-render-test-") as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"trusted-source")

            with patch(
                "app.services.marketing_video.asyncio.create_subprocess_exec",
                new=AsyncMock(),
            ) as create_process:
                with self.assertRaisesRegex(
                    MarketingValidationError,
                    "marketing_video_profile_invalid",
                ):
                    await render_marketing_video_variant(
                        source,
                        root / "variants",
                        key="attacker_controlled",
                        width=777,
                        height=999,
                        aspect_ratio="777:999",
                        duration_seconds=30,
                    )

            create_process.assert_not_awaited()

    async def test_renderer_removes_partial_output_on_ffmpeg_failure(self) -> None:
        partial_path: Path | None = None

        async def launch(*args, **kwargs):
            nonlocal partial_path
            del kwargs
            partial_path = Path(args[-1])
            partial_path.write_bytes(b"partial-invalid-output")
            return _Process(
                stdout=b"",
                stderr=b"untrusted decoder diagnostics secret",
                returncode=1,
            )

        with TemporaryDirectory(prefix="aibos-render-test-") as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"trusted-source")

            with patch(
                "app.services.marketing_video.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=launch),
            ):
                with self.assertRaisesRegex(
                    MarketingValidationError,
                    "marketing_video_render_failed",
                ) as error:
                    await render_marketing_video_variant(
                        source,
                        root / "variants",
                        key="vertical_9_16",
                        width=1080,
                        height=1920,
                        aspect_ratio="9:16",
                        duration_seconds=30,
                    )

            self.assertIsNotNone(partial_path)
            assert partial_path is not None
            self.assertFalse(partial_path.exists())
            self.assertNotIn("secret", str(error.exception))

    async def test_renderer_uses_argument_array_for_untrusted_filename(self) -> None:
        captured_args: tuple[object, ...] | None = None
        captured_kwargs: dict[str, object] | None = None

        async def launch(*args, **kwargs):
            nonlocal captured_args, captured_kwargs
            captured_args = args
            captured_kwargs = kwargs
            Path(args[-1]).write_bytes(b"rendered")
            return _Process(stdout=b"", returncode=0)

        with TemporaryDirectory(prefix="aibos-render-test-") as tmp:
            root = Path(tmp)
            source = root / "source $(touch should-not-run).mp4"
            source.write_bytes(b"trusted-source")

            with patch(
                "app.services.marketing_video.asyncio.create_subprocess_exec",
                new=AsyncMock(side_effect=launch),
            ):
                await render_marketing_video_variant(
                    source,
                    root / "variants",
                    key="vertical_9_16",
                    width=1080,
                    height=1920,
                    aspect_ratio="9:16",
                    duration_seconds=30,
                )

        self.assertIsNotNone(captured_args)
        self.assertIsNotNone(captured_kwargs)
        assert captured_args is not None
        assert captured_kwargs is not None

        self.assertEqual(captured_args[0], "ffmpeg")
        self.assertIn(str(source.resolve()), captured_args)
        self.assertEqual(
            captured_args.count(str(source.resolve())),
            1,
        )

        self.assertIn("-nostdin", captured_args)
        self.assertIn("libx264", captured_args)
        self.assertIn("yuv420p", captured_args)
        self.assertIn("+faststart", captured_args)
        self.assertIn("-map_metadata", captured_args)
        self.assertIn("-map_chapters", captured_args)

        # Production resource envelope: do not allow FFmpeg/x264 to silently
        # return to auto-threading on memory-constrained workers.
        input_index = captured_args.index("-i")
        decoder_threads_index = captured_args.index("-threads")
        self.assertLess(decoder_threads_index, input_index)
        self.assertEqual(
            captured_args[decoder_threads_index + 1],
            "1",
        )

        filter_threads_index = captured_args.index("-filter_threads")
        self.assertEqual(
            captured_args[filter_threads_index + 1],
            "1",
        )

        encoder_threads_index = captured_args.index("-threads:v")
        self.assertEqual(
            captured_args[encoder_threads_index + 1],
            "1",
        )

        preset_index = captured_args.index("-preset")
        self.assertEqual(
            captured_args[preset_index + 1],
            "veryfast",
        )

        self.assertEqual(
            captured_kwargs.get("stderr"),
            asyncio.subprocess.DEVNULL,
        )

        self.assertNotIn("shell", captured_kwargs)
        self.assertNotIn("bash", captured_args)
        self.assertNotIn("sh", captured_args)


if __name__ == "__main__":
    unittest.main()
