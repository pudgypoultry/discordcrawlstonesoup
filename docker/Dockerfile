# A local WebTiles server to develop against.
#
# Iterating against a public server is antisocial and slow. This builds crawl
# with WebTiles support and runs the Tornado webserver on :8080, so the bot has
# a real crawl to talk to.
#
#   docker build -t dcss-webtiles docker/
#   docker run --rm -p 8080:8080 dcss-webtiles
#
# Then: DCSS_WS_URL=ws://127.0.0.1:8080/socket
#
# The build compiles crawl from source and takes 10-30 minutes.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Build dependencies from crawl-ref/INSTALL.md.
#
# python3-yaml is not optional and is easy to miss: the build runs
# util/{job,species,mon,form}-gen.py to generate headers, and each of them
# imports yaml. Without it `make` dies with ModuleNotFoundError long before
# anything is compiled.
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
        python3-yaml \
        python3-venv \
        git \
        ca-certificates \
        locales \
    && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# A release tag, not a branch, and deliberately so. The Makefile builds
# webserver/webtiles/version.txt by running `git describe` with no fallback,
# and a --depth 1 clone of a branch has no tag in its history, so that target
# fails with "No names found, cannot describe anything". Cloning a tag puts
# HEAD on it and `git describe` answers immediately, which also makes the
# image reproducible. Building trunk needs a full clone instead.
ARG CRAWL_REF=0.34.1
RUN git clone --depth 1 --branch "${CRAWL_REF}" --recurse-submodules --shallow-submodules \
        https://github.com/crawl/crawl.git /crawl

WORKDIR /crawl/crawl-ref/source

# Work around a race in crawl's own Makefile before the main build.
#
# `RLTILES = rltiles` has no trailing slash, so the rule meant to produce the
# status icon sizes is written as `$(RLTILES)status-icon-sizes.js` — that is,
# "rltilesstatus-icon-sizes.js", a filename nothing ever creates. The copy rule
# into webserver/ needs `$(RLTILES)/status-icon-sizes.js` *with* the slash,
# which the generator does write but which no rule declares. make therefore has
# no rule for the file it needs, and the build only survives if that file
# happens to exist by the time the `webserver` target is evaluated.
#
# Whether it does is pure scheduling luck: at -j4 the generator usually wins,
# at -j16 it reliably loses with
#   No rule to make target 'webserver/game_data/static/status-icon-sizes.js'
# Running the generator up front removes the race at any core count.
RUN make -j"$(nproc)" WEBTILES=y build-rltiles \
    && python3 util/status-icon-sizes-gen.py rltiles/icon-sizes.txt

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
#
# bind_address defaults to "" (all interfaces), which is what makes the port
# reachable from outside the container.
CMD ["/venv/bin/python", "webserver/server.py"]
