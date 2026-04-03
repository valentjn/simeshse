# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Preprocess media files (photos and videos) by rescaling them and extracting metadata."""

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from logging import getLogger
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from PIL import Image

from simeshse.directories import MEDIA_DIR_NAME, THUMBNAILS_DIR_NAME, create_directories

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_logger = getLogger(__name__)


@dataclass
class Size:
    """Width and height of an image or video."""

    width: int
    """Width in pixels."""
    height: int
    """Height in pixels."""


@dataclass
class _TargetSize:
    """Target size for rescaling an image or video.

    At least one of width or height must not be ``None``. The aspect ratio of the media item is preserved if only one of
    the dimensions is specified.
    """

    width: int | None
    """Target width in pixels, or ``None`` if only the height should be considered."""
    height: int | None
    """Target height in pixels, or ``None`` if only the width should be considered."""

    def __post_init__(self) -> None:
        """Validate that at least one of width and height is not ``None``."""
        if self.width is None and self.height is None:
            msg = "width and height must not both be None"
            raise ValueError(msg)


@dataclass
class MediaItem:
    """Preprocessed media item."""

    is_video: bool
    """Indicates if the media item is a video."""
    created_at: datetime
    """Local time when the media item was created."""
    path: Path
    """File path to the media item."""
    thumbnail_path: Path
    """File path to the thumbnail of the media item."""
    thumbnail_size: Size
    """Size of the thumbnail."""


_MEDIA_IMAGE_TARGET_SIZE = _TargetSize(4096, 4096)
_MEDIA_VIDEO_TARGET_SIZE = _TargetSize(768, 768)
_THUMBNAIL_TARGET_SIZE = _TargetSize(None, 256)


async def preprocess(*, input_path: Path, data_dir: Path, created_at: datetime | None = None) -> MediaItem:
    """Preprocess the given media file and return the preprocessed media item."""
    _logger.info("preprocessing media item at path: %s", input_path)
    is_video = _is_video(input_path.suffix)
    if is_video:
        created_at = await _read_creation_time_from_video(input_path)
    elif not created_at:
        msg = "created_at must not be None for images"
        raise ValueError(msg)
    uuid = uuid4()
    media_item_path = data_dir / MEDIA_DIR_NAME / _format_filename(created_at, uuid, ".mp4" if is_video else ".jpg")
    thumbnail_path = data_dir / THUMBNAILS_DIR_NAME / _format_filename(created_at, uuid, ".jpg")
    create_directories(data_dir)
    try:
        if is_video:
            await _rescale_video(input_path, media_item_path, _MEDIA_VIDEO_TARGET_SIZE)
        else:
            _rescale_image(input_path, media_item_path, _MEDIA_IMAGE_TARGET_SIZE)
    except Exception:
        _logger.exception("failed to rescale media item: %s", input_path)
        copyfile(input_path, media_item_path)
    async with AsyncExitStack() as exit_stack:
        if is_video:
            temporary_thumbnail_path = await exit_stack.enter_async_context(_extract_first_frame_from_video(input_path))
        else:
            temporary_thumbnail_path = input_path
        thumbnail_size = _rescale_image(temporary_thumbnail_path, thumbnail_path, _THUMBNAIL_TARGET_SIZE)
    return MediaItem(
        is_video=is_video,
        created_at=created_at,
        path=media_item_path,
        thumbnail_path=thumbnail_path,
        thumbnail_size=thumbnail_size,
    )


def _is_video(suffix: str) -> bool:
    """Return whether a media item with the given suffix is a video."""
    result = {
        ".bmp": False,
        ".jpg": False,
        ".jpeg": False,
        ".png": False,
        ".mp4": True,
        ".mov": True,
    }.get(suffix.lower())
    if result is None:
        msg = f"unsupported suffix: {suffix}"
        raise ValueError(msg)
    return result


def _format_filename(created_at: datetime, uuid: UUID, suffix: str) -> str:
    """Format the filename for a media item or thumbnail."""
    return f"{created_at.strftime('%Y-%m-%d_%H-%M-%S')}_{uuid}{suffix}"


def _rescale_image(input_path: Path, output_path: Path, target_size: _TargetSize, *, quality: int = 95) -> Size:
    """Rescale the given image to the target size and save it to the output path."""
    _logger.info("rescaling image")
    with Image.open(input_path) as image:
        input_size = Size(image.width, image.height)
        rescaled_size = _scale_to_maximum_width_or_height(input_size, target_size)
        if input_size == rescaled_size and image.format == "JPEG":
            copyfile(input_path, output_path)
        else:
            rescaled_image = image.resize(
                (rescaled_size.width, rescaled_size.height), resample=Image.Resampling.LANCZOS
            )
            rescaled_image.save(output_path, format="JPEG", quality=quality)
        return rescaled_size


async def _rescale_video(input_path: Path, output_path: Path, target_size: _TargetSize) -> Size:
    """Rescale the given video to the target size and save it to the output path."""
    _logger.info("rescaling video")
    rescaled_width_height_divisor = 2
    ffprobe_json = await _run_ffprobe(input_path, "stream=width,height:stream_side_data=rotation")
    streams = ffprobe_json["streams"]
    for stream in streams:
        if "width" in stream and "height" in stream:
            input_size = Size(width=stream["width"], height=stream["height"])
            for side_data in stream.get("side_data_list", []):
                if "rotation" in side_data:
                    rotation_angle = float(side_data["rotation"])
                    break
            else:
                rotation_angle = 0.0
            break
    else:
        msg = f"could not determine resolution: {input_path}"
        raise RuntimeError(msg)
    rescaled_size = _scale_to_maximum_width_or_height(input_size, target_size)
    rescaled_size.width = _ensure_divisibility(rescaled_size.width, rescaled_width_height_divisor)
    rescaled_size.height = _ensure_divisibility(rescaled_size.height, rescaled_width_height_divisor)
    if rotation_angle % 180.0 != 0.0:
        rescaled_size = Size(width=rescaled_size.height, height=rescaled_size.width)
    await _run_command(
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        f"scale={rescaled_size.width}:{rescaled_size.height}",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "fast",
        output_path,
    )
    return rescaled_size


def _ensure_divisibility(number: int, divisor: int) -> int:
    """Return the given number rounded to the nearest multiple of the divisor."""
    return round(number / divisor) * divisor


async def _read_creation_time_from_video(path: Path) -> datetime:
    """Read the creation time of the given video file."""
    _logger.info("reading creation time from video")
    key_name = "com.apple.quicktime.creationdate"
    ffprobe_json = await _run_ffprobe(path, f"format_tags={key_name}")
    tags = ffprobe_json["format"]["tags"]
    return (
        datetime.fromisoformat(creation_time_str).replace(tzinfo=None)
        if (creation_time_str := tags.get(key_name))
        else datetime.now(tz=None)  # noqa: DTZ005
    )


@asynccontextmanager
async def _extract_first_frame_from_video(video_path: Path) -> AsyncGenerator[Path]:
    """Extract the first frame from the given video and return the path to the extracted image."""
    _logger.info("extracting first frame from video")
    with TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        output_path = temp_dir / "frame.png"
        await _run_command(
            "ffmpeg", "-y", "-i", video_path, "-vf", "select=eq(n\\,0)", "-f", "image2", "-c", "png", output_path
        )
        yield output_path


async def _run_ffprobe(video_path: Path, entries: str) -> Any:  # noqa: ANN401
    """Run ffprobe with the given arguments and return the parsed JSON output."""
    stdout = await _run_command(
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        entries,
        video_path,
    )
    return json.loads(stdout)


async def _run_command(*arguments: str | Path) -> bytes:
    """Run the given command and return stdout."""
    process = await asyncio.create_subprocess_exec(*arguments, stdout=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        msg = (
            f"command terminated with non-zero exit code {process.returncode}, "
            f"stdout: {stdout.decode(errors='replace')}"
        )
        raise RuntimeError(msg)
    return stdout


def _scale_to_maximum_width_or_height(input_size: Size, target_size: _TargetSize) -> Size:
    """Scale the input size to fit within the target size while preserving the aspect ratio."""
    input_aspect_ratio = input_size.width / input_size.height
    if target_size.width is not None:
        if target_size.height is not None:
            if input_size.width <= target_size.width and input_size.height <= target_size.height:
                return input_size
            if input_aspect_ratio * target_size.height >= target_size.width:
                return Size(width=round(input_aspect_ratio * target_size.height), height=target_size.height)
        return Size(width=target_size.width, height=round(target_size.width / input_aspect_ratio))
    if target_size.height is not None:
        return Size(width=round(input_aspect_ratio * target_size.height), height=target_size.height)
    msg = "one of target width and height must not be None"
    raise ValueError(msg)
