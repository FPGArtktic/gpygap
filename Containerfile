# gpygap runs in this container so that resolving, downloading and
# building untrusted sdists never touch the host: no build tools, no caches,
# no Cython or setuptools outside the mounted mirror and state directories.
#
# The image deliberately has no compilers. Build checks only need to prove
# that build requirements resolve from the mirror, and that happens before
# compilation starts; without compilers, C extensions fail fast afterwards.
#
# Wheels that upstream never published are compiled in *sibling* containers --
# the pypa manylinux and musllinux images, which carry the right libc floor and
# auditwheel. Only a client is needed here to launch them; it talks to the
# engine socket bin/gpygap passes in.
#
# Both clients are installed, and each engine gets its own. podman also serves a
# Docker-compatible API, but a container created through it comes up unable to
# fork ("Resource temporarily unavailable") while the same container created
# through podman's own API is fine, so the compatibility layer is not usable
# here. gpygap picks the matching client from the socket it was given.

# uv version pinned: it decides which Python builds `uv python install` asks
# for, so the mirrored uv binaries and Python builds must come from the same one.
FROM ghcr.io/astral-sh/uv:0.12.9 AS uv

# Pinned for the same reason as uv: a client that behaves differently between
# runs would change what the mirror ends up containing.
FROM docker.io/library/debian:bookworm-slim AS engine-client
ADD https://download.docker.com/linux/static/stable/x86_64/docker-29.9.1.tgz /tmp/docker.tgz
ADD https://github.com/containers/podman/releases/download/v6.1.3/podman-remote-static-linux_amd64.tar.gz /tmp/podman.tgz
RUN tar -xzf /tmp/docker.tgz -C /usr/local/bin --strip-components=1 docker/docker \
 && tar -xzf /tmp/podman.tgz -C /tmp \
 && mv /tmp/bin/podman-remote-static-linux_amd64 /usr/local/bin/podman \
 && chmod +x /usr/local/bin/docker /usr/local/bin/podman

FROM docker.io/library/python:3.12-slim-bookworm
COPY --from=uv /uv /uvx /usr/local/bin/
COPY --from=engine-client /usr/local/bin/docker /usr/local/bin/podman /usr/local/bin/

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
