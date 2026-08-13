# Project context

A Discord bot relaying chat commands to a live DCSS WebTiles game. Open
posting, one shared character, Twitch-Plays style.

## Protocol facts (verified against crawl's source, not memory)

These are the ones that are commonly misremembered. Re-check against
`crawl/crawl-ref/source/` before changing anything that depends on them.

- **Printable keys**: `{"msg":"input","text":"o"}`. Handled by
  `webserver/webtiles/process_handler.py`, which writes to the game process's
  tty. `{"msg":"input","data":[123]}` sends raw bytes; `client.js` uses that
  form for `{`.
- **Special keys**: `{"msg":"key","keycode":N}`. Relayed over the unix socket
  and parsed in `tileweb.cc::_handle_control_message`. `text_input` also
  exists there and takes a whole string, but the browser client uses `input`
  for typing, so we match it.
- **Keycodes**: `cio.h`, `CK_DELETE = -255` followed by a sequence that must
  not be rearranged. Ctrl-X is `X - 64`.
- **Compression**: raw deflate (`-zlib.MAX_WBITS`) over **one stream per
  connection**. Each frame has `00 00 FF FF` stripped by the server and must
  have it re-appended before inflating. Requesting the `no-compression`
  subprotocol skips all of this; `ws_handler.select_subprotocol` honours it.
- **Frames**: normally `{"msgs":[...]}`, sometimes a bare message. Handle both.
- **Message text**: a crawl format string, not HTML. `<lightred>x</lightred>`
  is markup, `<<` is a literal `<`, `<bat>` is literal text, colour lookup is
  case-sensitive. Mirrors `game_data/static/util.js`.
- **`old_msgs` vs `rollback`**: `old_msgs` N means the first N entries of
  `messages` repeat lines already shown — skip them. `rollback` N means N
  previously-sent lines are retracted and `messages` holds new content — do
  not skip. Current crawl emits `rollback` but not `old_msgs`.
- **Resync**: `spectator_joined` triggers `_send_everything()` in the binary,
  which calls `webtiles_send_messages()` — only *unsent* messages, so modern
  crawl does not replay the buffer on re-attach.
- **Menus**: `input_mode` keeps reporting `COMMAND` while a menu is open.
  Track `ui-push` / `ui-pop` / `ui-stack` / `close_all_menus` separately.
- **Ping**: server sends `ping`, expects `pong`, drops you otherwise.
- **Spectate URL**: `#watch-<username>`; play is `#play-<game_id>`. Hash
  routing lives in `static/scripts/client.js`.
- **Character creation**: `newgame.cc` — `#` recommended character, `!` random
  character, `*` random for the current choice, `+` random recommended weapon.

## Constraints

- Public servers ask API clients to stay under **10 commands/second**. The
  config refuses an interval below 0.1s. Play far slower than the ceiling.
- Discord allows roughly **5 messages per 5 seconds per channel** and a
  **2000-character** message cap. Hence the batcher.
- Reading messages needs the privileged **Message Content** intent.
- Clear a Discord-driven bot with the server's admin before pointing it at a
  public server. Use a dedicated account.

## Shape of the code

`client.py` speaks the protocol; `session.py` owns the game loop, the
watchdog and auto-restart; `grammar.py` is the allowlist that decides what
chat can do; `cmdqueue.py` bounds depth and age; `msglog.py` and `batcher.py`
turn game output into Discord posts; `discordbot.py` is the gateway side.

## Rules of thumb

- Keys need no prefix: a bare `o` is a command. `help`/`link`/`status` keep
  `.dcss/` so conversation cannot fire them, and unparseable bare messages are
  ignored silently because most channel traffic is chat.
- Any single printable character is sent as itself, and nothing is dropped for
  not suiting the current screen. Both restrictions still exist behind
  `DCSS_ENFORCE_CONTEXT` and `DCSS_BLOCK_DANGEROUS_KEYS`, off by default —
  a silently dropped command reads as a broken bot, which cost more than the
  keystrokes it saved.
- The context tracker is not a gate any more; it drives `.dcss/neutral`, the
  stuck watchdog, and the one remaining restriction: anything marked
  `multi_key` (doubled runs, `run`, `ctrl`, `shift`) is only sent during
  `PLAY`, whatever `DCSS_ENFORCE_CONTEXT` says.
- Doubling only applies to the eight vi keys. `ss` would otherwise become `S`,
  which saves and exits.
- `text_cursor` alone means a text field is active. Crawl's message-line
  prompts (Ctrl-F, travel) leave `input_mode` at NORMAL and push nothing onto
  the UI stack, so requiring a menu there misreads them as ordinary play.
- Skill hotkeys are positional and differ between the "useful" and "all"
  views, and past ~26 skills they become digits, which the target prompt
  (`[a-z]`) will not take. `skills.py` reads them off the menu; never hardcode
  a skill-to-key table.
- The skill menu arrives as `txt` with `id: menu_txt`, HTML spans and
  entities, two columns per line, and only the *changed* lines each update —
  so merge rather than replace, and clear before driving it.
- The item menu (`use_item`) is *structured*, unlike the skill screen: the
  `menu` message carries `items` with `text`, `hotkeys` and `q` (stack size).
  `q` then a letter uses the item outright — two keys, not three.
- Identified potions and scrolls read "potion of X" / "scroll of X"; anything
  else is unidentified (`item-name.cc`). That is the whole "unknown" test.
- `wss://` connections build their own TLS context pinned to `certifi`'s
  bundle rather than trusting `ssl.create_default_context()`'s OS-store
  default. On Windows that default reads live from CryptoAPI, and a CA
  rotation can leave both a fresh and an expired copy of the same root there;
  OpenSSL's path-builder can pick the stale one where Windows' own chain
  builder does not, failing with `certificate has expired` on a chain a
  browser accepts. See `WebTilesClient._build_ssl_context`.
- Test against `dcssbot.mockserver`, not a live server. `scripts/probe.py` is
  the one-keystroke debugging tool.
