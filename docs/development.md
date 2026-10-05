# Developer guide

How the code is organised, how a request flows through it, and how to build
and test each part.

**Languages:** C and Python, plus Kotlin for Android. Avoid JavaScript apps;
the admin pages use small vanilla JS.

**No heavy frameworks:**

- **Server:** Flask and websockets.
- **Desktop app:** PySide6.
- **Korvo firmware:** ESP-IDF and ESP-SR.

## Repository layout

```
server/                 Python server
  assistant/
    __main__.py         entry point: python -m assistant --data DIR
    app.py              wires everything together
    gateway.py          device WebSocket: hello/pairing, audio, routing of messages
    devices.py          DeviceSession: one connected device, its tools, playback
    sessions.py         TurnManager: wake → listen → end of turn → answer, follow-ups, misses
    audio.py            VAD (WebRTC + adaptive energy floor + near-field gating), decoding
    verifier.py         stage-2 wake word check
    clients.py          STT / TTS / LLM HTTP clients (OpenAI-compatible)
    orchestrator.py     one turn: fast paths, prompt, LLM tool loop, guards
    nlu.py              regex fast paths (time, timers, play, next, stop, volume…)
    router.py           output routing: speech to the asking device, music to rooms, ducking
    composer.py         makes the answer speakable (no markdown, no stage directions)
    lang.py             French/English detection, fixed phrases
    guardrails.py       confirmation of risky tools, rate limits
    skills/             tools exposed to the model, one module per skill
    channels/           Telegram, Signal, web chat
    media.py            radio / music streams, multi-room
    firmware.py         Korvo OTA and remote logs
    wakewords.py        wake word recordings shared between satellites
    store.py            SQLite
    webui.py, static/   admin web UI (Flask + vanilla JS)
  tests/fake_device.py  end-to-end voice turn without hardware
satellite/              ReSpeaker Core v2
  engine/src/           satd: AEC (sbaec, aec, res), DOA (track), beam, ns, kws, mixer, ipc
  engine/tests/         offline tools: simulate.py, kwstest, kwsscan, replay_asr.py
  agent/satagent/       agent: link to the server, web UI, button, plugins/leds
  install.sh deploy.sh systemd/
korvo/                  ESP32-Korvo V1.1 firmware (ESP-IDF 5.5)
  main/                 board code: audio (ESP-SR AFE), LEDs, buttons, Wi-Fi portal, OTA
  components/satcore/   portable core: protocol, wake word (port of kws.c), mixer, OTA logic
  test/                 host tests and QEMU apps
  build.sh flash.sh flash.py
android/                Kotlin app (Compose): voice/, core/, protocol/, tools/, music/, audio/, ui/, car/
desktop/                PySide6 app: lapin_desktop/, tests/
docs/                   guides, PROTOCOL.md, algorithms.md
```

## How a voice request flows

```mermaid
sequenceDiagram
  participant S as Satellite
  participant G as Gateway / TurnManager
  participant STT
  participant O as Orchestrator
  participant LLM
  participant TTS
  S->>G: wake {source: kws, preroll} + 0x01 audio
  G->>STT: pre-roll (stage-2 wake check)
  G-->>S: eot (server-side VAD + semantic end of turn)
  G->>STT: request audio
  G->>O: transcript
  O->>O: NLU fast path?
  O->>LLM: prompt + tools
  LLM-->>O: tool calls
  O->>O: run skills / device tools (confirm if risky)
  LLM-->>O: answer, streamed
  O->>TTS: sentence by sentence (per-sentence language)
  TTS-->>S: stream_open + 0x02 audio
  G-->>S: session_end {follow_up}
```

Guards in `orchestrator.py` catch the usual failure modes of local models
before an answer is spoken:

- **Wrong language:** an answer in the wrong language is regenerated.
- **Claimed action:** "I've turned off the lights" with no tool call is
  regenerated.
- **Refusals:** they are not kept in the history.
- **Preambles:** a preamble before a silent tool is dropped.
- **Misunderstood requests:** they are counted. After
  `assistant.max_misses` in a row, the turn ends.

## Adding a skill

Create `server/assistant/skills/<name>.py`. Modules are discovered
automatically.

```python
"""Jokes (one line describing the skill: shown on the Skills page)."""

from . import tool

EXAMPLES = {"en": ["Tell me a joke"], "fr": ["Raconte-moi une blague"]}


@tool("Tell a short, family-friendly joke.",
      {"topic": ("string", "optional topic")})
def tell_joke(ctx, topic=""):
    return {"joke": "…"}           # a short JSON-able dict, sent back to the model
```

- **The description is the prompt.** Say when to use the tool and when not
  to: the model picks tools from their descriptions alone.
- **Arguments and options.**
  - `ctx` gives `ctx.settings`, `ctx.user`, `ctx.room`, `ctx.device_id`,
    `ctx.app` (store, media, devices…).
  - `risk="confirm"` makes the server ask the user before running it.
  - `available=lambda app: …` hides it when its service isn't configured.
- **Errors:** return `{"error": "…"}` with a hint the model can act on ("no
  home location configured; ask the user for a city"). Don't raise.
- **Frequent, unambiguous commands** deserve a fast path in `nlu.py`, which
  answers in milliseconds without the model.

## Adding a device tool or a new client

A client declares its own tools in `hello` (or later with `tools`), with a
JSON schema, `confirm`, `silent` and `replaces`. The server offers them to
the model only for that client's own requests, and relays the calls as
`tool_call` and `tool_result`. See [PROTOCOL.md](PROTOCOL.md):

- §1: pairing;
- §5: device tools;
- §6: confirmations.

The Android `ToolSpecs.kt` and desktop `lapin_desktop/tools/` are working
examples.

## Building and testing

The development machine had no swap, and every heavy build runs inside a
memory cap (`systemd-run --user --scope -p MemoryMax=…`). The build scripts
do this for you when systemd is available.

| Part | Build | Tests |
|---|---|---|
| server | `server/run.sh` | `python tests/fake_device.py "what time is it?"` against a running server (end to end, uses the TTS to synthesize the question) |
| satellite engine | `make -C satellite/engine` (on the board, or with an ARM cross compiler) | `satellite/engine/tests/simulate.py` (synthetic room, AEC/DOA numbers); `kwstest`/`kwsscan` on recordings; `replay_asr.py` (needs `STT_TOKEN` if your STT wants one) |
| Korvo firmware | `korvo/build.sh` (ESP-IDF v5.5 in `~/esp/esp-idf` or `IDF_PATH`) | `korvo/test/run_tests.sh`: host tests of satcore (KWS, mixer, protocol, log buffer, OTA); with a server running, also on the real wake word recordings |
| Android | `android/build.sh` (JDK 17–21) | `./gradlew testDebugUnitTest` (JVM tests); `LiveServerTest` with `LAPIN_LIVE_WS=…` against a server |
| desktop | `desktop/lapin` | `.venv/bin/python -m unittest discover -s tests` (`QT_QPA_PLATFORM=offscreen` headless) |

### Testing without hardware

- **The browser:** the admin UI's **Talk & chat** page is a full satellite,
  with microphone, speaker and the same protocol.
- **A fake device:** `server/tests/fake_device.py` runs a whole voice turn
  through the WebSocket.
- **A fake Korvo:** `korvo/test/hostsat` runs the Korvo's portable core on
  your PC, connected to a real server through `ws_bridge.py`.

## Conventions

- **Small commits with readable messages**, one topic per commit.
- **No secrets in the repository.**
  - Tokens and accounts live in the server's data directory
    (`server/data/`, git-ignored).
  - Defaults in code point to `localhost` or `assistant.local`.
- **Matching style:**
  - **Python:** 4 spaces, lines up to about 120 characters, short docstrings
    that explain *why*.
  - **C:** K&R style with 4 spaces. Every buffer is fixed-size or allocated
    once at start: no allocation in the audio loop.
  - **Kotlin:** the Android Studio defaults.
- **User-facing text:** keep it in French and English.
  - Android: `values/` and `values-fr/`.
  - Server phrases: `lang.py`.
