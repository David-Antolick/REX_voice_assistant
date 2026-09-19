# Smart Apartment Controller — Feasibility

Can REX drive the apartment, not just the PC? Assessment as of
2026-09-19 for three systems: **SmartRent** (door lock), **Brivo Pass**
(building doors / gates / elevator), and the **Alloy wall tablet**
(lights, sensors, thermostat).

Short version: **SmartRent + Alloy: yes, cheaply. Brivo: not directly;
route it through the phone.**

## The key realization: Alloy *is* SmartRent

"Alloy SmartHome" is SmartRent's hardware brand. The wall-mounted
touchscreen is the **Alloy Fusion** hub — a Z-Wave controller with an
integrated thermostat. Everything paired to it (lights, outlets,
sensors, the door lock) is the same device list the SmartRent app shows.

So there are two systems here, not three:

| System | What it controls | Cloud | Resident-usable API |
|---|---|---|---|
| SmartRent / Alloy Fusion | lock, lights, outlets, thermostat, motion/leak sensors | `control.smartrent.com` | **Unofficial but well-mapped** |
| Brivo Pass | building entry doors, gates, elevator | Brivo cloud | **Admin-only official API; none for residents** |

## SmartRent / Alloy — feasible now

**Protocol.** No official resident API, but the app's own protocol is
fully reverse-engineered by `smartrent-py` (Python, on PyPI) and used by
a Home Assistant custom integration and an MQTT bridge.

- REST: `https://control.smartrent.com/api/v2/` — session login with
  email + password, returns access + refresh tokens.
- 2FA: if the account has it on, the sessions endpoint returns a
  `tfa_api_token` and expects a second post with the TOTP code. The
  library handles this (interactively by default). With a stored TOTP
  seed and `pyotp` it can be fully non-interactive.
- Live control: Phoenix websocket at
  `wss://control.smartrent.com/socket/websocket`. Commands are
  `update_attributes` messages on a `devices:<id>` channel; state
  updates stream back the same way.
- Device types the library already models: `entry_control` (lock),
  `switch_binary`, `switch_multilevel` (dimmer), `thermostat`,
  `sensor_notification` (leak / motion).

**Risks.**

- Unofficial. SmartRent can change the protocol; the maintainer has no
  SmartRent unit anymore and merges community fixes only. Two open
  issues (June 2026 websocket bug, a `websockets` version pin). Expect
  to vendor or fork rather than depend blindly.
- Cloud round-trip, not local. The Fusion hub exposes **no local API**;
  every command goes PC → SmartRent cloud → hub. Latency is fine for
  lights; it breaks the "100% local" line in the README, which needs an
  honest caveat.
- Account credentials in REX. Email, password, and (optionally) a TOTP
  seed live in keyring — the same trust level as the Spotify secret
  today, but the blast radius is your front door.

**Fit with REX.** Maps cleanly onto the existing action contract:

- One backend file, `rex_main/actions/smartrent.py`, transport
  `oauth_cloud` (closest existing value; it's token-auth cloud), slot
  `None` (always-on — nothing else competes for "lock the door").
- Lazy client + `safe_call`, same as `spotify.py`. The library is
  asyncio; the client wrapper owns a background event loop thread and
  the actions call into it synchronously. Keep the websocket open so
  commands don't pay a reconnect.
- Setup wizard step: SmartRent email/password (+ TOTP seed), then a
  device discovery pass that lists what it found so the user can name
  lights ("living room", "bedroom") for the regex table.

Candidate actions:

| Capability | Phrases | Notes |
|---|---|---|
| `lock_door` | "lock the door", "lock up" | safe: false positive only locks |
| `unlock_door` | "unlock the door" | **gated** — see below |
| `door_status` | "is the door locked" | read-only |
| `lights_on/off` | "lights on", "living room lights off" | per-switch by configured name |
| `dim` | "dim the lights to 30" | `switch_multilevel` only |
| `set_temp` | "set temperature to 70" | Fusion thermostat |
| `sensor_query` | "any motion in the hall" | read-only |

**The unlock question.** REX listens to a room mic on a PC. "Unlock
the door" heard through a window, from a TV, or from a guest is a
real threat in a way "skip song" never was. This is the
"never surprise destructively" principle from `PC_CONTROL_PLAN.md`
applied to physical security. Options, from safest:

1. Ship `lock` only. Unlock stays on the phone. (Recommended default.)
2. Unlock requires a spoken passphrase in the same utterance and a
   HUD confirmation, and is off unless explicitly enabled in settings.
3. Unlock allowed with no gate. Not recommended.

Whatever the choice, unlock should be `no_early_match=True` so the
FastVAD early path can never fire it from a partial transcript.

## Brivo Pass — not directly feasible; go through the phone

**Official API.** Brivo has a real REST API (OAuth2, API key, door
"activate" endpoints, remote unlock). It is for **Brivo Access
administrators** — the property manager's account. Client credentials
come from registering an application in the admin console, which a
resident can't do. Not an option unless the building's management
hands you an integration account, which they won't for one resident.

**Mobile SDK.** Brivo publishes its mobile SDK on GitHub (Android and
iOS). It shows the protocol shape — passes redeem with a `passId` +
`passCode`, and there is an `UnlockStrategy` that forces an internet
unlock without BLE proximity — but it also requires a Brivo-issued
`clientId` / `clientSecret`. The only way to get one as a resident is
to pull the Brivo Pass app's own credentials out of the APK and
impersonate the app. That is a ToS violation against a building
access-control system, it's brittle (Brivo rotates and attests), and
the consequences of a mistake are worse than getting a Spotify token
wrong. **Not recommended.**

**What actually works: drive the phone.** Brivo Pass supports
**Siri Shortcuts** (iOS) and door widgets; on Android an unofficial
tool already exists that automates the app through accessibility
services. So the workable architecture is:

```
REX (PC)  --HTTP-->  phone bridge  -->  Brivo Pass app  -->  Brivo cloud  -->  door
```

- **iOS:** a Shortcut "Open front gate" backed by Brivo's Shortcut
  action, triggered remotely. Triggering from a PC is the hard part;
  options are a Pushcut-style webhook → automation, or a Home Assistant
  companion-app notification action. None is zero-setup.
- **Android:** Tasker with AutoInput (or the accessibility approach
  the open-source tool uses) exposed via Tasker's HTTP request event
  or Join. Reasonable if you already run Tasker; hacky otherwise.

Either way this is a `phone_bridge` transport, not something REX
talks to natively, and reliability depends on the phone being awake,
unlocked as needed, and on the same network or reachable. Worth
building only if "open the gate" from the desk is genuinely a daily
want (delivery drivers, guests). Otherwise: skip Brivo, or wait to see
if Brivo's Apple Wallet / Home key path eventually lands in HomeKit.

## Recommendation

1. **Phase A — SmartRent backend, lock-only + lights + thermostat.**
   One backend file, one wizard step, ~a weekend. Prove the websocket
   stays healthy across a day of uptime before adding more.
2. **Phase B — sensors as queries, dimmers, named-room grouping.**
3. **Phase C — decide on unlock** after living with A. Default no.
4. **Brivo: phone bridge or nothing.** Prototype with a Siri Shortcut
   or Tasker task first, by hand, before wiring REX to it. If the
   manual trigger is flaky, REX won't fix that.

Before writing any code: confirm with a 20-line script that
`smartrent-py` logs into *your* account (2FA on or off) and lists the
Fusion's devices. That single check settles the whole SmartRent half.

## Open questions

- Does the account have 2FA on? Determines whether the TOTP-seed path
  is needed on day one.
- Which of the Fusion's devices are actually Z-Wave and show in the
  app? Some Fusion "sensors" (e.g. its built-in temperature) may not
  be exposed as separate devices.
- How does REX handle a cloud backend that's down? Today `safe_call`
  logs and returns None. For a lock command that needs to be a visible
  HUD failure ("couldn't reach SmartRent"), not silence.
- Does the README's "100% local, no cloud APIs" claim get a
  "except opt-in home integrations" clause?

## Sources

- smartrent-py (protocol, endpoints, 2FA flow, device types):
  https://github.com/ZacheryThomas/smartrent-py
- Home Assistant SmartRent component (proof the protocol is
  usable long-running): https://github.com/ZacheryThomas/homeassistant-smartrent
- SmartRent MQTT bridge: https://github.com/AMcPherran/SmartRent-MQTT-Bridge
- Alloy Fusion hub announcement (Z-Wave, integrated thermostat,
  app + on-device control):
  https://z-wavealliance.org/smartrent-launches-alloy-fusion-z-wave-hub/
- Brivo API docs (admin OAuth2 + API key): https://apidocs.brivo.com/
- Brivo mobile SDK (client credentials required, internet unlock
  strategy): https://github.com/brivo-mobile-team/brivo-mobile-sdk-android
- Brivo Pass Siri Shortcuts / widgets:
  https://apps.apple.com/us/app/brivo-mobile-pass/id1033578819
- Android accessibility-automation approach:
  https://github.com/n-4t/brivo-location-link
