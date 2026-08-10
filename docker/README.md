# Local WebTiles server

Develop against this, not against a public server.

```sh
docker build -t dcss-webtiles docker/
docker run --rm -p 8080:8080 dcss-webtiles
```

Then open <http://localhost:8080/>, register the account the bot will use, and
point the bot at it:

```sh
export DCSS_WS_URL=ws://127.0.0.1:8080/socket
export DCSS_SITE_URL=http://127.0.0.1:8080/
export DCSS_GAME_ID=dcss-web-trunk
```

The build compiles crawl from source and takes 10–30 minutes.

These steps were run end to end on `ubuntu:24.04` — the image's own base — at
both `-j4` and `-j16`, since one of the traps below only appears at high
parallelism. The resulting server was driven with `scripts/probe.py`: log in,
list games, start a game, answer character creation with `#` and `*`,
auto-explore, and relay the log. The behaviour the bot depends on is what real
crawl does:

* the species screen arrives as `ui-push` while `input_mode` still reports
  `COMMAND`, which is exactly why the UI stack is tracked separately
* `#` picks a recommended combination and the following screen accepts `*`,
  matching `newgame.cc`
* replaying `play` for an account with a save resumes it ("Welcome back")
  rather than starting a new character

## Two things the build needs that are easy to miss

Both of these were wrong here first time round, so they are worth stating.

**PyYAML is a build dependency, not just a runtime one.** `make` runs
`util/job-gen.py`, `species-gen.py`, `mon-gen.py` and `form-gen.py` to generate
headers, and all four `import yaml`. Without it the build dies with
`ModuleNotFoundError: No module named 'yaml'` before compiling anything.
Installing it into a virtualenv afterwards does not help — it has to be on the
system interpreter at build time.

**A race in crawl's Makefile has to be stepped around.** `RLTILES = rltiles`
carries no trailing slash, so the rule for the status icon sizes is written as
`$(RLTILES)status-icon-sizes.js` — `rltilesstatus-icon-sizes.js`, a filename
nothing creates. The copy into `webserver/` needs
`rltiles/status-icon-sizes.js`, which the generator does write but which no
rule declares, so make has no rule for the file it needs and the build only
survives if that file already exists when the `webserver` target is evaluated.

That is scheduling luck, and it scales with `-j`: a 4-core build usually wins,
a 16-core build reliably fails with

```
make: *** No rule to make target 'webserver/game_data/static/status-icon-sizes.js', needed by 'webserver'.  Stop.
```

Running `util/status-icon-sizes-gen.py` before the main build removes the race
at any core count. Both the Dockerfile and the native instructions below do
this.

**A shallow clone must be of a tag, not a branch.** The Makefile builds
`webserver/webtiles/version.txt` with a bare `git describe` and no fallback:

```make
webserver/webtiles/version.txt: .ver
	@git describe $(MERGE_BASE) > webserver/webtiles/version.txt
```

`git clone --depth 1 --branch master` fetches no tags, so `git describe` fails
with *"No names found, cannot describe anything"* and the build stops with
`Error 128`. Cloning a release tag puts HEAD on it and `git describe` answers
immediately. Hence `ARG CRAWL_REF=0.34.1`.

To build trunk instead, a full clone is required — `--depth 1` cannot work:

```sh
docker build -t dcss-webtiles --build-arg CRAWL_REF=master docker/   # will fail
```

## Building natively instead

On Linux, or in WSL2 on Windows, skipping Docker is a layer less. WSL2 forwards
localhost, so a server built this way is reachable from a bot running on the
Windows side at the default `ws://127.0.0.1:8080/socket`.

```sh
sudo apt install -y build-essential libncursesw5-dev bison flex \
    liblua5.1-0-dev libsqlite3-dev libz-dev pkg-config python3-yaml python3-venv git
git clone --depth 1 --branch 0.34.1 --recurse-submodules --shallow-submodules \
    https://github.com/crawl/crawl.git
cd crawl/crawl-ref/source
make -j"$(nproc)" WEBTILES=y build-rltiles
python3 util/status-icon-sizes-gen.py rltiles/icon-sizes.txt   # see the race above
make -j"$(nproc)" WEBTILES=y
python3 -m venv ~/wtenv
~/wtenv/bin/pip install -r webserver/requirements/base.py3.txt
~/wtenv/bin/python webserver/server.py
```

## Without a real crawl at all

The mock server covers most of the development loop and starts instantly:

```sh
python -m dcssbot.mockserver --port 8080
python scripts/probe.py --key o --key tab
```

It implements the messages the bot exchanges — login, play, ping/pong, `msgs`,
lifecycle — but it is not crawl. Anything about how the *game* behaves has to
be checked against a real build.
