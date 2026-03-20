#!/usr/bin/env python
# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Run the development server with some example data.

All arguments are forwarded to ``fastapi dev``.
"""

import asyncio
import logging
import os
import sys
from asyncio import sleep
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

from simeshse import database
from simeshse.preprocessor import preprocess

if TYPE_CHECKING:
    from collections.abc import Sequence

_logger = logging.getLogger(__name__)


async def main() -> None:
    """Run main entry point."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root_dir = Path(__file__).parent.parent
    with TemporaryDirectory() as data_dir_str:
        data_dir = Path(data_dir_str)
        environment = {**os.environ, "SIMESHSE_DATA_DIR": data_dir_str}
        media_items = await download_example_media_items(data_dir)
        await insert_example_media_items(data_dir, media_items)
        _logger.info("starting development server")
        try:
            process = await asyncio.create_subprocess_exec(
                "uv", "run", "fastapi", "dev", *sys.argv[1:], cwd=root_dir, env=environment
            )
            await process.wait()
        except KeyboardInterrupt:
            sys.exit(1)
        sys.exit(process.returncode)


async def download_example_media_items(data_dir: Path) -> list[database.MediaItem]:
    """Download example media items."""
    images = [
        (
            "https://upload.wikimedia.org/wikipedia/commons/0/05/"
            "View_of_Empire_State_Building_from_Rockefeller_Center_New_York_City_dllu.jpg",
            1,
            "Empire State Building",
            datetime(2000, 1, 2, 10, 3, 4, tzinfo=timezone(timedelta(hours=-5))),
        ),
        (
            "https://upload.wikimedia.org/wikipedia/commons/2/25/Majestic_Liberty.jpg",
            1,
            "Statue of Liberty",
            datetime(2000, 1, 2, 11, 4, 5, tzinfo=timezone(timedelta(hours=-5))),
        ),
        (
            "https://upload.wikimedia.org/wikipedia/commons/4/4f/US_Capitol_west_side.JPG",
            2,
            "US Capitol",
            datetime(2000, 1, 10, 12, 5, 6, tzinfo=timezone(timedelta(hours=-5))),
        ),
    ]
    media_items = []
    with TemporaryDirectory() as input_dir_str:
        input_dir = Path(input_dir_str)
        for url, section_id, caption, created_at in images:
            filename = url.rsplit("/", 1)[-1]
            input_path = input_dir / filename
            _logger.info("downloading %s", url)
            request = Request(  # noqa: S310
                url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0"}
            )
            await sleep(1.0)
            with urlopen(request) as response:  # noqa: ASYNC210, S310
                input_path.write_bytes(response.read())
            _logger.info("preprocessing %s", input_path)
            preprocessed = await preprocess(input_path=input_path, data_dir=data_dir, created_at=created_at)
            media_items.append(
                database.MediaItem(
                    section_id=section_id,
                    is_video=preprocessed.is_video,
                    created_at=preprocessed.created_at,
                    caption=caption,
                    path=preprocessed.path.relative_to(data_dir),
                    thumbnail_path=preprocessed.thumbnail_path.relative_to(data_dir),
                    thumbnail_width=preprocessed.thumbnail_size.width,
                    thumbnail_height=preprocessed.thumbnail_size.height,
                )
            )
    return media_items


async def insert_example_media_items(data_dir: Path, media_items: Sequence[database.MediaItem]) -> None:
    """Insert example media items into the database."""
    sections = [
        database.Section(id=1, order_index=1, name="New York"),
        database.Section(id=2, order_index=0, name="Washington, D.C."),
    ]
    _logger.info("inserting example media items into the database")
    engine = database.create_engine(data_dir)
    await database.create_tables(engine)
    async with database.create_session(engine) as session:
        for section in sections:
            session.add(section)
        for media_item in media_items:
            session.add(media_item)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
