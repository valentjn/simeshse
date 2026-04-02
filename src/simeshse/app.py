# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""FastAPI application setup."""

import asyncio
import json
import logging
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto
from functools import partial, wraps
from pathlib import Path
from random import Random
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from time import time
from traceback import format_exc, print_exc
from typing import TYPE_CHECKING, Annotated

import jinja2
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from simeshse.directories import MEDIA_DIR_NAME, THUMBNAILS_DIR_NAME, create_directories

from . import database, preprocessor
from .directories import get_resources_dir

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

_logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(env_prefix="SIMESHSE_")

    data_dir: Path
    """Directory where the data (database, media files, thumbnails) is stored."""


@dataclass(kw_only=True)
class DisplayedMediaItem:
    """Media item with additional information for display in the UI."""

    row: database.MediaItem
    """Media item database row."""
    angle: float
    """Rotation angle in degrees of the media item."""
    z_index: int
    """Z-index of the media item, used for layering in the UI."""


class Direction(StrEnum):
    """Direction for moving sections."""

    UP = auto()
    """Move the section up."""
    DOWN = auto()
    """Move the section down."""


class MediaItemAction(StrEnum):
    """Action for updating media items."""

    UPDATE_CAPTIONS = auto()
    """Update captions of media items."""
    UPDATE_SECTIONS = auto()
    """Move media items to another section."""
    DELETE = auto()
    """Delete media items."""


class AddMediaItemsResponse(BaseModel):
    """Response model for adding media items."""

    redirect_url: str
    """URL to redirect to after adding media items."""


def create_app() -> FastAPI:  # noqa: C901, PLR0915
    """Create and configure the FastAPI application."""
    set_up_logging()
    settings = Settings()  # type: ignore[call-arg]  # ty: ignore[missing-argument]
    engine = database.create_engine(settings.data_dir)
    create_directories(settings.data_dir)
    DatabaseSessionDep = Annotated[  # noqa: N806
        AsyncSession,
        Depends(partial(database.create_session_generator, engine)),
    ]
    TemplatesDep = Annotated[Jinja2Templates, Depends(get_templates)]  # noqa: N806
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """Lifespan function to set up the database before the app starts."""
        await database.create_tables(engine)
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def handle_exceptions(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        try:
            return await call_next(request)
        except Exception:
            if is_admin(request):
                print_exc()
                return PlainTextResponse("".join(format_exc()), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            raise

    app.mount("/static", StaticFiles(directory=get_resources_dir() / "static"))
    for dir_name in [MEDIA_DIR_NAME, THUMBNAILS_DIR_NAME]:
        app.mount(f"/{dir_name}", StaticFiles(directory=settings.data_dir / dir_name))

    @app.get("/", response_class=HTMLResponse)
    @app.get("/admin", response_class=HTMLResponse)
    async def get_root(db_session: DatabaseSessionDep, templates: TemplatesDep, request: Request) -> HTMLResponse:
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
                    DisplayedMediaItem(row=media_item, angle=random.gauss(0.0, 3.0), z_index=1_000_000 - idx)
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

    @app.post("/admin/integrity/fix")
    @acquire_lock(lock)
    async def fix_integrity_problems(db_session: DatabaseSessionDep, request: Request) -> RedirectResponse:
        """Fix integrity problems in the database and on disk."""
        _logger.info("checking for integrity problems")
        problems = await database.IntegrityProblems.check(db_session, settings.data_dir)
        _logger.info("fixing integrity problems")
        await problems.fix(db_session, settings.data_dir)
        await db_session.commit()
        return redirect_to_admin(request, section=None)

    @app.put("/admin/sections/{section_id}/media-items")
    @acquire_lock(lock)
    async def add_media_items(
        db_session: DatabaseSessionDep,
        request: Request,
        section_id: int,
        files: list[UploadFile],
        created_ats: Annotated[str | None, Form()] = None,
    ) -> AddMediaItemsResponse:
        """Add media items to a section."""
        parsed_created_ats = json.loads(created_ats or "[]")
        if not isinstance(parsed_created_ats, list) or not all(isinstance(t, (int, float)) for t in parsed_created_ats):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid created_ats")
        with TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            for file, created_at in zip(files, parsed_created_ats, strict=True):
                input_path = temp_dir / (file.filename or "uploaded_file").replace("/", "_")
                _logger.info("processing uploaded file %s with created_at %d", file.filename, created_at)
                with input_path.open("wb") as input_file:
                    copyfileobj(file.file, input_file)
                async with asyncio.timeout(30.0):
                    preprocessed = await preprocessor.preprocess(
                        input_path=input_path,
                        data_dir=settings.data_dir,
                        created_at=datetime.fromtimestamp(created_at, tz=UTC).replace(tzinfo=None),
                    )
                media_item = database.MediaItem(
                    section_id=section_id,
                    is_video=preprocessed.is_video,
                    created_at=preprocessed.created_at,
                    caption="",
                    path=preprocessed.path.relative_to(settings.data_dir),
                    thumbnail_path=preprocessed.thumbnail_path.relative_to(settings.data_dir),
                    thumbnail_width=preprocessed.thumbnail_size.width,
                    thumbnail_height=preprocessed.thumbnail_size.height,
                )
                _logger.info(
                    "adding media item with path %s and thumbnail path %s to section ID %d",
                    media_item.path,
                    media_item.thumbnail_path,
                    section_id,
                )
                db_session.add(media_item)
        response = AddMediaItemsResponse(
            redirect_url=get_admin_redirect_url(request, section=await db_session.get(database.Section, section_id))
        )
        await db_session.commit()
        return response

    @app.post("/admin/sections")
    @acquire_lock(lock)
    async def add_section(
        db_session: DatabaseSessionDep,
        request: Request,
        *,
        name: Annotated[str | None, Form()] = None,
        order_index: Annotated[int, Form()],
    ) -> RedirectResponse:
        """Add a new section."""
        sections = await db_session.exec(select(database.Section).where(database.Section.order_index >= order_index))
        for section in sections:
            section.order_index += 1
        section = database.Section(name=name or "", order_index=order_index)
        _logger.info("adding section with name %s and order_index %d", section.name, section.order_index)
        db_session.add(section)
        response = redirect_to_admin(request, section=section)
        await db_session.commit()
        return response

    @app.post("/admin/sections/{section_id}/rename")
    @acquire_lock(lock)
    async def rename_section(
        db_session: DatabaseSessionDep, request: Request, section_id: int, name: Annotated[str | None, Form()] = None
    ) -> RedirectResponse:
        """Rename a section."""
        section = await db_session.get(database.Section, section_id)
        if not section:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"section {section_id} not found")
        section.name = name or ""
        _logger.info("renaming section ID %d to %s", section_id, section.name)
        response = redirect_to_admin(request, section=section)
        await db_session.commit()
        return response

    @app.post("/admin/sections/{section_id}/move")
    @acquire_lock(lock)
    async def move_section(
        db_session: DatabaseSessionDep, request: Request, section_id: int, direction: Direction
    ) -> RedirectResponse:
        """Move a section up or down."""
        section = await db_session.get(database.Section, section_id)
        if not section:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"section {section_id} not found")
        match direction:
            case Direction.UP:
                swap_section = (
                    await db_session.exec(
                        select(database.Section)
                        .where(col(database.Section.order_index) < section.order_index)
                        .order_by(col(database.Section.order_index).desc())
                    )
                ).first()
            case Direction.DOWN:
                swap_section = (
                    await db_session.exec(
                        select(database.Section)
                        .where(col(database.Section.order_index) > section.order_index)
                        .order_by(col(database.Section.order_index).asc())
                    )
                ).first()
            case _:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid direction {direction}")
        response = redirect_to_admin(request, section=section)
        if swap_section:
            section.order_index, swap_section.order_index = swap_section.order_index, section.order_index
            _logger.info("swapping section ID %d with section ID %d", section.id, swap_section.id)
            await db_session.commit()
        return response

    @app.post("/admin/sections/{section_id}/delete")
    @acquire_lock(lock)
    async def delete_section(db_session: DatabaseSessionDep, request: Request, section_id: int) -> RedirectResponse:
        """Delete a section."""
        section = await db_session.get(database.Section, section_id)
        if not section:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"section {section_id} not found")
        media_items = await db_session.exec(
            select(database.MediaItem).where(database.MediaItem.section_id == section_id)
        )
        if media_items.first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"section {section_id} not empty")
        _logger.info("deleting section ID %d with name %s", section_id, section.name)
        await db_session.delete(section)
        await db_session.commit()
        return redirect_to_admin(request, section=None)

    @app.post("/admin/sections/{section_id}/media-items")
    @acquire_lock(lock)
    async def update_media_items(  # noqa: PLR0913
        db_session: DatabaseSessionDep,
        request: Request,
        section_id: int,
        action: Annotated[MediaItemAction, Form()],
        media_item_ids: Annotated[list[int] | None, Form()] = None,
        captions: Annotated[list[str] | None, Form()] = None,
        section_ids: Annotated[list[int] | None, Form()] = None,
        media_item_ids_to_delete: Annotated[list[int] | None, Form()] = None,
    ) -> RedirectResponse:
        """Update media items based on the specified action."""
        paths_to_delete = []
        match action:
            case MediaItemAction.UPDATE_CAPTIONS:
                await update_captions(db_session, media_item_ids, captions)
            case MediaItemAction.UPDATE_SECTIONS:
                await update_sections(db_session, media_item_ids, section_ids)
            case MediaItemAction.DELETE:
                paths_to_delete += await delete_media(db_session, media_item_ids_to_delete)
            case _:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"invalid action {action}")
        response = redirect_to_admin(request, section=await db_session.get(database.Section, section_id))
        await db_session.commit()
        for path in paths_to_delete:
            _logger.info("deleting file at path %s because its media item was deleted", path)
            (settings.data_dir / path).unlink(missing_ok=True)
        return response

    _logger.info("application created successfully")
    return app


def set_up_logging() -> None:
    """Set up logging for the application."""
    package_logger = logging.getLogger("simeshse")
    if package_logger.hasHandlers():
        return
    package_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger.addHandler(handler)


async def update_captions(
    db_session: AsyncSession, media_item_ids: Sequence[int] | None, captions: Sequence[str] | None
) -> None:
    """Update captions of media items."""
    for media_id, caption in zip(media_item_ids or [], captions or [], strict=True):
        media_item = await db_session.get(database.MediaItem, media_id)
        if not media_item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"media item {media_id} not found")
        _logger.info("updating caption of media item ID %d to %s", media_id, caption)
        media_item.caption = caption


async def update_sections(
    db_session: AsyncSession, media_item_ids: Sequence[int] | None, section_ids: Sequence[int] | None
) -> None:
    """Move media items to another section."""
    all_section_ids = list(await db_session.exec(select(database.Section.id)))
    for media_item_id, section_id in zip(media_item_ids or [], section_ids or [], strict=True):
        media_item = await db_session.get(database.MediaItem, media_item_id)
        if not media_item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"media item {media_item_id} not found")
        if section_id not in all_section_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"section {section_id} not found")
        _logger.info("moving media item %d from section %d to %d", media_item_id, media_item.section_id, section_id)
        media_item.section_id = section_id


async def delete_media(db_session: AsyncSession, media_item_ids: Sequence[int] | None) -> list[Path]:
    """Delete media items."""
    paths_to_delete = []
    for media_item_id in media_item_ids or []:
        media_item = await db_session.get(database.MediaItem, media_item_id)
        if not media_item:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"media item {media_item_id} not found")
        paths_to_delete.append(media_item.path)
        paths_to_delete.append(media_item.thumbnail_path)
        _logger.info(
            "deleting media item ID %d with path %s and thumbnail path %s",
            media_item_id,
            media_item.path,
            media_item.thumbnail_path,
        )
        await db_session.delete(media_item)
    return paths_to_delete


def is_admin(request: Request) -> bool:
    """Check if the request is for an admin page."""
    root_path = get_root_path(request)
    return request.url.path == f"{root_path}/admin" or request.url.path.startswith(f"{root_path}/admin/")


def redirect_to_admin(request: Request, *, section: database.Section | None) -> RedirectResponse:
    """Redirect to the admin page, optionally with a section anchor."""
    return RedirectResponse(url=get_admin_redirect_url(request, section=section), status_code=status.HTTP_303_SEE_OTHER)


def get_admin_redirect_url(request: Request, *, section: database.Section | None) -> str:
    """Get the URL to redirect to the admin page, optionally with a section anchor."""
    url = f"{get_root_path(request)}/admin"
    if section:
        url += f"#{slugify(section.name)}"
    return url


def get_root_path(request: Request) -> str:
    """Get the root path from a request."""
    root_path = request.scope.get("root_path", "")
    if not isinstance(root_path, str):
        msg = f"root_path must be str, got: {type(root_path).__name__}"
        raise TypeError(msg)
    return root_path


def get_templates() -> Jinja2Templates:
    """Get the ``Jinja2Templates`` instance for rendering templates."""
    environment = jinja2.Environment(
        loader=jinja2.FileSystemLoader(get_resources_dir() / "templates"),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    return Jinja2Templates(env=environment)


def acquire_lock[**P, R](lock: asyncio.Lock) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap a function to acquire the lock before executing and release it afterwards."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            async with lock:
                return await func(*args, **kwargs)  # ty: ignore[invalid-return-type, invalid-argument-type]

        return wrapper  # ty:ignore[invalid-return-type]

    return decorator  # ty:ignore[invalid-return-type]


def slugify(string: str) -> str:
    """Convert a string to a slug (lowercase, words separated by hyphens)."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", string.lower()).strip("-"))


app = create_app()
