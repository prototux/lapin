# Server guide

The server is the brain of Lapin. It is one Python process with two parts:

- **Gateway:** a WebSocket gateway for the devices, port 8765.
- **Admin web UI:** a Flask app, port 8090.

All state lives in one data directory: `settings.json`, the SQLite database
and the wake word recordings.

## Installing and running

Requirements:

- Linux;
- Python 3.11 or newer;
- `ffmpeg`, which decodes radio streams and Telegram voice notes;
- the Python packages in `server/requirements.txt`, which `run.sh`
  installs.

```sh
cd server
./run.sh                    # data in server/data
DATA=/var/lib/lapin ./run.sh -v   # another data directory, debug logs
```

`run.sh` creates `.venv` next to itself on the first run, and again whenever
`requirements.txt` changes. You can also run the server by hand:
`python -m assistant --data DIR [-v]`.

### Running as a service

```ini
# /etc/systemd/system/lapin.service
[Unit]
Description=Lapin voice assistant
After=network-online.target
Wants=network-online.target

[Service]
User=lapin
Environment=DATA=/var/lib/lapin
ExecStart=/opt/lapin/server/run.sh
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Backups

Stop the server and copy the data directory. It holds everything: settings
with the service tokens, people, conversations, memories, timers, devices
and wake word recordings. Treat it as sensitive, because the tokens are
stored in clear.

## The admin web UI

http://<server>:8090. Set **Settings → Server → Web UI password** before
anything else. Without it, the UI and its API are open to the whole
network.

| Page | What you do there |
|---|---|
| **Dashboard** | service health, satellites, live activity, recent turns |
| **Devices** | approve or reject new devices; set name, room and owner; volume; remove. Korvo cards also have *Device log* and *Update firmware* |
| **Conversations** | every turn: what was heard, the model's tool calls, the answer, timings |
| **Talk & chat** | text chat with the assistant, or use this browser as a satellite (microphone and speaker) |
| **Timers** | running timers, alarms and reminders; create or cancel |
| **Media** | what plays where; start radio or music in a room |
| **People** | household members, their own Jellyfin and Navidrome accounts, their memories |
| **Skills** | enable or disable each skill, see its tools and example requests (click one to try it) |
| **Integrations** | messaging channels (Telegram, Signal, web) and linking senders to people |
| **Settings** | everything below |
| **Logs** | the server log, live |

## Settings

The most important settings, by section. They are saved in
`<data>/settings.json`, and you only need to change what differs from the
defaults.

### Server

- **Server name.**
- **Approve new devices automatically:** leave it off.
- **Web UI password.**
- **Ports:** the gateway (8765) and web (8090) ports are set in
  `settings.json` (`server.gateway_port`, `server.web_port`) and take effect
  after a restart.

### Speech recognition, Speech synthesis, Language model

Each one has a base URL ending in `/v1`, a token and a model.

- **Speech synthesis:**
  - **Voice:** the default is `serena`.
  - **Voice for French:** a separate voice, if you want one.
  - **Delivery instructions:** keep them positive. Describe the tone you
    want ("calm, clear, conversational") and don't list the sounds to avoid:
    naming laughter or humming tends to cause them.
  - **Each sentence** is sent with its detected language.
  - **Retakes:** a take that runs much longer than its text (stray noises,
    babbling) is generated again, up to three times.
- **Language model:**
  - **Disable reasoning:** on by default. It sends
    `chat_template_kwargs.enable_thinking=false` for Qwen-style models.
  - **Max tool rounds:** how many tool calls a turn can chain.
  - **Turns of history:** how much of the conversation is resent.

### Assistant

- **Name, persona:** the model's system prompt.
- **About the household:** free text given to the model, for example who
  lives here and the names of pets.
- **Home location and time zone:** for the weather and times.
- **Reply language:**
  - `auto` answers in the language of each request;
  - `en` or `fr` forces one language.
  - Very short requests ("oui", "stop") keep the language of the
    conversation.
- **Follow-up mode:** after an answer that ends with a question, the
  speaker listens again for *Follow-up window* ms, with no wake word.
- **Tries before giving up:** after this many requests in a row that it
  can't understand (3 by default), it says it doesn't understand and ends
  the conversation.

### Wake word

The room speakers detect the wake word themselves. The server then
transcribes the start of the audio and checks that it matches one of the
**Wake phrases**: this is *stage 2*, and it cuts false wakes from TV and
music. *Learned spellings* collects the ways the recognizer writes your wake
phrase ("des lapins", "dilapin"…). They are learned from enrollment samples.

### Turn taking

The server decides when you've finished speaking:

- **Quick end of turn:** after 800 ms of silence, if the sentence reads as
  complete.
- **Otherwise:** after 1.6 s of silence.
- **Hard limit:** 20 s of audio.

Phones and computers use near-field gating: voices much quieter than yours,
such as a TV or colleagues, don't count as speech.

### Privacy

- **Keep transcripts:** on by default.
- **Keep request audio:** off.
- **Delete history after N days.**

The **Memory** skill only stores what someone explicitly asks it to
remember. Memories can be deleted on the People page.

### Quiet hours

Reminders are softer during quiet hours, or sent as a message instead.

### Media

- **Radio stations:** one per line, `Name | URL`.
- **Music level.**

## Integrations

All of these are optional. A skill whose service isn't configured is shown
as *Not configured* and isn't offered to the model.

| Service | Where to configure | Used for |
|---|---|---|
| **Navidrome / Subsonic** | People → *Accounts* (per person, or the `household` user) | "play Daft Punk", albums, playlists, next/previous, what's playing |
| **Jellyfin** | People → *Accounts* (URL plus user/password or API key) | music, TV show and movie recommendations (unwatched, genres, next up), remote control of Jellyfin apps |
| **Home Assistant** | Settings → *Home Assistant*: URL and long-lived access token | lights, switches, covers, climate, scenes; "the lights" means the asking speaker's room |
| **Web search** | Settings → *Web search*: URL of a self-hosted [OpenSERP](https://github.com/karust/openserp) | current events, anything the model shouldn't guess |
| **Weather** | nothing (Open-Meteo, no key) | forecasts for the home location or any town |
| **Wikipedia** | nothing | encyclopedic facts |

A request uses the speaker's own account when it has one. Otherwise it uses
the `household` account, and failing that, anyone's.

## Skills

| Skill | Examples |
|---|---|
| clock | "quelle heure est-il ?", "what's the date on Friday?" |
| timers | "minuteur de 10 minutes", "wake me up at 7", "remind me to call mum tomorrow at 6 pm" |
| calc | "15 % of 240", "how many cups in a litre?" |
| weather | "il va pleuvoir demain ?" |
| knowledge | "who wrote Les Misérables?" |
| search | "what's the latest news about the Artemis mission?" |
| media | "play FIP in the kitchen", "stop", "volume down" |
| subsonic / jellyfin | "mets l'album Discovery", "next song", "what's playing?", "recommend a TV show I haven't watched" |
| homeassistant | "turn off the lights", "règle le chauffage à 20 degrés" |
| devices | "announce dinner is ready", "intercom to the bedroom", "where are my speakers?" |
| messages | "send Alice a message: I'm on my way" (through her messaging channel) |
| memory | "remember that the gate code is 1234", "what's the gate code?" |

Phones and computers add their own **device tools** while they are
connected (see [android.md](android.md) and
[desktop/README.md](../desktop/README.md)). A tool marked *asks first*
(calls, messages, power actions) is always confirmed: on screen with Yes/No
buttons, or by voice.

Common requests skip the language model entirely and are handled in
milliseconds by fast paths in `nlu.py`: time, timers, play, next, stop,
volume, what's playing.

## Messaging channels

Settings are on **Integrations**. Each channel can be turned on separately.
A message from an unknown sender is listed there so you can link it to a
person in one click.

- **Telegram:** create a bot with @BotFather and paste its token. It uses
  long polling, so no public URL is needed. Voice notes are transcribed, and
  it can answer with a voice note.
- **Signal:** run
  [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) in
  `json-rpc` mode, register a number for the assistant once, and enter the
  API URL and the number:

  ```sh
  docker run -d --name signal-api -p 8080:8080 -e MODE=json-rpc \
      -v signal-data:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
  ```

- **Web:** the chat on the **Talk & chat** page.

## Firmware updates for the Korvo

After `korvo/build.sh`, the server finds the new image in `korvo/dist/`. To
update a board over Wi-Fi, use **Devices → (the Korvo) → Update firmware**.
The board keeps the new firmware only if it reaches the server again within
90 s. Otherwise it rolls back to the previous image.

## Troubleshooting

- **The Dashboard shows a service in red:** check the URL (it must end in
  `/v1`), the token and the model name. `curl <url>/models` should answer.
- **It hears nothing, or the recognition comes back empty:** look at the
  *Logs* page.
  - A speech service that runs out of GPU memory on long clips returns
    errors 500. The server already splits long audio and retries, but the
    service may need a restart.
- **It answers in the wrong language:** set *Reply language* to `auto`.
  The server also detects an answer in the wrong language and regenerates
  it.
- **The model claims it did something it didn't:** the server detects this
  and regenerates the answer. If it keeps happening, use a larger model or
  lower the temperature.
- **A device stays "waiting for approval":** approve it on the **Devices**
  page. A device that was removed comes back as pending.
