# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Management of directories."""

from pathlib import Path

MEDIA_DIR_NAME = "media"
"""Name of the directory where media files are stored."""
THUMBNAILS_DIR_NAME = "thumbnails"
"""Name of the directory where thumbnail files are stored."""


def create_directories(data_dir: Path) -> None:
    """Create the media and thumbnails directories if they don't exist."""
    (data_dir / MEDIA_DIR_NAME).mkdir(parents=True, exist_ok=True)
    (data_dir / THUMBNAILS_DIR_NAME).mkdir(parents=True, exist_ok=True)


def get_resources_dir() -> Path:
    """Get the path to the resources directory."""
    return Path(__file__).with_name("resources")
