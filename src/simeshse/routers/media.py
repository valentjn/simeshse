# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Media router."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from random import Random
from time import time
from typing import TYPE_CHECKING, Annotated, Any

import jinja2
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import col, select

from simeshse import database
from simeshse.directories import get_resources_dir
from simeshse.routers.util import ADMIN_PREFIX

from .util import is_admin, slugify

if TYPE_CHECKING:
    from simeshse.settings import Settings

_logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class _DisplayedMediaItem:
    """Media item with additional information for display in the UI."""

    row: database.MediaItem
    """Media item database row."""
    angle: float
    """Rotation angle in degrees of the media item."""
    z_index: int
    """Z-index of the media item, used for layering in the UI."""


def create_router(settings: Settings, database_session_type: Any) -> APIRouter:  # noqa: ANN401
    """Create media router."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    @router.get(ADMIN_PREFIX, response_class=HTMLResponse)
    async def get_media(
        db_session: database_session_type,
        templates: _TemplatesDep,
        request: Request,
    ) -> HTMLResponse:
        """Render the main page."""
        inkjet_license = (get_resources_dir() / "static/admin_assets/LICENSE.inkjet.txt").read_text(encoding="utf-8")
        last_updated_at = datetime.fromtimestamp(
            database.get_path(settings.data_dir).stat().st_mtime, tz=datetime.now().astimezone().tzinfo
        )
        integrity_problems = (
            await database.IntegrityProblems.check(db_session, settings.data_dir) if is_admin(request) else None
        )
        sections = await db_session.exec(select(database.Section).order_by(col(database.Section.order_index)))
        media_items = await db_session.exec(
            select(database.MediaItem).order_by(col(database.MediaItem.created_at).desc())
        )
        section_id_to_media_items = defaultdict(list)
        for media_item in media_items:
            section_id_to_media_items[media_item.section_id].append(media_item)
        random = Random(time() // 3600)  # noqa: S311
        dict_sections = [
            {
                "id": section.id,
                "name": section.name,
                "slug": slugify(section.name),
                "order_index": section.order_index,
                "media_items": [
                    _DisplayedMediaItem(row=media_item, angle=random.gauss(0.0, 3.0), z_index=1_000_000 - idx)
                    for idx, media_item in enumerate(section_id_to_media_items[section.id])
                ],
            }
            for section in sections
            if section.id is not None
        ]
        context = {
            "inkjet_license": inkjet_license,
            "integrity_problems": integrity_problems,
            "is_admin": is_admin(request),
            "last_updated_at": last_updated_at,
            "root_path": request.scope.get("root_path", ""),
            "sections": dict_sections,
        }
        return templates.TemplateResponse(request=request, name="main.html.jinja", context=context)

    return router


def _get_templates() -> Jinja2Templates:
    """Get the ``Jinja2Templates`` instance for rendering templates."""
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(get_resources_dir() / "templates"),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    return Jinja2Templates(env=environment)


type _TemplatesDep = Annotated[Jinja2Templates, Depends(_get_templates)]
