# Lapin desktop

The desktop satellite of the Lapin voice assistant, for Linux, Windows and macOS.
It's a native Qt app (Python + PySide6), not a web view.

Press the shortcut (default **Ctrl+Alt+Space**) and a small card opens at the bottom
of the screen. It listens, shows what it heard and the answer, and speaks the answer.
You can also type a request in the card.

Speech recognition, the language model and the voices run on the assistant server.
This app is a client of its device protocol (`docs/PROTOCOL.md`). It stays connected
while it runs, and offers computer actions ("device tools") that the server's model can call:

| tool | what it does | notes |
|---|---|---|
| `open_app(app)` | starts an installed app by name (French or English, fuzzy) | silent; Linux: .desktop files (XDG + flatpak/snap), Windows: Start menu (`Get-StartApps`, .lnk fallback), macOS: `/Applications` bundles |
| `open_url(url)` | opens a link in the default browser | silent; http(s), mailto, tel... (no `file:` or `javascript:`) |
| `open_folder(name)` | Documents, Downloads, Pictures, Music, Videos, Desktop, home, or a folder in the home | silent |
| `computer_media(action)` | play / pause / next / previous for the computer's media player | silent; Linux: MPRIS over D-Bus (or `playerctl`), Windows: media keys, macOS: Music/Spotify via `osascript` |
| `computer_volume(level, change, mute)` | system volume | silent; Linux: `wpctl` or `pactl`, Windows: volume keys (2 % steps), macOS: `osascript` |
| `lock_screen()` | locks the session | silent |
| `screenshot()` | saves a PNG in Pictures | Wayland: `grim`, `gnome-screenshot` or `spectacle` |
| `copy_to_clipboard(text)` / `read_clipboard()` | clipboard | the read is truncated to 2000 characters |
| `power(action)` | shutdown / reboot / suspend / logout | **asks for confirmation** (Yes/No in the card, or by voice) |
| `system_info()` | battery, uptime, free disk and memory, OS | |

The server also sends `set` (`volume`), which sets the app's own output volume.

## Install

Each [release](https://github.com/prototux/lapin/releases/latest) has a
package for each system. They are not signed by a known publisher, so the
system warns you the first time.

- **Windows:** `lapin-desktop-<version>-windows-x64-setup.exe` installs it
  for your user only, with no admin rights (SmartScreen: **More info → Run
  anyway**). The `.zip` is the same app without an installer.
- **macOS** (Apple silicon): open `lapin-desktop-<version>-macos-arm64.dmg`
  and drag Lapin to Applications. The app is not notarized: the first time,
  macOS refuses to open it. Go to **System Settings → Privacy & Security**,
  click **Open Anyway** next to the message about Lapin, or run
  `xattr -dr com.apple.quarantine /Applications/Lapin.app`. It asks for the
  microphone, then **Accessibility** and **Input Monitoring** for the
  shortcut, for Lapin itself. After an update, macOS may ask again.
- **Linux:** the Flatpak, `lapin-desktop-<version>-linux-x86_64.flatpak`:

  ```sh
  flatpak install --user ./lapin-desktop-1.3.0-linux-x86_64.flatpak
  flatpak run net.prototux.Lapin
  ```

  It fetches the KDE runtime from Flathub the first time. The computer
  actions run their commands on your system with `flatpak-spawn --host`, so
  the Flatpak has no real sandbox. On Wayland, bind
  `flatpak run net.prototux.Lapin --activate` to the shortcut (see
  [Global shortcut](#global-shortcut)).

### From the sources

Put the `desktop` folder wherever you like (and rename it if you want): the
launchers find their own folder. The first run creates `.venv` inside it and
installs the dependencies from `requirements.txt`; later runs start at once.
Moving the folder later is fine, `.venv` included. Python 3.10 or newer.

#### Linux

```sh
# PortAudio for sounddevice, and the xcb cursor library Qt needs on X11:
sudo apt install libportaudio2 libxcb-cursor0        # Debian/Ubuntu
sudo pacman -S portaudio xcb-util-cursor             # Arch/Manjaro
sudo dnf install portaudio xcb-util-cursor           # Fedora

/path/to/desktop/lapin
```

Optional tools that it uses when they're there: `playerctl`, `wpctl` (PipeWire) or
`pactl`, `gio` or `gtk-launch`, and `grim` / `gnome-screenshot` / `spectacle` for
screenshots on Wayland.

#### Windows

Install Python from python.org (tick "Add to PATH"), then double-click
`lapin.cmd` (or run it from a terminal). Without arguments it starts without a
console window; with arguments (`lapin.cmd --settings`) it keeps the console.

#### macOS

```sh
brew install python portaudio
/path/to/desktop/lapin
```

macOS asks for some permissions for the app that runs Python (Terminal, or Python itself):

- **Microphone**, the first time you talk.
- **Accessibility** and **Input Monitoring**, for the global shortcut (pynput).
- **Automation**, the first time it controls Music, Spotify or System Events (`power`).

## First start and pairing

Open **Settings** from the tray icon (or run `lapin --settings`) and fill in:

- **Server URL**: default `ws://assistant.local:8765/v1/device`.
- **Device name**: defaults to the host name.
- **Owner**: the household user this computer belongs to (e.g. `alice`, lower case,
  as on the People page). The server binds the device to that person on the first
  pairing, so requests use their memory, accounts and language. Change it later on
  the server's Devices page.
- **Shortcut**, **Start at login**, **Speak answers to typed requests**, **Language**.

The status line shows "Waiting for approval on the server's Devices page" until you
approve the device there. After that it connects by itself.

Settings live in `config.json` in the platform's config folder:

- Linux: `~/.config/lapin-desktop/`
- Windows: `%LOCALAPPDATA%\lapin-desktop\`
- macOS: `~/Library/Preferences/lapin-desktop/`

Override it with `--config-dir DIR` or `LAPIN_CONFIG_DIR`. The file also holds the
device id (`desktop-<uuid>`) and its secret token, both generated once. Delete the
device on the server to pair again with a new id.

## Using it

- **Shortcut / tray icon click**: opens the card and listens. The server detects the
  end of your sentence by itself. Press again while it listens to stop right away.
  Press while it answers to interrupt it and speak again.
- **Typing**: type in the field and press Enter. Typing while it listens cancels the
  voice turn.
- **Yes / No** buttons appear when an action needs confirmation. Answering by voice works too.
- **Esc**, the ✕ button or clicking elsewhere closes the card and stops the answer.
  Clicking elsewhere is ignored for a few seconds after a tool ran, so a window that
  `open_app` just opened doesn't close the card.
- The card closes by itself a few seconds after the answer. Music the assistant plays
  on this computer keeps playing; use the tray's **Stop audio** or say "stop".

The card follows the system's light/dark theme. The UI is in French or English,
depending on the system locale (or the Language setting).

## Global shortcut

On **X11, Windows and macOS** the app grabs the shortcut itself. Change it in
Settings: any key with or without modifiers, e.g. the **Menu key** (right of the
space bar, between AltGr and Ctrl; there's a button for it, since pressing it in
the capture field opens a context menu instead).

- **X11**: an exclusive grab, like the desktop's own shortcuts: the key only
  reaches Lapin (the Menu key no longer opens context menus), with NumLock or
  CapsLock on or off. If another program already holds the shortcut, Settings
  says so.
- **Windows**: a single-key shortcut such as the Menu key is swallowed too.
- **macOS**: through pynput (Macs have no Menu key).

On **Wayland**, apps can't grab global shortcuts. The app is single-instance, so
running it again with `--activate` makes the running copy listen. Bind that command
to a shortcut in your desktop's settings, with the full path of the launcher, e.g.
`/path/to/desktop/lapin --activate`. Settings shows the exact command for your copy.

- **GNOME**: Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts → **+**.
  Name it "Lapin", paste the command, set Ctrl+Alt+Space. Or from a terminal:

  ```sh
  # replaces the list of custom shortcuts: add the existing ones to the list if you have some
  K=/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/lapin/
  gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['$K']"
  gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K name 'Lapin'
  gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K command \
      '/path/to/desktop/lapin --activate'
  gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$K binding '<Control><Alt>space'
  ```

- **KDE Plasma**: System Settings → Keyboard → Shortcuts → **Add New** → *Command or Script*.
  Paste the command and set the shortcut.
- **Sway**: `bindsym Ctrl+Mod1+space exec /path/to/desktop/lapin --activate`
- **Hyprland**: `bind = CTRL ALT, space, exec, /path/to/desktop/lapin --activate`

Other commands for the running instance: `--settings` and `--quit`.

## Start at login

The **Start at login** setting creates:

- Linux: `~/.config/autostart/lapin-desktop.desktop` (XDG autostart).
- Windows: the `Lapin` value under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.
- macOS: `~/Library/LaunchAgents/net.lapin.desktop.plist`.

Each one runs the launcher (`lapin`, `lapin.cmd`), or the installed app:
`Lapin.exe`, `Lapin.app`, `flatpak run net.prototux.Lapin`. After moving the
folder, toggle the setting again.

## Command line

```
lapin                      tray icon + overlay (or signal the running instance)
lapin --activate           talk (starts the app if it isn't running)
lapin --settings | --quit
lapin --headless           no overlay or tray: hotkey / --activate only (works with QT_QPA_PLATFORM=offscreen)

# debug: one turn, prints the transcript, reply, tool calls and confirmations, then exits
lapin --text "Quelle heure est-il ?" [--speak yes|no] [--confirm yes|no] [--no-audio]
lapin --wav question.wav            # a 16-bit WAV used as the microphone of a voice turn
```

With `--confirm`, the CLI answers a confirmation the way the Yes/No buttons would
(default **no**). `--no-audio` plays nothing and only counts the audio streams.

`LAPIN_DRY_RUN=1` makes the tools work out what they would do and report it, without
doing it: the app and command they would launch, the volume they would set, the
power command.

## Tests

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -t .
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/snapshots.py /tmp/snapshots    # PNGs of the overlay in each state
```

The unit tests cover:

- the resampler, the mixer (prebuffer, drain, ducking, barge-in, volume) and the earcons;
- the protocol state machine (with a fake link);
- app matching (with a fake XDG apps folder), folders, URLs, the clipboard, screenshots;
- MPRIS (with a fake player on the session bus);
- shortcut parsing, autostart, config and strings.

The tools run in dry-run mode there.

## How it works

- `link.py`: the WebSocket client, in a thread (sync `websockets`). It sends `hello`
  with `kind: "desktop"`, `confirm_ui: true` and the tools. It reconnects with a
  backoff of 1, 2, 5, then 10 s, and stays connected while waiting for approval.
- `core.py`: the turn state machine (wake, eot, cancel, transcript, reply, streams,
  session_end and follow-ups, confirm, tool_call, set, listen, stop). Tools run in a
  worker pool and answer with `tool_result`.
- `audio.py`:
  - microphone at 16 kHz mono, in 20 ms frames (the device is opened at its native
    rate and resampled when it can't do 16 kHz);
  - one output stream with a mixer: a buffer per stream, resampling to the output
    rate, 150 ms prebuffer, `stream_close` drain;
  - music ducked by 18 dB under speech and by 40 dB while listening;
  - earcons synthesized with numpy.
- `overlay.py` (the card), `app.py` (tray, wiring), `settings.py`, `hotkey.py`,
  `single.py` (QLocalServer), `autostart.py`, `tools/`.

## Known limitations

- Wayland: no global shortcut grab (use `--activate`, see above). Focus may not move
  to the card, so Esc and clicking elsewhere only work once you click into it.
  Qt can't take screenshots there, so an external tool is needed.
- Windows volume uses the volume keys: an exact level is approximate (2 % steps).
  Media keys act on whichever app Windows routes them to.
- macOS media control only drives Music and Spotify.
- No local wake word: the shortcut, the tray icon or `--activate` start a turn.
- The microphone has no echo cancellation. Interrupting is done with the shortcut,
  not by talking over the answer.
