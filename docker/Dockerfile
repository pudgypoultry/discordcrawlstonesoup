# A local WebTiles server to develop against.
#
# Iterating against a public server is antisocial and slow. This builds crawl
# with WebTiles support and runs the Tornado webserver on :8080, so the bot has
# a real crawl to talk to.
#
#   docker build -t dcss-webtiles docker/
#   docker run --rm -p 8080:8080 dcss-webtiles
#
# Then: DCSS_WS_URL=ws://localhost:8080/socket
#
# NOTE: this has not been built in CI — there is no Docker daemon in the
# development environment it was written in. Package names follow crawl's
# INSTALL.md; expect to adjust a version or two on first build.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Build dependencies from crawl-ref/INSTALL.md, plus the webserver's Python bits.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libncursesw5-dev \
        bison \
        flex \
        liblua5.1-0-dev \
        libsqlite3-dev \
        libz-dev \
        pkg-config \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ca-certificates \
        locales \
    && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

ARG CRAWL_REF=master
RUN git clone --depth 1 --branch "${CRAWL_REF}" --recurse-submodules --shallow-submodules \
        https://github.com/crawl/crawl.git /crawl

WORKDIR /crawl/crawl-ref/source

# WEBTILES=y builds the server-side tiles binary that talks over a unix socket.
RUN make -j"$(nproc)" WEBTILES=y

# base.py3.txt is enough to run the server; dev.py3.txt only adds linters.
RUN python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -r webserver/requirements/base.py3.txt

EXPOSE 8080

# The stock config binds :8080 and defines a `dcss-web-trunk` game, which is
# what DCSS_GAME_ID defaults to. dgl_mode is on by default, so register the
# bot's account once through the web UI at http://localhost:8080/ before
# pointing the bot at it.
CMD ["/venv/bin/python", "webserver/server.py"]
