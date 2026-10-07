# Android app

The Lapin app turns a phone into a personal satellite.

- **Assistant key:** press it, or long-press Home or Power, depending on the
  phone, and a small card rises from the bottom of the screen.
- **Listening:** it listens, shows what it heard and the answer, and speaks
  the answer.
- **Typing:** you can type a request instead. Typed requests get a text-only
  answer, because if you're typing you probably don't want noise.

The same app works on **Android TV**, with a big talk button, and appears
in **Android Auto**.

It is written in Kotlin with Jetpack Compose: package `net.prototux.lapin`,
minimum Android 8.0 (API 26).

## Installing

Download `lapin-android-<version>.apk` from the
[releases page](https://github.com/prototux/lapin/releases/latest) and open
it on the phone (allow installing apps from your browser or file manager
when asked), or from a computer:

```sh
adb install -r lapin-android-1.3.0.apk
```

Later releases install over it and keep the settings and the pairing. A
release built without the project's signing key has a name ending in
`-debug.apk`: it can't update an APK signed with another key, so uninstall
the old one first (you then pair the phone again).

### Building it yourself

```sh
cd android
./build.sh            # needs a JDK 17-21; finds one or uses JAVA_HOME
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

It also needs the Android SDK (platform 37 and build tools): set
`ANDROID_HOME`, or put `sdk.dir=/path/to/sdk` in `android/local.properties`.
`build.sh` runs Gradle inside a 6 GB memory cap when systemd is available.
You can also open the `android` folder in Android Studio.

## Setting it up

The app opens on its setup screen:

1. **Server address:** `ws://<server>:8765/v1/device`.
2. **Device name**, and the **owner**: the household user this phone belongs
   to, as on the server's People page, for example `alice`. The phone is
   bound to that person for good on the first pairing. Its requests use their
   memories, music accounts and messaging links.
3. **Save and connect**, then approve the phone on the server's **Devices**
   page. Use **Try the assistant** to check.
4. **Permissions:**
   - **Microphone:** required.
   - **Contacts, SMS, phone, location:** each one enables its phone action.
   - **Do Not Disturb access** and **Notification access:** optional.
     Notification access lets Lapin see and control what any app is playing;
     it doesn't read your notifications.
5. **Default assistant:** tap *Open assistant settings* and choose Lapin as
   the *Digital assistant app*. Then the assistant key and the long press
   open Lapin.

## What it can do on the phone

When connected, the phone offers these **device tools** to the assistant.
They are only used for requests made *from this phone*.

| Tool | Example | Notes |
|---|---|---|
| `send_phone_message` | "envoie un SMS à Alice : j'arrive" | **asks first**. SMS is sent directly. Signal opens with the message ready and you tap send |
| `call_contact` | "call Bob" | **asks first** |
| `find_contact` | "what's Alice's number?" | fuzzy search in the address book |
| `start_navigation` | "emmène-moi à la gare" | Google Maps, Waze, Organic Maps… |
| `open_app` | "open Spotify" | fuzzy app name, French or English |
| `open_url` | "open wikipedia.org" | |
| `set_phone_alarm` / `set_phone_timer` | "wake me up at 6:30", "timer 5 minutes" | in the phone's clock app, so it rings even offline |
| `phone_play_music` | "play Daft Punk" | in your own music app (Tempus or any media3 app), from your Navidrome library; not streamed by the server |
| `phone_media` / `phone_now_playing` | "next", "pause", "what's playing?" | controls the app actually playing |
| `flashlight`, `do_not_disturb` | "allume la lampe torche" | |
| `get_location` | "where am I?" | coordinates and street address |
| `battery_status` | "how much battery do I have?" | |
| `create_calendar_event` | "add dentist Tuesday at 2 pm" | opens the calendar filled in; you tap save |

Confirmation appears as Yes/No buttons on the card. You can also answer by
voice.

### Voice detection on a phone

The microphone uses the voice-communication input, with the phone's
automatic gain off. The server applies *near-field gating*: it learns how
loud you are in the first moments of the request. Voices clearly quieter
than yours (a TV, a subway announcement, a colleague at the next desk) don't
count as speech, and don't keep the turn open.

## Android TV

The same APK installs on Android TV, where it appears in the launcher.

- **To talk:** press OK on the talk screen, or the search key on the remote.
- **TV tools:** `open_app` and `open_url`, and media control of the app
  playing.

## Android Auto

Lapin appears in the car's app list. Press **Talk** and ask. The steering
wheel voice button stays with Google Assistant: Android doesn't let other
apps take it.

- **Sideloaded app:** Android Auto ignores it until you enable *Unknown
  sources* in Android Auto's developer settings.
- **Version:** it needs car app API level 5.

## Troubleshooting

- **"Waiting for approval":** approve the phone on the server's Devices page.
- **"Can't reach the server":** the phone must be on the same network as
  the server, or reach it through a VPN. There is no TLS, so don't open the
  gateway to the internet.
- **The assistant key opens Google instead:** Lapin is not the default
  digital assistant app. See step 5 above.
- **"Play …" does nothing:** install a media3 music app (for example Tempus)
  and pick it in the app's settings, under *Music app*.
