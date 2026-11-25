# ScholarMind image: one container runs everything (deploy/run.sh): the api with the frontend
# (port 8000), the cron scheduler, one fetch worker and ANALYZE_WORKERS analyze workers, which
# call pi. compose.yaml builds it and runs it with PostgreSQL and Redis (`docker compose up -d`,
# the mirrors and proxy below set in .env). To build by hand, from the repository root:
#
#   docker build --build-arg PIP_INDEXES=https://pypi.tuna.tsinghua.edu.cn/simple,https://pypi.org/simple \
#     --build-arg NPM_REGISTRIES=https://registry.npmmirror.com,https://registry.npmjs.org -t scholarmind .
#
# Each list is tried in turn, separated by commas; the defaults are the official sources.
# HTTP_PROXY, HTTPS_PROXY and NO_PROXY are Docker's predefined build args and reach pip and npm
# when passed with --build-arg.
#
# It needs DATABASE_URL, REDIS_URL, SECRET_KEY and LLM_API_KEY in the environment and
# config.yaml at /app/config.yaml (or CONFIG_FILE pointing elsewhere). Migrate, then run:
#
#   docker run --rm --env-file .env -v ./config.yaml:/app/config.yaml:ro scholarmind alembic upgrade head
#   docker run -d --env-file .env -v ./config.yaml:/app/config.yaml:ro -e ANALYZE_WORKERS=2 scholarmind
#
# To run the parts apart, give the command `scholarmind-run api` or `scholarmind-run worker`;
# run the worker part exactly once, or the daily sync is enqueued twice.

# pi, the only way the project calls a model. Only node and pi's package go into the image,
# not npm.
FROM node:22-slim AS pi
ARG NPM_REGISTRIES=https://registry.npmjs.org
RUN for registry in $(echo "$NPM_REGISTRIES" | tr , ' '); do \
      npm install -g --no-fund --no-audit --registry "$registry" @earendil-works/pi-coding-agent@1.5.2 && exit 0; \
      echo "npm install from $registry failed"; \
    done; exit 1

# The frontend's static files (frontend/dist). A stage of its own: a backend change does not
# rebuild it, and the dependencies are installed again only when the lockfile changes.
FROM node:22-slim AS frontend
ARG NPM_REGISTRIES=https://registry.npmjs.org
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN for registry in $(echo "$NPM_REGISTRIES" | tr , ' '); do \
      npm ci --no-fund --no-audit --registry "$registry" && exit 0; \
      echo "npm ci from $registry failed"; \
    done; exit 1
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ARG PIP_INDEXES=https://pypi.org/simple
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_FILE=/app/config.yaml \
    PROMPTS_DIR=/app/prompts \
    FRONTEND_DIR=/app/frontend \
    PI_CODING_AGENT_DIR=/app/pi-agent
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /usr/local/lib/node_modules/@earendil-works/pi-coding-agent /usr/local/lib/node_modules/@earendil-works/pi-coding-agent
RUN ln -s ../lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js /usr/local/bin/pi
# alembic.ini is here, so `alembic upgrade head` runs as is.
WORKDIR /app/backend
COPY backend/ ./
# COPY keeps the context's modes (600/700 when unpacked under umask 077, as the deploy script
# does), and the app runs as another user: make the sources readable whatever the host had.
RUN chmod -R a+rX . \
    && for index in $(echo "$PIP_INDEXES" | tr , ' '); do \
      pip install --no-cache-dir --index-url "$index" . && exit 0; \
      echo "pip install from $index failed"; \
    done; exit 1
# After the backend's layers, so a frontend change does not reinstall the backend.
COPY --from=frontend /src/dist /app/frontend/
COPY prompts/ /app/prompts/
COPY deploy/run.sh /usr/local/bin/scholarmind-run
# pi writes models.json, auth.json and models-store.json into PI_CODING_AGENT_DIR, and gets
# the user's home as HOME.
RUN chmod -R a+rX /app/frontend /app/prompts \
    && chmod 755 /usr/local/bin/scholarmind-run \
    && useradd --system --uid 10001 --create-home --home-dir /home/scholarmind scholarmind \
    && install -d -o scholarmind -g scholarmind -m 700 /app/pi-agent
USER scholarmind
# Fails the build if pi does not run in this image as the user that runs it.
RUN node --version && pi --version
EXPOSE 8000
CMD ["scholarmind-run"]
