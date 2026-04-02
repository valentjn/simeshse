# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""FastAPI application setup."""

import logging
from contextlib import asynccontextmanager
from functools import partial
from traceback import format_exc, print_exc
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel.ext.asyncio.session import AsyncSession

from simeshse.directories import MEDIA_DIR_NAME, THUMBNAILS_DIR_NAME, create_directories
from simeshse.routers.util import ADMIN_PREFIX, is_admin
from simeshse.settings import Settings

from . import database
from .directories import get_resources_dir
from .routers import admin, media

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

_logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    set_up_logging()
    settings = Settings()  # type: ignore[call-arg]  # ty: ignore[missing-argument]
    engine = database.create_engine(settings.data_dir)
    create_directories(settings.data_dir)
    DatabaseSessionDep = Annotated[  # noqa: N806
        AsyncSession,
        Depends(partial(database.create_session_generator, engine)),
    ]

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
    app.include_router(media.create_router(settings, DatabaseSessionDep))
    app.include_router(admin.create_router(settings, DatabaseSessionDep), prefix=ADMIN_PREFIX)
    _logger.info("application created successfully")
    return app


def set_up_logging() -> None:
    """Set up logging for the application."""
    package_logger = logging.getLogger("simeshse")
    if package_logger.hasHandlers():
        return
    package_logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    package_logger.addHandler(handler)


app = create_app()
