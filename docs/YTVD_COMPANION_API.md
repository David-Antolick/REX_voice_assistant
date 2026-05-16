# YTVD Companion-Server API — Reference for REX action authors

This is a focused reference for wiring REX voice actions to YTVD (the YouTube
Music + Video desktop fork). It's not a full spec — only what action authors
need. Source of truth for endpoints lives in the YTVD repo
(`src/main/integrations/companion-server/api/v1/`).

The plan that motivated this fork lives at
[`docs/YT_VIDEO_FORK_PLAN.md`](YT_VIDEO_FORK_PLAN.md) (same doc as in the
YTVD repo). Read it for context if you're new to the architecture.

## TL;DR

- Same companion-server YTMD used (port 9863), same auth token, single
  process — YTVD replaces YTMD; both don't run side-by-side.
- The existing music routes from YTMD's API are preserved unchanged. Existing
  `ytmd.py` actions continue to work.
- Two new namespaces are added: `/api/v1/playback/*` (source-agnostic) and
  `/api/v1/video/*` (video-specific).
- Auth flow is identical to YTMD's — no REX changes needed to authenticate.
  Reuse `YTMD_HOST` / `YTMD_PORT` / `YTMD_TOKEN` env vars.

## Connection

| What | Value |
|---|---|
| Default host | `127.0.0.1` |
| Default port | `9863` |
| Auth header | `Authorization: <token>` (raw token, no `Bearer` prefix) |
| Env vars REX uses | `YTMD_HOST`, `YTMD_PORT`, `YTMD_TOKEN` (unchanged from YTMD) |
| Token storage | `keyring` under the existing `ytmd_token` slot |

YTVD intentionally reuses YTMD's env vars and token keyring slot. A user
upgrading from YTMD doesn't need to re-authenticate or change config.

## Namespaces

YTVD exposes three logical namespaces under `/api/v1/`:

| Prefix | Purpose | Status | REX module |
|---|---|---|---|
| `/api/v1/` (root) | Music-specific (unchanged from YTMD) | ✅ Stable | `ytmd.py` |
| `/api/v1/playback/` | Source-agnostic; dispatches to the audio-bus owner | ✅ MVP shipped | not yet wired |
| `/api/v1/video/` | Video-specific | ⚠️ Skeleton only | not yet wired |

## `/api/v1/playback/*` — source-agnostic playback

Use this for voice verbs that should affect whatever's currently playing
("pause", "skip ahead") without REX needing to know which view holds the
audio bus.

### `GET /api/v1/playback/state`

Auth required. Rate-limited (1/5s/client).

**Response body:**

```json
{
  "activeSource": "music" | "video" | null,
  "paused": bool,
  "currentTime": number,
  "duration": number,
  "volume": number,    // 0.0–1.0
  "muted": bool
}
```

When `activeSource` is `null` (nothing has played yet since YTVD launched),
the other fields return zeroed defaults (paused=true, volume=1, etc.). The
YTVD `SourceCoordinator` flips `activeSource` the first time a view
transitions paused→playing.

### `POST /api/v1/playback/command`

Auth required. Rate-limited (2/1s/client).

**Request body (typebox Union):**

```json
{ "command": "play" }
{ "command": "pause" }
{ "command": "playPause" }
{ "command": "seekTo",       "data": <seconds, ≥ 0> }
{ "command": "seekRelative", "data": <±seconds> }
{ "command": "mute" }
{ "command": "unmute" }
{ "command": "volumeUp" }
{ "command": "volumeDown" }
{ "command": "setVolume",    "data": <0..100> }
```

**Response:** `204 No Content` on success, `503` if the active view isn't
reachable.

**Routing semantics:**

- The command is dispatched to the active source's underlying IPC channel
  (music → `remoteControl:execute`, video → `ytVideoView:execute`).
- If `activeSource` is null, YTVD defaults to **music** (music is the primary
  view; this matches the user's likely intent on cold start).
- `seekRelative` on music is implemented by reading the music store's
  current `videoProgress`, computing an absolute target, and sending a
  `seekTo`. Side-effect-equivalent to native relative seek.

**Commands NOT in /playback (deliberately):**

- `next` / `previous` — semantics diverge across sources (next song vs next
  video) and YTVD's video preload doesn't have a "next video" implementation
  yet. Use `/api/v1/command` (music's existing endpoint via `ytmd.py`) for
  music next/previous until a unified surface ships.
- `setPlaybackRate` — music side has no playback-rate IPC. Video-only; use
  `/api/v1/video/command` with `setPlaybackRate`.

## `/api/v1/video/*` — video-specific

### `GET /api/v1/video/state`

Auth required. Rate-limited (1/5s/client). Returns the video view's state
regardless of who has the audio bus.

**Response body:**

```json
{
  "paused": bool,
  "currentTime": number,
  "duration": number,
  "volume": number,    // 0.0–1.0
  "muted": bool
}
```

Note this is a **strict subset** of `/playback/state` (no `activeSource`).
Reach for `/video/state` only when you specifically need video's state even
while music is the active source (rare). Otherwise use `/playback/state`.

### `POST /api/v1/video/command`

Auth required. Rate-limited (2/1s/client).

**Currently implemented commands:**

```json
{ "command": "play" }
{ "command": "pause" }
{ "command": "playPause" }
{ "command": "seekTo",          "data": <seconds, ≥ 0> }
{ "command": "seekRelative",    "data": <±seconds> }
{ "command": "toggleFullscreen" }
{ "command": "toggleCaptions" }
{ "command": "toggleTheater" }
{ "command": "setPlaybackRate", "data": <0.25..2> }
{ "command": "search",          "data": "<query, 1..200 chars>" }
{ "command": "navigate",        "data": "home" | "subscriptions" | "library" }
```

**Notes on specific commands:**

- `play` / `pause` / `playPause` / `seekTo` / `seekRelative` force the
  command to the **video view specifically**, regardless of audio-bus state.
  Playing video via this endpoint still pauses music via SourceCoordinator
  (a side effect of the view transitioning to playing, not the API call).
- `toggleFullscreen` / `toggleCaptions` / `toggleTheater` are implemented by
  YTVD's main process sending an `f` / `c` / `t` keypress to the video
  view's webContents. Requires the YouTube player to be loaded on the page;
  on `youtube.com/` (home), these are no-ops.
- `search` and `navigate` change the video view's URL but do **not**
  auto-show the video view. If the user is currently on the music view,
  they won't see the result until they switch via the title-bar toggle or
  tray entry. (Auto-show is a Phase 3 polish item.)
- `setPlaybackRate` is clamped to YouTube's accepted range; the underlying
  call is the YT player API's `setPlaybackRate`.

**Planned commands (NOT YET IMPLEMENTED):**

- Engagement: `like`, `dislike`, `removeRating`, `subscribe`, `unsubscribe`
- Display: `toggleMiniplayer`
- Captions: `setCaptionLang <code>`
- Quality: `setQuality <auto|144|...|2160>`
- Chapters: `nextChapter`, `previousChapter`
- Queue: `addToQueue`, `addToWatchLater`

These need DOM-selector work (engagement buttons, chapter list, quality
menu) and are deferred. Until they ship in YTVD, REX video-specific actions
that depend on them cannot be wired. Check YTVD's
`src/main/integrations/companion-server/api/v1/video.ts` to see the
current command Union before adding a new REX action.

## `/api/v1/*` (root) — music namespace

Unchanged from YTMD upstream. `ytmd.py` already wires this surface.
Authoritative docs:
[YTMD wiki — Companion Server API v1](https://github.com/ytmdesktop/ytmdesktop/wiki/v2-%E2%80%90-Companion-Server-API-v1).

## Audio-bus model

YTVD enforces a single-audio-bus rule in the main process via the
`SourceCoordinator` (`src/main/source-coordinator/index.ts`). The
behavior REX action authors should know:

- Each time a view transitions paused→playing, the other view receives a
  pause command. So at most one source produces audio at a time.
- `activeSource` reflects which view last started playing.
- The visible view and the active source can diverge — a user can browse
  the music UI while a video plays audio. This is intentional.
- Voice commands that hit `/playback/command` don't need to know or track
  active-source state; the coordinator owns it.

## When to use which endpoint

| Voice intent | Endpoint | Module |
|---|---|---|
| "pause", "resume", "skip ahead", "rewind" | `/playback/command` | new YTVD module |
| "volume up", "volume down", "volume 50", "mute", "unmute" | `/playback/command` | new YTVD module |
| "play music", "stop music", "search bohemian rhapsody", "like", "dislike" | `/command` | existing `ytmd.py` |
| "next" / "previous" (music) | `/command` | existing `ytmd.py` |
| "fullscreen", "captions on", "theater mode", "speed up" | `/video/command` | new YTVD module |
| "search youtube cat videos", "go to subscriptions", "show library" | `/video/command` | new YTVD module |
| "like this video", "subscribe", "next chapter" | `/video/command` | not yet wireable |
| Reading current playback state for UI/feedback | `/playback/state` | new YTVD module |

## Adding new YTVD actions to REX — recipe

1. Confirm the YTVD command exists by reading
   `src/main/integrations/companion-server/api/v1/{playback,video}.ts` in
   the YTVD repo. The `APIV1*CommandRequestBody` Type.Union is the
   authoritative list of accepted command literals.
2. Add the method to the lazy client class (mirrors `ytmd.py`'s `YTMD`).
3. Add an `@action` registration with a pattern that doesn't collide
   with existing actions in `ytmd.py` or `spotify.py`. Run `pytest
   test_actions.py` to verify.
4. Update [`ACTIONS.md`](ACTIONS.md) inventory section.
5. Update [`README.md`](../README.md) commands table.

## Open questions / follow-ups

- **Token sharing across YTMD↔YTVD migration.** The user keeps the
  `ytmd_token` keyring slot when moving from YTMD to YTVD — but YTVD is a
  separate package install, so the auth dance must run once against the
  new app's auth window. Not a YTVD-API concern; called out for the
  setup-wizard / docs.
- **WebSocket state push.** YTVD's `/playback` and `/video` plugins emit
  no realtime state pushes yet (only `/state` GET). When they ship,
  document the event names here.
- **Generic next/previous routing.** Once YTVD's video preload gains a
  "next video" handler and `/playback/command` accepts `next`/`previous`,
  move REX's `next_track`/`previous_track` actions from `ytmd.py` to the
  generic YTVD module.
