# dcssbot

A Discord bot that relays chat messages to a live game of Dungeon Crawl Stone
Soup. Anyone in the channel can type `o` or `tab`; the keystrokes
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

**Any key, any time.** A single printable character is sent as itself, and
nothing is dropped for not suiting the screen that is up. This started out as
an allowlist with context gating, on the reasoning that someone would
eventually find the key that saves and exits. In practice the gating was the
bigger problem: a command that vanishes with no reply is indistinguishable
from a bot that has stopped working, and that ambiguity cost far more than the
occasional wasted keystroke. Both restrictions still exist behind
`DCSS_ENFORCE_CONTEXT` and `DCSS_BLOCK_DANGEROUS_KEYS`, off by default.

**The context tracker still earns its keep**, just not as a gate. It drives
`.dcss/neutral` and the stuck watchdog, both of which need to know whether a
menu, a `--more--` or a text field is up in order to send the right key to
escape it.

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
| `DCSS_REQUIRE_PREFIX` | `false` | Require `.dcss/` on keys too, keeping the channel chattable |
| `DCSS_ENFORCE_CONTEXT` | `false` | Drop commands that do not fit the current screen |
| `DCSS_BLOCK_DANGEROUS_KEYS` | `false` | Keep `S`, `~`, `&`, Ctrl- out of normal play |
| `DCSS_NEUTRAL_STEP_DELAY` | `0.4` | Pause between keys while `.dcss/neutral` backs out |

`DCSS_COMMAND_INTERVAL` below `0.1` is refused: public servers ask API clients
to stay under 10 commands per second, and anarchy input is more watchable well
below that anyway.

## Commands

**Type the key. No prefix.** A message that is just `o` sends `o`; `S` sends
`S`; `5`, `#`, `?` likewise. Letters, digits and punctuation go straight
through with case intact.

**Keys with no character have a name**, because there is nothing to type:

```
tab  esc  enter  space  backspace  delete
arrowup  arrowdown  arrowleft  arrowright
pageup  pagedown  home  end  ctrl <letter>
```

Word aliases exist for the common commands because they read better in a busy
channel — `explore` for `o`, `quaff` for `q`. `.dcss/help` lists every one
against the key it sends.

Directions are words: `north`, not `n`. A single letter is that letter, and
`n` is a move south-east in crawl's vi keys.

**Double a movement key to run.** `uu` is Shift-u — north-east until something
happens. Only the eight vi keys (`h j k l y u b n`) double; doubling anything
else would silently upper-case it, and `ss` would be save-and-exit.

**Modifiers are two words**: `ctrl f`, `shift u`, `shift arrowup`. Shift on a
letter is its capital; on an arrow or Tab it is the distinct keycode from
`cio.h`. Shift on punctuation is refused, because which symbol that produces
is a fact about the keyboard, not about crawl.

Everything that combines keys — doubled runs, `run <dir>`, `ctrl`, `shift` —
is **held back unless the game is in ordinary play**. A run has no meaning in
a menu and a control key there can do something surprising. Single keys are
unaffected and still go through anywhere.

`neutral` backs out of whatever is on screen — menu, prompt, targeting, text
field — until normal play resumes, re-reading the situation after each key
rather than sending a fixed sequence.

**The bot's own commands keep the prefix**: `.dcss/help`, `.dcss/link`,
`.dcss/status`. Otherwise saying "help" in conversation would dump the command
list. The prefix still works on keys too — `.dcss/o` is the same as `o`.

### What this means for the channel

The bot reads every message, so **ordinary words that are commands become
keystrokes**: `no`, `yes`, `map`, `read`, `eat`, `wait`, `run`, `look`, `home`,
`end`. Anything that is not a key is ignored silently, but the channel is no
longer usable for conversation. Give the bot its own channel, or set
`DCSS_REQUIRE_PREFIX=true` to go back to needing `.dcss/` on everything.

Nothing is filtered for making sense. A command that does not fit the current
screen is sent anyway and the game decides what it means; `DCSS_ENFORCE_CONTEXT`
turns the old gating back on. `S` is save-and-exit and reaches the game like
any other key — `DCSS_BLOCK_DANGEROUS_KEYS` keeps `S`, `~`, `&` and the Ctrl-
codes out of normal play if one person ending every run becomes a problem.

## Development

```sh
python -m pytest                      # 198 tests, no network needed
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
