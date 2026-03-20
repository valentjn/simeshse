# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Database management."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Self, override

from pydantic import NaiveDatetime, NonNegativeInt, PositiveInt  # noqa: TC002
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import Column, DateTime, Field, SQLModel, String, TypeDecorator, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from simeshse.directories import MEDIA_DIR_NAME, THUMBNAILS_DIR_NAME

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from sqlalchemy import Dialect

_logger = getLogger(__name__)


class PathType(TypeDecorator[Path]):
    """Custom SQLAlchemy type for storing file paths as strings."""

    impl = String

    @override
    def process_bind_param(self, value: Path | None, dialect: Dialect) -> str | None:
        return str(value) if value is not None else None

    @override
    def process_result_value(self, value: str | None, dialect: Dialect) -> Path | None:
        return Path(value) if value is not None else None


class Section(SQLModel, table=True):
    """Group of media items (e.g., photos taken at the same location)."""

    id: int | None = Field(None, primary_key=True)
    """Unique identifier of the section."""
    name: str
    """Name of the section, used as heading."""
    order_index: NonNegativeInt
    """Sections are ordered by this index in the UI."""


class MediaItem(SQLModel, table=True):
    """Photo or video."""

    id: int | None = Field(None, primary_key=True)
    """Unique identifier of the media item."""
    section_id: int = Field(index=True)
    """ID of the section this media item belongs to."""
    is_video: bool
    """Indicates if the media item is a video."""
    created_at: NaiveDatetime = Field(sa_column=Column(DateTime(timezone=False)))
    """Local time when the media item was created."""
    caption: str
    """Caption or description of the media item."""
    path: Path = Field(sa_column=Column(PathType))
    """File path to the media item relative to the data directory."""
    thumbnail_path: Path = Field(sa_column=Column(PathType))
    """File path to the thumbnail of the media item relative to the data directory."""
    thumbnail_width: PositiveInt
    """Width of the thumbnail in pixels."""
    thumbnail_height: PositiveInt
    """Height of the thumbnail in pixels."""


def get_path(data_dir: Path) -> Path:
    """Get the path to the SQLite database file."""
    return data_dir / "database.sqlite"


def create_engine(data_dir: Path) -> AsyncEngine:
    """Create an asynchronous SQLAlchemy engine for the SQLite database."""
    data_dir.mkdir(parents=True, exist_ok=True)
    return create_async_engine(f"sqlite+aiosqlite:///{get_path(data_dir)}")


@asynccontextmanager
async def create_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Create an asynchronous SQLAlchemy session."""
    async with AsyncSession(engine) as session:
        yield session


async def create_session_generator(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """Create an asynchronous SQLAlchemy session, returning an async generator."""
    async with AsyncSession(engine) as session:
        yield session


async def create_tables(engine: AsyncEngine) -> None:
    """Create database and tables if they don't exist."""
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


@dataclass
class IntegrityProblem(ABC):
    """Problem found during integrity checking of the database."""

    @classmethod
    @abstractmethod
    async def check(cls, db_session: AsyncSession, data_dir: Path) -> Sequence[Self]:
        """Check the database for problems of this type and return a list of problems found."""
        raise NotImplementedError

    @abstractmethod
    async def refresh(self, db_session: AsyncSession) -> None:
        """Refresh any database objects associated with this problem."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    async def fix(cls, db_session: AsyncSession, data_dir: Path, problems: Sequence[Self]) -> None:
        """Fix the given problems in the database where applicable."""
        raise NotImplementedError


@dataclass
class SectionOrderIndexProblem(IntegrityProblem):
    """Problem where a section has an incorrect order index."""

    section: Section
    """Section with the incorrect order index."""
    expected_order_index: int
    """Expected order index of the section based on its position in the list."""

    @override
    @classmethod
    async def check(cls, db_session: AsyncSession, data_dir: Path) -> Sequence[SectionOrderIndexProblem]:
        sections = await db_session.exec(select(Section).order_by(col(Section.order_index)))
        problems = []
        for expected_order_index, section in enumerate(sections):
            if section.order_index != expected_order_index:
                problems.append(SectionOrderIndexProblem(section, expected_order_index))
        return problems

    @override
    async def refresh(self, db_session: AsyncSession) -> None:
        await db_session.refresh(self.section)

    @override
    @classmethod
    async def fix(
        cls,
        db_session: AsyncSession,
        data_dir: Path,
        problems: Sequence[SectionOrderIndexProblem],
    ) -> None:  # ty: ignore[invalid-method-override]
        for problem in problems:
            await problem.refresh(db_session)
            section = await db_session.get(Section, problem.section.id)
            if section:
                _logger.info(
                    "fixing order index of section ID %d from %d to %d",
                    section.id,
                    section.order_index,
                    problem.expected_order_index,
                )
                section.order_index = problem.expected_order_index
        await db_session.commit()


@dataclass
class MediaSectionIDProblem(IntegrityProblem):
    """Problem where a media item references a nonexistent section."""

    media_item: MediaItem
    """Media item referencing the nonexistent section."""

    @override
    @classmethod
    async def check(cls, db_session: AsyncSession, data_dir: Path) -> Sequence[MediaSectionIDProblem]:
        section_ids = list(await db_session.exec(select(Section.id)))
        media_items = await db_session.exec(select(MediaItem))
        return [
            MediaSectionIDProblem(media_item) for media_item in media_items if media_item.section_id not in section_ids
        ]

    @override
    async def refresh(self, db_session: AsyncSession) -> None:
        await db_session.refresh(self.media_item)

    @override
    @classmethod
    async def fix(
        cls,
        db_session: AsyncSession,
        data_dir: Path,
        problems: Sequence[MediaSectionIDProblem],
    ) -> None:  # ty: ignore[invalid-method-override]
        if not problems:
            return
        await problems[0].refresh(db_session)
        order_indices = list(await db_session.exec(select(Section.order_index)))
        if not order_indices:
            order_indices = [0]
        recovery_section = Section(name="Recovery Section", order_index=(max(order_indices) + 1))
        _logger.info("creating recovery section with order index %d", recovery_section.order_index)
        db_session.add(recovery_section)
        await db_session.commit()
        await db_session.refresh(recovery_section)
        if recovery_section.id is None:
            msg = "failed to create recovery section for media items with invalid section IDs"
            raise RuntimeError(msg)
        for problem in problems:
            await problem.refresh(db_session)
            media = await db_session.get(MediaItem, problem.media_item.id)
            if media:
                _logger.info(
                    "fixing section ID of media item ID %d from %d to recovery section ID %d",
                    media.id,
                    media.section_id,
                    recovery_section.id,
                )
                media.section_id = recovery_section.id
        await db_session.commit()


@dataclass
class MediaFileNotFoundProblem(IntegrityProblem):
    """Problem where a media item references a file that does not exist on disk."""

    media_item: MediaItem
    """Media item referencing the missing files."""
    missing_paths: list[Path]
    """List of paths that are missing on disk (e.g., media file and/or thumbnail)."""

    @override
    @classmethod
    async def check(cls, db_session: AsyncSession, data_dir: Path) -> Sequence[MediaFileNotFoundProblem]:
        problems = []
        media_items = await db_session.exec(select(MediaItem))
        for media_item in media_items:
            missing_paths = [
                path for path in [media_item.path, media_item.thumbnail_path] if not (data_dir / path).exists()
            ]
            if missing_paths:
                problems.append(MediaFileNotFoundProblem(media_item, missing_paths))
        return problems

    @override
    async def refresh(self, db_session: AsyncSession) -> None:
        await db_session.refresh(self.media_item)

    @override
    @classmethod
    async def fix(
        cls,
        db_session: AsyncSession,
        data_dir: Path,
        problems: Sequence[MediaFileNotFoundProblem],
    ) -> None:  # ty: ignore[invalid-method-override]
        for problem in problems:
            await problem.refresh(db_session)
            media_item = await db_session.get(MediaItem, problem.media_item.id)
            if media_item:
                _logger.info("deleting media item ID %d because it references missing files", media_item.id)
                await db_session.delete(media_item)
        await db_session.commit()


@dataclass
class MediaItemNotFoundProblem(IntegrityProblem):
    """Problem where a file exists on disk that is not referenced by any media item in the database."""

    path: Path
    """Path that exists on disk but is not referenced by any media item in the database."""

    @override
    @classmethod
    async def check(cls, db_session: AsyncSession, data_dir: Path) -> Sequence[MediaItemNotFoundProblem]:
        media_items = await db_session.exec(select(MediaItem))
        referenced_paths = set()
        for media_item in media_items:
            referenced_paths.add(media_item.path)
            referenced_paths.add(media_item.thumbnail_path)
        paths = set()
        for dir_name in [MEDIA_DIR_NAME, THUMBNAILS_DIR_NAME]:
            paths |= {path.relative_to(data_dir) for path in (data_dir / dir_name).glob("*")}
        return [MediaItemNotFoundProblem(path) for path in sorted(paths - referenced_paths)]

    @override
    async def refresh(self, db_session: AsyncSession) -> None:
        pass

    @override
    @classmethod
    async def fix(
        cls,
        db_session: AsyncSession,
        data_dir: Path,
        problems: Sequence[MediaItemNotFoundProblem],
    ) -> None:  # ty: ignore[invalid-method-override]
        for problem in problems:
            _logger.info(
                "deleting file at path %s because it is not referenced by any media item in the database", problem.path
            )
            (data_dir / problem.path).unlink(missing_ok=True)


@dataclass
class IntegrityProblems:
    """All integrity problems found during a check."""

    section_order_index_problems: Sequence[SectionOrderIndexProblem]
    """Problems where sections have incorrect order indices."""
    media_section_id_problems: Sequence[MediaSectionIDProblem]
    """Problems where media items reference nonexistent sections."""
    media_file_not_found_problems: Sequence[MediaFileNotFoundProblem]
    """Problems where media items reference files that do not exist on disk."""
    media_item_not_found_problems: Sequence[MediaItemNotFoundProblem]
    """Problems where files exist on disk that are not referenced by any media item in the database."""

    @staticmethod
    async def check(db_session: AsyncSession, data_dir: Path) -> IntegrityProblems:
        """Check the database for integrity problems and return all problems found."""
        return IntegrityProblems(
            section_order_index_problems=await SectionOrderIndexProblem.check(db_session, data_dir),
            media_section_id_problems=await MediaSectionIDProblem.check(db_session, data_dir),
            media_file_not_found_problems=await MediaFileNotFoundProblem.check(db_session, data_dir),
            media_item_not_found_problems=await MediaItemNotFoundProblem.check(db_session, data_dir),
        )

    async def fix(self, db_session: AsyncSession, data_dir: Path) -> None:
        """Fix all integrity problems."""
        await SectionOrderIndexProblem.fix(db_session, data_dir, self.section_order_index_problems)
        await MediaSectionIDProblem.fix(db_session, data_dir, self.media_section_id_problems)
        await MediaFileNotFoundProblem.fix(db_session, data_dir, self.media_file_not_found_problems)
        await MediaItemNotFoundProblem.fix(db_session, data_dir, self.media_item_not_found_problems)
