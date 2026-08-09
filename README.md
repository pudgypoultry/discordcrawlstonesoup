# dcssbot

A Discord bot that relays chat messages to a live game of Dungeon Crawl Stone
Soup. Anyone in the channel can post `.dcss/o` or `.dcss/tab`; the keystrokes
go to one shared character, conflicts and all. Twitch Plays Pokémon, with
permadeath.

Discord shows the game log. Watching the game happens on the crawl server's own
spectate page, which the bot links when a run starts.

## How it talks to the game

Not through a browser. WebTiles is a Tornado webserver that relays JSON between
the browser and each running crawl process — over a unix socket to the game
binary for control keys, and to the process's tty for ordinary typing. The bot
is a websocket client speaking that same protocol, so there is no DOM to drive
and no screen to scrape.

Details that are easy to get wrong, all verified against crawl's source rather
than from memory:

| Thing | What is actually true |
| --- | --- |
| Printable keys | `{"msg":"input","text":"o"}` — handled by `process_handler.py`, written to the game's tty |
| Special keys | `{"msg":"key","keycode":9}` — relayed to the binary and parsed in `tileweb.cc` |
| Compression | Frames are raw-deflate over **one stream per connection**, not per frame. Asking for the `no-compression` subprotocol turns it off entirely, which `ws_handler.select_subprotocol` honours |
| Frame contents | Usually `{"msgs":[...]}` carrying several messages; sometimes a bare message. Both forms occur |
| Message text | A crawl *format string*, not HTML: `<lightred>ouch</lightred>`, `<<` escapes a literal `<`, and an unknown tag like `<bat>` is ordinary text |
| Menus | `input_mode` still reports `COMMAND` while a menu is open, so the UI stack has to be tracked separately |
| Ping | The server sends `ping` and expects `pong`, or it drops you |

## Design decisions worth knowing

**The grammar is an allowlist.** With open posting, a blacklist is a losing
game — someone eventually finds the sequence that saves and exits or opens the
macro editor. Only tokens in `dcssbot/grammar.py` produce anything, and there
is no generic "send this character" command. `S`, `Ctrl-Q`, `~` and `&` are
unreachable by construction. The two commands that take free text (`select`,
`text`) filter their argument and are gated to contexts where a stray key
cannot reach the dungeon.

**Context is checked twice.** Once when the message is parsed, and again at
dispatch, because a menu can open while a command sits in the queue.

**The queue is bounded in depth and in age.** A command that fires fifteen
seconds late is noise, not chaos — whatever it was reacting to is gone. Old
commands are discarded on dequeue, and a burst that fills the queue is refused
rather than evicting commands already waiting.

**One message, one command.** Twelve people posting `.dcss/o` fire twelve
auto-explores. Set `DCSS_COLLAPSE_DUPLICATES=true` for the other behaviour.

**There is a watchdog.** Random input finds menus constantly. If the game sits
in a non-play context past `DCSS_STUCK_TIMEOUT` with an empty queue, the bot
sends Escape — once per window, not every poll.

**Death restarts the run.** Crawl does not continue after you die, and anarchy
input kills characters fast. On `game_ended` the bot posts the morgue link and
starts a new character, answering the creation menus *only while one is
actually open*.

## Running it

```sh
pip install -e ".[dev]"

export DISCORD_TOKEN=...
export DISCORD_CHANNEL_IDS=123456789012345678
export DCSS_WS_URL=wss://crawl.example.org/socket
export DCSS_SITE_URL=https://crawl.example.org/
export DCSS_USERNAME=mybot
export DCSS_PASSWORD=...

python -m dcssbot
```

The bot needs the privileged **Message Content** intent, enabled in the Discord
developer portal. Without it the bot connects fine and simply never sees a
message.

### On Windows

Install into a virtual environment. Installing into a system Python fails on
the console-script shims with `WinError 2 ... websockets.exe.deleteme`, and no
amount of retrying fixes it.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

Calling `.venv\Scripts\python` directly avoids `Activate.ps1`, which trips over
the default PowerShell execution policy. Settings are per-window and vanish
when it closes:

```powershell
$env:DISCORD_TOKEN="..."
$env:DISCORD_CHANNEL_IDS="..."
.venv\Scripts\python -m dcssbot
```

### Discord setup

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** → turn on **MESSAGE CONTENT INTENT** → Save.
3. **Bot** → **Reset Token** → copy it. It is shown once, and it is a password.
4. **OAuth2 → URL Generator** → scope **bot**, permissions **View Channels**
   and **Send Messages** → open the generated URL → pick your server.
5. In Discord, **User Settings → Advanced → Developer Mode**, then right-click
   the channel → **Copy Channel ID** for `DISCORD_CHANNEL_IDS`.

## Troubleshooting

On startup the bot logs what it resolved to, which answers most questions:

```
INFO dcssbot.runner:  game: ws://127.0.0.1:8080/socket (id dcss-web-trunk, user mybot) | discord: 1 channel(s), prefix '.dcss/' | interval 0.75s
INFO dcssbot.session: connecting to ws://127.0.0.1:8080/socket as mybot (game dcss-web-trunk)
INFO dcssbot.session: connected, logging in
INFO dcssbot.session: logged in as mybot
```

Set `DCSS_LOG_LEVEL=DEBUG` to also see commands being dropped and every
context change.

| Symptom | Cause |
| --- | --- |
| `.dcss/help` works but `.dcss/o` does nothing | The game side is not connected. The bot says so in-channel once a minute; `.dcss/status` has the detail |
| Log stops after `connecting to ...` | The connect is hanging rather than being refused — wrong host, or a firewall dropping packets. It gives up after 20s |
| `session ended: ... Connect call failed` | Nothing is listening. Start the mock, or check the port |
| Connects on Linux but not Windows | `localhost` resolves to `::1` first on Windows. Use `127.0.0.1` in `DCSS_WS_URL` |
| Bot online, ignores every message | Message Content intent is off, or `DISCORD_CHANNEL_IDS` holds a server ID instead of a channel ID |
| `cannot see channel <id>` | The bot is not in that server, or lacks View Channels |

A game that is genuinely running always announces itself: the bot posts
**New game started** with the spectate link, unprompted. If that message never
appeared, the game side never got in.

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `DCSS_WS_URL` | `ws://127.0.0.1:8080/socket` | WebTiles websocket |
| `DCSS_SITE_URL` | `http://127.0.0.1:8080/` | Base URL for `#watch-` links |
| `DCSS_USERNAME` / `DCSS_PASSWORD` | — | The bot's crawl account |
| `DCSS_GAME_ID` | `dcss-web-trunk` | Game to start |
| `DISCORD_TOKEN` | — | Bot token |
| `DISCORD_CHANNEL_IDS` | — | Channel IDs to relay in |
| `DCSS_PREFIX` | `.dcss/` | Command prefix |
| `DCSS_COMMAND_INTERVAL` | `0.75` | Seconds between keystrokes |
| `DCSS_QUEUE_DEPTH` | `25` | Max queued commands |
| `DCSS_QUEUE_TTL` | `12` | Seconds before a queued command is stale |
| `DCSS_USER_COOLDOWN` | `0` | Per-user seconds between posts |
| `DCSS_COLLAPSE_DUPLICATES` | `false` | Fold identical queued commands into one |
| `DCSS_FLUSH_IDLE` | `1.2` | Quiet gap that triggers a log post |
| `DCSS_FLUSH_MAX_LINES` | `18` | Lines per post |
| `DCSS_STATUS_LINE` | `true` | Prefix each post with HP/place/turn |
| `DCSS_STUCK_TIMEOUT` | `45` | Seconds before the watchdog sends Escape |
| `DCSS_AUTO_RESTART` | `true` | Start a new character on death |
| `DCSS_COMPRESSION` | `false` | Use compressed frames instead of `no-compression` |

`DCSS_COMMAND_INTERVAL` below `0.1` is refused: public servers ask API clients
to stay under 10 commands per second, and anarchy input is more watchable well
below that anyway.

## Commands

`.dcss/help` posts the full list. In an open channel it will get spammed, so it
is on a 30-second cooldown — consider pinning the list and removing the command.

```
Move    .dcss/n .dcss/s .dcss/e .dcss/w .dcss/ne .dcss/nw .dcss/se .dcss/sw
        .dcss/run <dir>
Act     .dcss/o .dcss/tab .dcss/wait .dcss/rest .dcss/up .dcss/down
        .dcss/travel .dcss/map
Items   .dcss/pickup .dcss/inv .dcss/wield .dcss/wear .dcss/drop .dcss/quaff
        .dcss/read .dcss/eat .dcss/fire .dcss/evoke ...
Magic   .dcss/cast .dcss/spells .dcss/memorise .dcss/abil .dcss/pray
Prompts .dcss/esc .dcss/enter .dcss/space .dcss/yes .dcss/no .dcss/more
        .dcss/select <x> .dcss/text <words> .dcss/1 ... .dcss/9
Bot     .dcss/link .dcss/status .dcss/help
```

Movement uses compass names rather than crawl's vi keys, because `n` would mean
both "north" and "no".

## Development

```sh
python -m pytest                      # 157 tests, no network needed
python -m dcssbot.mockserver          # a stand-in WebTiles server
python scripts/probe.py --key o       # connect, send one key, print the JSON
```

`scripts/probe.py` is the thing to reach for when something misbehaves against
a real server: it does the connection, the login and one keystroke with nothing
else in the way, and prints the decoded messages.

`--games` logs in and prints the game ids a server offers, which is what
`DCSS_GAME_ID` needs and which nothing else exposes:

```sh
python scripts/probe.py --url wss://crawl.example.org/socket \
    --username mybot --password ... --games
```

See `docker/` for a local build of real crawl, and `rc/bot.rc` for the rcfile
options that cut message volume and remove modal prompts — tuning that file is
the highest-leverage hour in this project.

## Before pointing this at a public server

Ask the server's admin first. A Discord-driven bot account is exactly the kind
of thing to clear in advance, and public servers have a code of conduct that
covers automated clients. Run it on a dedicated account, never a personal one.
