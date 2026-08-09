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

The build compiles crawl from source and takes a while. **It has not been run
in CI** — the environment this was written in has no Docker daemon. Package
names come from crawl's `INSTALL.md`; if a dependency has moved on, adjust the
`apt-get` line.

## Without Docker

The mock server covers most of the development loop and starts instantly:

```sh
python -m dcssbot.mockserver --port 8080
python scripts/probe.py --key o --key tab
```

It implements the messages the bot exchanges — login, play, ping/pong, `msgs`,
lifecycle — but it is not crawl. Anything about how the *game* behaves has to
be checked against a real build.
