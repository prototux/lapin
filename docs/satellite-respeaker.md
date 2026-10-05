# ReSpeaker satellite

A room speaker built on a **Seeed ReSpeaker Core v2**: a small ARM board
with a 6-microphone circular array, a ring of 12 LEDs, a button and a
speaker output. It hears the wake word from across the room, even while
music plays, and streams your request to the server.

It runs two programs:

- **`satd`** (`satellite/engine`, C): the real-time audio engine.
  - Sound processing: echo cancellation, direction finding, beamforming,
    noise suppression and voice detection.
  - The wake word.
  - The playback mixer, for answers, music, alarms and earcons.
  - It costs about 5 ms of CPU per 16 ms of audio. The signal path is
    described in [algorithms.md](algorithms.md).
- **`satagent`** (`satellite/agent`, Python): the link to the server.
  - The local web page on port 8080.
  - The LED ring plugin and the button.

## Installing

1. Flash the board with Seeed's **Debian** image, connect it to your
   network (Ethernet or Wi-Fi) and check you can `ssh respeaker@<board-ip>`.
   The default password on Seeed's image is `respeaker`. Change it.
2. From your computer, in the repository:

   ```sh
   cd satellite
   ./deploy.sh respeaker@<board-ip> ws://<server>:8765/v1/device
   ```

   The script copies the sources and runs `install.sh` on the board with
   sudo. It asks for the password; set `SUDO_PASS` or use `SSH_OPTS="-i key"`
   to automate it. `install.sh` then:
   - installs the build packages;
   - compiles `satd` on the board;
   - installs the agent in `/opt/satellite`;
   - sets the server URL;
   - enables the two systemd services.

   You can also copy the `satellite` folder to the board yourself and run
   `sudo ./install.sh ws://<server>:8765/v1/device` there.
3. On the server, approve the new satellite on **Devices**, then give it a
   name and a room.
4. Open `http://<board-ip>:8080` and enroll the wake word (next section).

Run `./deploy.sh` again to update the board. Your settings, kept in
`/var/lib/satellite/config.json`, are preserved.

## The wake word

The wake word detector compares what it hears with a few recordings of your
voice (MFCC features and dynamic time warping). It needs no training and
works with any short phrase.

1. On the satellite's page, tab **Wake word**, click **Record a sample** and
   say the phrase right after the beep. Record 3 to 5 samples, from where
   you usually stand, at a normal voice.
2. Click **Set the threshold from my samples**. The *live best match* bar
   shows how close each try comes; your phrase should cross the line.
3. Each household member can add their own samples. More samples beat a
   higher threshold.

The samples are also uploaded to the server, which:

- learns how the recognizer spells your phrase;
- shares the samples with the Korvo satellites.

Wakes that the server rejects are kept on the board as negative examples,
so the same false trigger (a TV line, for instance) becomes less likely.

The threshold adapts to what is playing:

- **Answers:** it is capped at 0.44 while the satellite plays its own
  answer, so the satellite doesn't wake itself.
- **Music:** slightly more lenient (+0.08), to make up for the echo left
  after cancellation.

## Using it

- **Wake word**, then your request. The ring turns toward you while it
  listens, and spins while it thinks.
- **Button:**
  - **short press:** stops what is playing or the alarm; while listening, it
    cancels; otherwise it starts a request without the wake word.
  - **long press** (1.5 s): mutes or unmutes the microphones. The ring shows
    the muted state.
- **Saying the wake word while it answers or plays music** interrupts it
  (barge-in).

## The web page

`http://<board-ip>:8080`:

| Tab | What |
|---|---|
| **Audio** | volume, mute, echo cancellation, beamforming mode, noise suppression, AGC, equalizer and limiter, earcons |
| **Wake word** | enrollment, threshold, live match meter |
| **Plugins** | the LED ring: brightness, palette, idle animation, orientation |
| **Device** | name, room, server URL, pairing |
| **System** | services, CPU and memory, logs, restart |

The page has no password by default. Set one on the **Device** tab, or as
`webui.password` in `/var/lib/satellite/config.json`.

## Tuning

The defaults suit a ReSpeaker on a shelf in a normal living room.

The wake word *Threshold* is a maximum distance to your samples: **higher
is more sensitive**.

- **It misses the wake word:** record more samples from where it misses. If
  that isn't enough, raise *Threshold* a little (by 0.02 at a time).
- **It wakes up on its own:** lower *Threshold*. Look at the server's
  **Conversations** page: false wakes appear there as rejected.
- **It misses the wake word over loud music:** that is the hardest case.
  Lower the music volume, or raise *More lenient while music plays*
  (`kws_playback_margin`, Audio tab).
- **It cuts you off, or keeps listening too long:** that is decided on the
  server (**Settings → Turn taking**). The satellite's *End of speech after*
  setting (`eos_silence_ms`, 1.2 s) only shortens it.
- **Its own voice comes back in the recording:** keep echo cancellation
  and *Residual echo suppression* on, and don't use an external amplifier
  with its own volume knob between the board and the speaker. The echo
  reference is taken before the amplifier.

## Troubleshooting

```sh
ssh respeaker@<board-ip>
systemctl status satellite-engine satellite-agent
journalctl -u satellite-engine -u satellite-agent -f
```

- **The page works but the server doesn't list the satellite:** check the
  server URL on the **Device** tab, and that port 8765 on the server can be
  reached from the board.
- **No sound:** check the volume and mute on the **Audio** tab, and run
  `aplay -l` on the board.
- **The LEDs stay dark:** the LED plugin needs SPI. `install.sh` adds the
  user to the `spi` and `gpio` groups; reboot once after the first install.
