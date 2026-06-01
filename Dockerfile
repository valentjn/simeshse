# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

FROM ghcr.io/astral-sh/uv:0.11.18-alpine@sha256:14290eea3f710b806d9eb1d916234c9f9be13b66d0bf8aa3ccfc68357868dfe5
WORKDIR /app
RUN apk add --no-cache ffmpeg tzdata
COPY .python-version pyproject.toml uv.lock ./
RUN mkdir --parents src/simeshse
RUN touch README.md src/simeshse/__init__.py
RUN uv sync --frozen --no-dev
COPY ./ ./
ENV ROOT_PATH=/
ENTRYPOINT uv run --no-dev fastapi run --root-path "$ROOT_PATH"
