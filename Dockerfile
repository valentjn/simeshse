# Copyright (C) 2026 Julian Valentin
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

FROM ghcr.io/astral-sh/uv:0.11.27-alpine@sha256:801da98dcbbfa72622cb17e8d6636ea093246a4ab10de73a925ccc77fd05330f
WORKDIR /app
RUN apk add --no-cache ffmpeg tzdata
COPY .python-version pyproject.toml uv.lock ./
RUN mkdir --parents src/simeshse
RUN touch README.md src/simeshse/__init__.py
RUN uv sync --frozen --no-dev
COPY ./ ./
ENV ROOT_PATH=/
ENTRYPOINT uv run --no-dev fastapi run --root-path "$ROOT_PATH"
