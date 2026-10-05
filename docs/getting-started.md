# Getting started

This guide takes you from an empty machine to a first spoken answer. Plan an
hour if your speech and language model services already run, longer if you
have to set them up.

> [!WARNING]
> Lapin is a proof of concept with no real security (see the
> [README](../README.md#limitations-and-missing-features)). Install it on a
> private home network, never on a machine reachable from the internet.

## 1. What you need

- **A server machine.**
  - Linux, Python 3.11 or newer, and `ffmpeg`.
  - The server itself is light: well under 1 GB of RAM and no GPU.
- **Three AI services with an OpenAI-compatible HTTP API**, on the same
  machine or elsewhere on your network:
  - **Speech to text:** `POST /v1/audio/transcriptions`, for example
    Parakeet or Whisper behind a compatible server.
  - **Text to speech:** `POST /v1/audio/speech`. Lapin was tuned for
    Qwen3-TTS CustomVoice served by vLLM-Omni. It sends the `voice`,
    `instructions` and `language` fields, and other engines that accept
    those work too.
  - **Language model:** `POST /v1/chat/completions` with tool calling, for
    example a Qwen 3 model on vLLM. Small models handle the tools poorly;
    expect good results from about 20B parameters.
- **At least one client:**
  - a ReSpeaker Core v2 or an ESP32-Korvo V1.1 for a room;
  - an Android phone;
  - a Linux, Windows or macOS computer;
  - or just a browser, using the admin UI's **Talk** page.

## 2. Start the server

```sh
git clone https://github.com/prototux/lapin.git
cd lapin/server
./run.sh
```

The first run creates `server/.venv` and installs the dependencies. The
server then listens on two ports:

- **8090:** the admin web UI, http://localhost:8090.
- **8765:** the device gateway, `ws://<server>:8765/v1/device`, where all
  clients connect.

Settings, the database and the wake word recordings go to `server/data/`.
To keep them elsewhere, start it as `DATA=/var/lib/lapin ./run.sh`. To run
it as a service, see [server.md](server.md#running-as-a-service).

## 3. Configure it

Open http://localhost:8090 and go to **Settings**:

1. **Server** (at the bottom of the page): set a **Web UI password**
   first. It protects the admin UI, which shows every conversation.
2. **Speech recognition, Speech synthesis, Language model:** the base URL
   (ending in `/v1`), the model name and the token, if your service needs
   one. The **Dashboard** shows a green dot for each service it can reach.
3. **Assistant:** its name, reply language, persona, home location (for
   the weather) and time zone.

Then go to **People** and add each member of the household, in lower case
(for example `alice`). Phones and computers are bound to one of these names.
Music accounts, memories and messaging links are kept per person.

**Check:** open **Talk & chat** and type "what time is it?". You should get
an answer. Click the microphone to try your voice through the browser.

## 4. Connect a client

| Client | How | Guide |
|---|---|---|
| ReSpeaker Core v2 | `satellite/deploy.sh respeaker@<board-ip> ws://<server>:8765/v1/device` | [satellite-respeaker.md](satellite-respeaker.md) |
| ESP32-Korvo V1.1 | `korvo/build.sh` once, then `korvo/flash.sh` | [korvo/README.md](../korvo/README.md) |
| Android phone or TV | build and install the APK, then fill in the setup screen | [android.md](android.md) |
| Computer | `desktop/lapin`, then fill in the settings window | [desktop/README.md](../desktop/README.md) |

Every new client shows up on the **Devices** page as *pending*. Click
**Approve**. Until then, the client says it is waiting for approval.

## 5. Set up the wake word (room speakers)

The room speakers detect your own recordings of the wake phrase rather than
a pre-trained model.

1. Open the ReSpeaker's page at `http://<board-ip>:8080`, tab **Wake word**.
2. Record 3 to 5 samples from where you usually stand, at a normal voice.
   "Dis Lapin" or "Hey Lapin" work well.
3. Click **Set the threshold from my samples**.

The recordings are sent to the server, and every Korvo downloads them too.
On the server's **Settings** page, under *Wake word*, list the phrases you
recorded. The server uses them for its second check of each wake.

## 6. Talk to it

- "Dis Lapin… quelle heure est-il ?"
- "Hey Lapin… set a timer for ten minutes."
- "Dis Lapin… mets de la musique de Daft Punk." (needs Navidrome or Jellyfin, see [server.md](server.md#integrations))
- "Hey Lapin… what's the weather tomorrow?"
- "Dis Lapin… souviens-toi que le code du portail est 1234."

If something goes wrong, the **Logs** and **Conversations** pages show what
was heard, what the model did, and why.

## Next steps

- Add music, Home Assistant and web search: [server.md](server.md#integrations).
- Link Telegram or Signal so you can write to the assistant: [server.md](server.md#messaging-channels).
- Tune the room speakers: [satellite-respeaker.md](satellite-respeaker.md#tuning).
