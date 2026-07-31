# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

FROM ghcr.io/astral-sh/uv:0.12.1-alpine@sha256:0aad8ff88b5967fcb7a27ae630c2f02556897eb7eb6e5f9347f18e25145bcd10
WORKDIR /app
RUN apk add --no-cache ffmpeg tzdata
COPY .python-version pyproject.toml uv.lock ./
RUN mkdir --parents src/simeshse
RUN touch README.md src/simeshse/__init__.py
RUN uv sync --frozen --no-dev
COPY ./ ./
ENV ROOT_PATH=/
ENTRYPOINT uv run --no-dev fastapi run --root-path "$ROOT_PATH"
