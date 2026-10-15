# gpygap runs in this container so that resolving, downloading and
# building untrusted sdists never touch the host: no build tools, no caches,
# no Cython or setuptools outside the mounted mirror and state directories.
#
# The image deliberately has no compilers. Build checks only need to prove
# that build requirements resolve from the mirror, and that happens before
# compilation starts; without compilers, C extensions fail fast afterwards.

# uv version pinned: it decides which Python builds `uv python install` asks
# for, so the mirrored uv binaries and Python builds must come from the same one.
FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

FROM docker.io/library/python:3.12-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/

ENV HOME=/tmp \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /opt/gpygap
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-cache

ENV PATH="/opt/gpygap/.venv/bin:${PATH}"
ENTRYPOINT ["gpygap"]
