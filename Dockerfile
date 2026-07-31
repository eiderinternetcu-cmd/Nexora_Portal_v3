FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# --- dev: adds test-only deps (pytest, pytest-asyncio, ...) on top of `base`.
# Only reachable via an explicit `target: dev` build (docker-compose.yml, local
# dev). Never selected implicitly, so it can't leak into a build that forgets
# to set a target.
FROM base AS dev

COPY requirements-dev.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements-dev.txt

# --- runtime: THIS is the last stage in the file, which makes it the default
# `docker build` target whenever nobody passes --target/`target:` — exactly
# what docker-compose.production.yml does. It is intentionally just an alias
# for `base` (no extra layers, no dev deps), so the production image is
# byte-for-byte unaffected by the `dev` stage above.
FROM base AS runtime
