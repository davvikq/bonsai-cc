# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- `bonsai-cc export --format svg|gif` still lists those in `--help`
  even though they raise `ExportError("not implemented")`.
- Top-level `--port` / `--no-browser` on `bonsai-cc` are shadowed
  by each subcommand's own copies, so `bonsai-cc --port 8080 web`
  silently uses port 0.
- Truncate `<home>/_install_smoke.jsonl` per `install-hook` run
  instead of appending forever.
- `Content-Security-Policy: default-src 'self'` on the index
  response as defence-in-depth.
- Friendlier error when `--port N` collides with a port already in
  use, instead of a raw `OSError`.

## [0.2.3] — 2026-05-17

- README image paths now use absolute jsDelivr URLs so the hero GIF,
  screenshot, and theme gallery render on the PyPI project page
  (relative paths in 0.2.1 only worked inside a GitHub view).
- `Issues` link removed from `[project.urls]`.

## [0.2.1] — 2026-05-17

Republish to claim the PyPI name with the polished release. Same
behaviour as 0.2.0 plus the visual + CI fixes that landed after the
0.2.0 wheel was already uploaded.

### Fixed

- **Pine tiers are puffy cloud-pads** instead of flat ellipse UFOs.
  Each tier is a single Bezier path with bumps along the top edge
  and needle sprays anchored to the bottom curve.
- **Foliage grows in volume with session length.** New continuous
  `canvas.abundance()` scales cluster radius and leaf count across
  every theme, so a 200-event tree visibly carries more foliage
  than a 50-event one (not just a wider footprint).
- **Tree geometry stays inside the pot** at every event count.
  Willow curtains used to hang through the rim, pine's lower tier
  sat inside the soil, oak branches stretched past the pot edges.
- **Sakura blossoms no longer alias into vertical bars** at the
  default browser zoom -- per-blossom 0-72° rotation.
- **Session `started_at` / `ended_at` reflect real hook-write
  times**, not the daemon's processing instant. Orphan-journal
  replay no longer produces zero-duration garden rows.
- Cross-platform determinism: `normalize_path` rewritten as a pure
  string operation so the same fixture journal produces the same
  growth on Windows, Linux, and macOS.
- `_redact(node)` typed to satisfy `mypy --strict`.

### Changed

- Per-event fsync and hook-cold-start benchmarks moved out of CI.
  Shared GitHub runners produce too much variance for a meaningful
  threshold; they remain in `scripts/` as local diagnostics.
- Docs: removed `DESIGN.md` (overlap with README + code comments)
  and `CONTRIBUTING.md` (no contributor pipeline yet); CHANGELOG
  tightened from 413 to ~200 lines; README rewritten in
  project-page style with hero GIF and theme gallery.

## [0.2.0] — 2026-05-17

The big one. The Textual ASCII renderer is gone; the new live
surface is a local web view with twelve language themes, an
animated replay, and a garden grid you can filter. The hook is
now self-sufficient — it writes journal lines directly to disk
and the daemon is optional.

### Live web view

The renderer is one inline-SVG page served on a random loopback
port. No CDN, no bundler, no framework. Server-Sent Events push
each new state from the daemon; the client just drops the SVG
into the DOM. Twelve themes:

- bamboo (python), pine (rust), oak (go), willow (javascript),
  willow + TS-blue fruits (typescript), sakura (swift), maple
  (ruby), old oak with deadwood (c, cpp), banyan with aerial
  roots (java), ginkgo (haskell), birch (zig), generic (default).

Each theme has its own anatomy — asymmetric trunks, Bezier-curved
branches, two-tone leaf clusters, theme-specific leaf shapes
(five-petal blossoms, fan-shaped ginkgo, palmate maple, etc.). All
colours come from an Anthropic-inspired warm palette: pampas,
crail, moss, bark, plus theme accents.

Time-of-day ambient: sun at noon, moon during night sessions, dew
at dawn, snowflakes after eight-hour marathons.

### Garden, replay, sidebar

- **Garden grid** — every saved session as a card with thumbnail
  SVG, language tag, event count, age. Filters by search,
  language, project path. Auto-refreshes every 30s.
- **Hero stats** — three big numbers above the grid: total time
  (excludes <10s noise), sessions (total + this month), current
  streak. Streak's local-calendar math survives midnight-spanning
  sessions and won't punish you in the morning before today's
  first session.
- **Animated replay** — click a card and the session re-grows
  byte-identical in the browser. Pause / play / 0.5×–4× speed /
  share link with `?speed=` and `?t=` preserved.
- **Tool stats sidebar** — Tabler-icon column on the right showing
  every tool the agent used, sorted by count. 200 ms scale pulse
  when a count increments. Errors row at the bottom in crail.

### Hook is self-sufficient

The hook used to forward events to the daemon over a Unix
socket / TCP loopback. That coupling is gone. The hook script
appends one JSONL line to `<home>/journals/<sid>.jsonl`, fsyncs,
and exits in well under 500 ms. Even with no daemon running ever,
journals accumulate on disk and the next `bonsai-cc` launch
picks them up via orphan-journal recovery.

Cross-platform atomic appends: `O_APPEND` on POSIX, `msvcrt.locking`
on Windows (where `O_APPEND` is not race-safe).

### Resilience

- **Garden persistence beyond `SessionEnd`.** Windows Claude Code
  doesn't reliably fire `SessionEnd`; a session can end via
  `/exit` with four `Stop` events and zero `SessionEnd`. Save
  paths: SessionEnd hook, shutdown flush on quit, periodic
  `partial` snapshot every 10 events, idle-timeout `complete` after
  300s of silence. On every daemon start, an orphan-journal scan
  reconstructs sessions that died before saving (`status=recovered`).
- **Read-only commands recover too.** `list`, `show`, `export`,
  `garden` run the orphan scan as a prelude — no more empty
  garden listings from a previous SIGKILL.
- **`recover_orphan_sessions` uses line-position as idx.** The
  hook records omit the `idx` field; an earlier loop required
  it and silently skipped every real production journal, leaving
  the CLI with an empty garden after a clean install. Fixed and
  pinned by `test_recovery_handles_phase11_records_without_idx_field`.
- **Session `started_at` / `ended_at` reflect when events
  *actually* happened.** Was the processing wall-clock, which
  made orphan-journal replay produce zero-duration garden rows
  (everything stamped with the same instant). Now threaded
  through from the journal's `ts` field via `IngestedEvent`.

### Visual polish

- **Pine tiers stopped looking like UFOs.** Each tier is now a
  cloud silhouette with bumps along the top edge and needle sprays
  hanging below, instead of a single flat ellipse.
- **Foliage volume grows with session length.** New continuous
  `abundance()` scalar (0.85 → ~1.6 across event counts) drives
  cluster radius and leaf count. A 200-event session reads
  visibly fuller than a 50-event one, not just wider.
- **No more clipping into the pot.** Willow curtains used to hang
  through the pot rim; pine's lower tier sat inside the soil;
  oak branches stretched past the pot edges. All clamped.
- **Sakura apex no longer aliases into vertical bars.** Each
  five-petal blossom now gets a random 0–72° rotation so the
  upward-pointing petal of one doesn't align with its neighbour.
- **`willow_ts` blue fruits land on foliage**, not random air —
  anchored to the same tip / mid / apex positions the willow
  renderer uses.

### Security

- **DNS-rebinding guard.** Server validates `Host` header against
  the loopback allowlist (`127.0.0.1`, `localhost`, `::1`).
  Otherwise a remote site could short-TTL its DNS to `127.0.0.1`
  and trick your browser into reading the garden DB or DELETE-ing
  saved sessions.
- **Opt-in payload redaction: `BONSAI_CC_REDACT=1`.** Tells the
  hook to blank `prompt`, `old_string`, `new_string`, and
  `content` in journal records. The growth engine doesn't read
  those fields, so the rendered tree is byte-identical with or
  without redaction.

### CLI

- `bonsai-cc watch --replay <file>` *actually* replays the journal
  now. The flag was previously accepted by argparse but never
  threaded through, so the README's smoke test silently rendered
  an empty pot. Three tests pin the wiring.
- `bonsai-cc replay <id>` — open one saved session in the browser
  directly.
- `bonsai-cc doctor` reports build SHA, hook install status,
  garden orphan count, terminal width, fsync support, port file.
- `install-hook` refuses to write a hook command pointing at the
  Windows Store Python shim (the `%LOCALAPPDATA%\...\WindowsApps\`
  placeholder that opens the Store instead of running Python).
  Falls back to `sys.executable` and prints actionable remediation
  if no real interpreter is found.

### Web client polish

- **Mobile breakpoint at 720 px.** Live area stacks vertically,
  idle hero collapses to one column, filter row full-widths,
  theme picker hides (power users can still pick via `?theme=`
  in the URL). Motivated by shared `/replay/<id>` links opened
  on phones.
- **Accessibility pass.** Every filter input, the playback
  controls, and the 14 theme-picker buttons grew explicit
  `aria-label`s. The filter row has `sr-only` `<label for>` for
  screen readers that prefer the semantic association.
- **Idle hero.** When no session is live, the three-stat band
  takes the hero zone so the page never reads as empty.
- **Dark-mode sky.** Tracks the page theme — light page gets a
  warm cream sky, dark page gets a deep warm near-black with a
  soft moon glow.

### Removed

- The Textual TUI (`BonsaiApp`, `TreeCanvas`, `StatsFooter`, the
  garden browser). No `--ascii` flag anywhere; the `textual`
  dependency is dropped. `bonsai-cc show` / `export --format txt`
  still produce ASCII for piping and sharing — that's a static
  snapshot path, different audience.
- The TCP / Unix-socket IPC layer (`bonsai_cc.ipc`,
  `events/server.py`, `events/transport.py`, `events/ingest.py`).
  The hook talks to the journal directly.

## [0.1.0] — 2026-05-15

First public release. End-to-end pipeline: Claude Code hook →
stdlib hook client → daemon (socket-based) → growth engine →
Textual renderer → SQLite garden.

### Added

- Pydantic v2 models for every documented Claude Code hook event;
  per-session JSONL journal with `fsync`-on-append; `asyncio.Queue`
  event bus as the architectural seam between event production
  and the growth engine.
- Non-destructive `install-hook` / `uninstall-hook` with idempotent
  settings.json merges, atomic writes, and `--global` opt-in.
- Stable stdlib-only hook client; `bonsai-cc doctor` with twelve
  diagnostic checks; cold-start benchmark enforcing p99 < 150 ms.
- Pure `apply_event(state, event)` with semantic file-path
  attachment and deterministic RNG seeded by
  `blake2b(session_seed, event_idx)` — byte-identical across runs
  and Python versions.
- Textual TUI with `TreeCanvas`, `StatsFooter`, scrolling, resize.
- SQLite garden with schema migrations, save-on-`SessionEnd` +
  flush-on-quit, `--replay PATH` to drive the pipeline from a
  recorded journal.
- Eleven language palettes with manifest + extension-histogram
  detection; seasonal overlays; time-of-day ambient.
- TXT and PNG exports; SVG / GIF exports stubbed honestly.

### Tests

- 226 passing, 1 skipped (Unix-socket-only on Windows).
- Determinism, architectural-seam, raw-first-durability, and
  hook-client-static-guards gates.

[Unreleased]: https://github.com/davvikq/bonsai-cc/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/davvikq/bonsai-cc/compare/v0.2.1...v0.2.3
[0.2.1]: https://github.com/davvikq/bonsai-cc/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/davvikq/bonsai-cc/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/davvikq/bonsai-cc/releases/tag/v0.1.0
