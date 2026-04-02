# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Utilities for the routers."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

ADMIN_PREFIX = "/admin"


def is_admin(request: Request) -> bool:
    """Check if the request is for an admin page."""
    root_path = get_root_path(request)
    return request.url.path == f"{root_path}{ADMIN_PREFIX}" or request.url.path.startswith(
        f"{root_path}{ADMIN_PREFIX}/"
    )


def get_root_path(request: Request) -> str:
    """Get the root path from a request."""
    root_path = request.scope.get("root_path", "")
    if not isinstance(root_path, str):
        msg = f"root_path must be str, got: {type(root_path).__name__}"
        raise TypeError(msg)
    return root_path


def slugify(string: str) -> str:
    """Convert a string to a slug (lowercase, words separated by hyphens)."""
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", string.lower()).strip("-"))
