# Korvo satellite

Firmware that turns an **Espressif ESP32-Korvo V1.1** into a satellite of the
assistant, like the ReSpeaker satellites: the wake word ("dis lapin") is
detected on the board, the request is streamed to the server, the answers and
the music play on its speaker, and the LED ring shows what it is doing.

`flash.sh` writes the images in `dist/`. That folder is not in the git
repository. Each [release](https://github.com/prototux/lapin/releases/latest)
has it ready: download `lapin-korvo-<version>.zip`, unzip it and run
`flash.sh` from there. Or build it with `./build.sh` (needs ESP-IDF v5.5, see
[Building from source](#building-from-source) below). Flashing itself does
not need ESP-IDF.

The release also has `lapin-korvo-<version>-full.bin`: the bootloader,
partition table and firmware in one image, for other tools (the ESP web
flasher, `esptool.py write_flash 0x0 lapin-korvo-<version>-full.bin`). It
holds no settings: on first boot the board opens its setup access point
(see [Changing the settings later](#changing-the-settings-later)).

## Flashing in 3 steps

**1. Plug the board in.** The Korvo mainboard has two micro-USB ports: use the
one labelled **USB-UART** (the other one only gives power), with a data cable,
and turn the power switch on. Connect a 4 Ω speaker to the speaker connector
(nothing in the headphone jack: it turns the amplifier off).

- Linux: the USB-UART chip (Silicon Labs CP210x) needs no driver. If you get
  "no permission", add yourself to the serial group and log in again:
  `sudo usermod -aG uucp $USER` (Manjaro / Arch) or `dialout` (Debian / Ubuntu).
- macOS / Windows: install the Silicon Labs "CP210x VCP" driver if no serial
  port appears.

**2. Flash** (Linux / macOS; on Windows run `python flash.py`):

```sh
cd korvo
./flash.sh
```

It finds the board, asks for the Wi-Fi network and password, the server URL
(default `ws://assistant.local:8765/v1/device`), a name and a room, then writes
the firmware and the settings. Everything can also be given on the command
line:

```sh
./flash.sh --ssid MaisonWifi --password 'secret' --name "Korvo cuisine" --room cuisine --monitor
```

The first run creates `.venv-flash/` with esptool (needs Python 3.8+ and
internet once). It reads the board's flash size first (any module with 4 MB
or more works) and prints the device id (`korvo-<MAC>`). If esptool cannot
connect, put the board in download mode by hand: **hold BOOT, press and
release RST, release BOOT**, and run the command again.

After flashing it resets the board and reads its serial log for 25 s, then
prints a short summary, for example:

```
--- Boot check --------------------------------------------------
Flash:      16 MB
Booted:     yes, firmware 1.1.0
PSRAM:      8 MB
Audio:      codecs OK
Wi-Fi:      joined "MyWiFi", IP 192.168.1.57
Server:     reached, WAITING FOR APPROVAL: approve korvo-246f28aabbcc on the server's Devices page
Raw log:    korvo/boot-check-20261004-221500.log
```

It says so when the board crashes (with the reason and the backtrace), cannot
join the Wi-Fi (network not found / wrong password), or cannot reach the
server. `--no-check` skips it; `./flash.sh --check-only` runs it again later.

**3. Approve it on the server.** Open the Devices page of the admin UI
(http://localhost:8090 on the server, or `http://<server>:8090`): a new
satellite named as you chose (id `korvo-<MAC>`) waits for approval; approve
it. Meanwhile its ring shows two slowly turning blue dots. Once approved it
downloads the wake word (a few seconds) and is ready: say "dis lapin".

### Changing the settings later

- `./flash.sh --config-only` rewrites only the settings (Wi-Fi, server, name,
  room). The pairing is kept: it lives in another flash partition.
- Without a computer: hold **SET** for 3 seconds (it also opens by itself
  when the configured network cannot be joined for 30 s, or when no network
  is configured; it keeps retrying the network and closes once connected). It opens
  an open Wi-Fi network **Korvo-Setup-XXXX**; join it with a phone, the setup
  page opens (otherwise browse to http://192.168.4.1), choose the network,
  type the password, check the server URL, save. It restarts.
- `./flash.sh --erase` wipes everything, identity included: the server sees a
  new device (approve it, delete the old one).
- `./flash.sh --monitor-only` shows the serial log.

### Updates without the cable

After this flash the cable is no longer needed: the server can update the
firmware over the same WebSocket (`ota_begin`, binary `0x03` chunks with
their offset, `ota_end`; the device acknowledges every 64 KB and checks the
SHA-256). The new firmware goes to the other app slot; it is kept only once
it has reached the server (`welcome`), otherwise the next restart returns to
the previous one. The ring shows an amber progress arc during an update.

### Diagnostics without the cable

Every log line is kept in memory, and the last ones survive a crash, a
watchdog or a brownout reset. At each connection the Korvo sends them to the
server (`{"type":"log","reset_reason":..,"boot_count":..,"lines":[..]}`,
shown on the Devices page), with the summary of the last crash if there was
one; new warnings and errors follow as they happen. The setup page (hold SET)
shows the same log and the last reset reason; http://192.168.4.1/log has all
of it. `dist/korvo_satellite.elf` decodes the crash addresses:
`xtensa-esp32-elf-addr2line -pfiaC -e dist/korvo_satellite.elf 0x400d...`.

## The wake word

The Korvo has no enrollment of its own. Each time it connects it asks the
server for the recordings made on the ReSpeaker satellites
(`wake_templates_get`; one `wake_template` message per recording) and turns
them into the same templates as the satellites, with the same detector: MFCC
features and an online subsequence DTW, ported from the satellite engine
(`satellite/engine/src/kws.c`). The threshold is chosen the same way (from the
spread between your own repetitions), as are the rules around it: two
matching recordings needed when there are three or more, the match must cover
detected speech, a stricter limit while its own answer plays (0.44), a more
lenient one (+0.08) while music plays, 1.5 s between wakes. Wakes the server
rejects as not being the wake word are kept as negative examples.

The templates are saved in flash, so the wake word keeps working after a
reboot even if the server is down; they are refreshed at every connection. To
improve the wake word, record more samples on a ReSpeaker satellite (its web
page, "Wake word"): the Korvo picks them up at its next connection (power
cycle it to force one).

## LED ring

| Ring | Meaning |
|---|---|
| off (or dim breathing with `--led-idle-breathing`) | idle, ready |
| white flash from the top | wake word heard / button: listening starts |
| whole ring bright blue, breathing with your voice | listening |
| four orbs chasing each other | thinking |
| colours turning, pulsing with the sound | speaking |
| dim red | microphone muted |
| one amber spark circling | offline (no Wi-Fi or no server) |
| two blue dots turning | waiting for approval on the server |
| green glow | done (action without a spoken answer) |
| red blinks | error / nobody to talk to |
| arc from the top | volume (after VOL+ / VOL-) |
| white sweep around the ring | booting (the very first thing it does) |
| white chase over blue | Wi-Fi setup access point open |
| amber arc growing | firmware update in progress |
| warm pulsing | alarm / timer ringing |

## Buttons

| Key | Action |
|---|---|
| REC | talk: start a request; press again while it listens to end it; stops a ringing alarm |
| MODE | microphone mute on / off |
| PLAY | stop: music, answer or alarm; cancels a request in progress |
| VOL+ / VOL- | volume (hold to repeat) |
| SET (hold 3 s) | Wi-Fi setup page |

## Troubleshooting

- **`No ESP32 board found`**: wrong USB port (use USB-UART), charge-only cable,
  power switch off, or a missing driver (macOS / Windows). `./flash.sh --list-ports`.
- **Amber spark, never connects**: wrong Wi-Fi password or server URL. Watch
  `./flash.sh --monitor-only`; fix with `./flash.sh --config-only` or the setup
  page (hold SET). The server must be reachable on its gateway port (6000).
- **Two blue dots forever**: approve it on the Devices page.
- **It never wakes**: the log shows `templates: N of M usable` after each
  connection; with no recordings on the server there is no wake word (record
  some on a ReSpeaker satellite). The REC key always works.
- **It wakes by itself**: the server's verifier rejects most false wakes and
  the Korvo learns from them; recording a few more samples on a satellite
  tightens the automatic threshold.
- **Dark ring**: the white sweep at power-up comes before anything else; if
  even that is missing, check the FPC cable to the mic board, then run
  `./flash.sh --check-only` to read what the board says.
- **It keeps restarting**: the reset reason and the last lines before each
  restart are on the server's Devices page (or `./flash.sh --check-only`).
- **It hears itself / bad echo cancellation**: after its first connection it
  plays a soft chime to find which ES7210 channel carries the playback reference
  (log: `playback reference found on lane N`) and saves it; it re-checks on
  every loud playback.
- **No sound**: speaker on the speaker connector, nothing in the headphone
  jack, volume (VOL+).

## Building from source

```sh
./build.sh          # needs ESP-IDF v5.5 in ~/esp/esp-idf (or IDF_PATH); refreshes dist/
test/run_tests.sh   # host tests of the portable core (+ live tests against the server)
```

`build.sh` builds with at most 4 jobs inside a 6 GB memory cap (systemd-run)
and writes `dist/`: bootloader, partition table, `ota_data_initial.bin`, the
app (also the update image: its size and SHA-256 are in `manifest.json`
under `ota`), `korvo_satellite.elf` (symbols for crash addresses), and
`korvo-satellite-full.bin` (everything but the settings in one image, at
offset 0, for other flashing tools).

## How it works

```
ES7210 (3 mics + DAC loopback, 16 kHz) --I2S1--> feed task --[mic, ref]--> ESP-SR AFE
   AFE (AEC with the loopback reference, WebRTC noise suppression, VAD; no WakeNet)
   --> engine (16 ms hops): STFT -> MFCC -> DTW wake word, pre-roll ring (2.5 s),
       speech AGC, uplink 0x01 frames, state machine (satellite engine.c port)
server --WebSocket--> proto.c (satellite agent on_server) --> engine / mixer
mixer (jitter buffers in PSRAM, 24 kHz speech and 48 kHz music resampled to
       48 kHz, ducking -18 dB under speech / -40 dB while listening, earcons,
       EQ, compressor, limiter) --I2S0--> ES8311 --> amplifier --> speaker
```

The AEC reference is the DAC output looped back into the ES7210 on the board,
so it is exactly what the speaker plays, time-aligned with the microphones.

Real-time layout (1.2.0): core 1 runs the AFE (feed task) and the speaker
(play task, priority 22, above lwIP); core 0 runs Wi-Fi, lwIP, the WebSocket
and the engine (wake word, ~1.3 ms per 16 ms hop). The wake word features
(int8 with a per-frame scale, 17 KB) and the DTW state (5 KB) live in internal
RAM; the render and the DTW run from IRAM. Flash writes (wake word store,
settings, otadata) wait until nothing plays and nobody talks for 3 s, because
a flash write stops the cache on both cores. The DAC DMA holds 40 ms. Every
30 s (10 s while playing) the log gets `diag: cpu` (per task: core, % of one
core, free stack bytes) and `diag: heap` lines.

| Path | What |
|---|---|
| `components/satcore/` | portable core (also built on a PC for the tests): `kws.c` (detector), `fft.c`, `ns.c`, `resample.c`, `mixer.c`, `engine.c` (state machine, wake decision), `proto.c` (protocol), `templates.c` (server recordings -> templates -> flash), `anim.c` (LED animations), `ota.c` + `sha256.c` (update protocol) |
| `main/` | ESP32 side: `board.c` (codecs), `audio.c` (AFE, tasks, reference detection), `link.c` (WebSocket, backoff, pings), `wifi.c`, `portal.c` (setup page), `leds.c`, `buttons.c`, `config.c` (NVS), `logbuf.c` (log capture and report), `ota_esp.c` (update backend), `mem.c` (byte-addressable allocations), `port_esp.c` |
| `partitions.csv` | 4 MB layout (fits any module of 4 MB or more): `nvs` (identity: `device_id`, `token`; runtime state; never written by flash.py), `otadata`, two 1.6 MB app slots `ota_0` / `ota_1`, `cfg` (settings written by flash.py), `coredump` (last crash), `storage` (LittleFS, templates) |
| `flash.sh`, `flash.py` | flasher (esptool + NVS image generator in a private venv) |
| `test/` | host tests (`run_tests.sh`), `hostsat` + `ws_bridge.py` (the core against the real server), `esp32_bench/` (the detector on the ESP32 target under QEMU), `esp32_ota/` (the update written by the device backend and the rollback, under QEMU) |

Board pins come from Espressif's support for this board (esp-skainet
`esp32_korvo_v1_1_board.h` / `bsp_board.c`, its 2020 Korvo LED and button
code) and the board schematics; see `main/board.h`.

Settings in the `cfg` partition (namespace `korvo`): `wifi_ssid`,
`wifi_pass`, `server_url`, `name`, `room`, `owner`, `led_bright` (0-100),
`led_idle` (0/1), `mic_gain` (dB, 30), `dac_volume` (0-100, 80).
