# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

FROM ghcr.io/astral-sh/uv:0.11.11-alpine@sha256:1cf3901d7654f9d94909891e08c23ada53029d3fa697f04029cd8f986de8df2d
WORKDIR /app
RUN apk add --no-cache ffmpeg tzdata
COPY .python-version pyproject.toml uv.lock ./
RUN mkdir --parents src/simeshse
RUN touch README.md src/simeshse/__init__.py
RUN uv sync --frozen --no-dev
COPY ./ ./
ENV ROOT_PATH=/
ENTRYPOINT uv run --no-dev fastapi run --root-path "$ROOT_PATH"
