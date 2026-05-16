# Plan: Fork YTMD for YouTube Video Support

## Context
Hard fork of [ytmdesktop/ytmdesktop](https://github.com/ytmdesktop/ytmdesktop) (GPL-3.0, Electron + TypeScript + Vue + Vite/Forge). Goal: keep music functionality intact, add youtube.com video control as a parallel feature. Drives the REX voice assistant via the existing companion-server protocol on `127.0.0.1:9863`. Personal project, no SLA, best-effort maintenance. Distant stretch goal: architectural refactors land upstream as PRs.

## Naming / branding
- Pick a neutral name (not "YouTube *anything*" — trademark). Candidates: `ytdesktop`, `yt-companion`, `ytm-plus`. **Decide before forking.**
- New icon (don't reuse YTMD's).
- README opens with "Forked from [ytmdesktop/ytmdesktop](https://github.com/ytmdesktop/ytmdesktop) — thanks to the YTMD team for the foundation."
- License stays GPL-3.0, all upstream copyright notices preserved.
- Send a friendly heads-up email to YTMD maintainers after first public release.

## Architecture decisions to lock in upfront

1. **One window or two?** Two BrowserViews running concurrently (music + video can both be open). Lean: **two**, separate windows.
2. **Companion server: extend or split?** Extend existing port 9863 with namespaced routes `/api/v1/video/*`. Same auth handshake, one token. Lean: **extend**.
3. **Refactor before adding, or add then refactor?** Add video first (faster feedback), then refactor common infra into a `youtube-property` base class. The refactor PR is what *might* land upstream — but isn't worth doing first if it slows down getting video working.

## What to keep (largely untouched)
- Electron shell, Forge config, Vite build
- `src/main/integrations/companion-server/` — extend with video routes
- `src/main/integrations/{custom-css,discord-presence,notifications,volume-ratio}` — keep
- `src/renderer/{components,windows,store-ipc}` — keep, extend
- Settings UI scaffolding — add a "Video" section
- Existing music functionality — zero regressions

## What to add

### `src/renderer/ytvideoview/` (sibling of `ytmview`)
BrowserView loading `www.youtube.com`. Preload script extracts state via:
- `<video>` element directly: `currentTime`, `duration`, `paused`, `volume`, `playbackRate`
- YT player API: `document.querySelector('#movie_player').getPlayerState() / .seekTo() / .playVideo() / .pauseVideo() / .getVideoData()`
- DOM selectors for: like, dislike, subscribe, theater, miniplayer, fullscreen, captions toggle, next button, autoplay toggle, chapter list, quality menu

Pushes state diffs to main via IPC, mirroring `ytmview` patterns.

### Companion server: `/api/v1/video/*`

Commands:
- Playback: `play`, `pause`, `play-pause`, `seek-to <s>`, `seek-relative <±s>`
- Navigation: `next`, `previous`, `next-chapter`, `previous-chapter`
- Volume: `set-volume <0-100>`, `mute`, `unmute`
- Speed: `set-playback-rate <0.25-2.0>`
- Engagement: `like`, `dislike`, `remove-rating`, `subscribe`, `unsubscribe`
- Display: `toggle-fullscreen`, `toggle-theater`, `toggle-miniplayer`
- Captions: `toggle-captions`, `set-caption-lang <code>`
- Quality: `set-quality <auto|144|...|2160>`
- Queue: `add-to-queue`, `add-to-watch-later`
- Browse: `navigate <home|subs|library>`, `search <query>`

State (GET + WS push):
- videoId, title, channel, channelId
- duration, currentTime, paused
- volume, muted, playbackRate, quality
- chapters (list with start times), currentChapterIndex
- captions: enabled, language, availableLanguages
- likeState, subscribeState
- displayMode: default/theater/miniplayer/fullscreen
- isLive, isAdPlaying

### Settings UI section: "Video"
- Default URL on launch
- Default playback speed
- Companion server video API enable toggle

### Optional later
- Video-aware Discord rich presence template

## What to rip out / leave alone
- Last.fm scrobbling — doesn't apply to videos, leave music-only
- Music-specific Discord presence — leave music-only, add a separate video template later

## Refactor opportunity (the upstream-PR play)
After video works end-to-end in your fork, do a clean separate PR against upstream YTMD that:
- Pulls the BrowserView + preload + IPC + companion-property pattern into a `src/main/youtube-property/` base class
- `ytmview` becomes one consumer of the abstraction
- Doesn't add video to upstream — just enables forks like yours to live alongside cleanly

This is the PR that has a real shot at merging because it benefits YTMD itself (cleaner architecture) without scope-creeping their identity.

## REX-side changes (rex_voice_assistant repo)

New file: `rex_main/actions/youtube_video.py`
- Lazy client singleton talking to companion-server video API
- Mirror `ytmd.py` patterns: `safe_call` wrapping, keyring token (reuse existing companion auth), config from `config.yaml`
- Wrappers stay thin per the actions contract

Action registry entries (phrasings TBD — phrase-collision-safe with music slot):
- `pause_video` — "pause video"
- `play_video` — "play video", "resume video"
- `skip_forward` — "skip ahead", "skip thirty seconds"
- `skip_backward` — "go back", "rewind ten seconds"
- `like_video` — "like this video", "thumbs up video"
- `next_video` — "next video"
- `toggle_fullscreen` — "fullscreen", "exit fullscreen"
- `toggle_theater` — "theater mode"
- `toggle_captions` — "captions on", "captions off"
- `next_chapter` — "next chapter", "skip chapter"
- `set_speed_*` — "speed up", "slow down", "normal speed"

Slot question: probably a new `video` slot, **not** unified with `music`. Phrase disambiguation per the existing "Phrase-based disambiguation" decision — natural phrases first, prefix only on collision.

Files to update on REX side:
- `rex_main/actions/__init__.py` — register new module
- `rex_main/actions/service.py` — `youtube_video_module.warm()` call
- `docs/ACTIONS.md` — add inventory section
- `README.md` — add commands table rows
- `rex_main/default_config.yaml` — add video API config keys
- New `DECISIONS.md` entry recording the fork choice

## Phasing

**Phase 0 — Fork setup (1-2 hours)**
- Fork, rename, update `package.json` + branding strings + icon
- Verify: builds, packages, music still works unchanged

**Phase 1 — MVP video view (one weekend)**
- `ytvideoview` BrowserView loading youtube.com
- Tray menu entry to open it
- Preload exposes: paused state, currentTime, basic play/pause/seek commands
- Companion server: `/api/v1/video/state` + `/api/v1/video/command` for those commands
- REX side: 3-4 actions wired up, manual smoke test

**Phase 2 — Full command surface (one or two weekends)**
- Remaining commands (like/subscribe/fullscreen/theater/captions/speed/chapters)
- WS state-update pushes
- REX side: complete `youtube_video.py` action set

**Phase 3 — Polish**
- Settings UI "Video" section
- Optional: Discord video presence
- Decide whether to chase the upstream-mergeable refactor PR

## Legal / safety constraints
- No ad-skipping (no CSS that hides ads, no auto-click "Skip Ad")
- No yt-dlp, no video downloads
- No auto-like / auto-subscribe without explicit voice intent
- README disclaimer: "Not affiliated with Google or YouTube"
- No YouTube logo, neutral icon
- Mirror YTMD's posture exactly — they've operated safely in this gray zone for 6+ years

## Open questions to resolve before coding
1. **Repo name?**
2. **Two windows or one with view-switching?** (lean: two)
3. **Slot model: separate `video` slot, or unified `media` slot with active context?** (lean: separate)
4. **Want video Discord rich presence in v1, or defer?** (lean: defer)
5. **Do the upstream refactor PR before, after, or never?** (lean: after MVP works)
