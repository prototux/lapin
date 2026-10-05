#!/usr/bin/env python3
"""Flash the Korvo satellite firmware and its settings. No ESP-IDF needed.

    ./flash.sh                       # asks for Wi-Fi, server, name, room
    ./flash.sh --ssid Home --password secret --name Cuisine --room cuisine
    ./flash.sh --config-only         # only rewrite the settings
    ./flash.sh --erase               # wipe the whole flash first (new identity)
    ./flash.sh --monitor-only        # just watch the serial log
    ./flash.sh --check-only          # reset the board and summarize its boot

After flashing, the board is reset and its serial log read for 25 s: a short
summary says whether it booted, crashed, joined the Wi-Fi and reached the
server (raw log saved as boot-check-*.log next to this script).

The first run creates a private Python environment (.venv-flash, next to
this script) with esptool and esp-idf-nvs-partition-gen.
"""

import argparse
import getpass
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, "dist")
VENV = os.path.join(HERE, ".venv-flash")
REQUIREMENTS = ["esptool>=4.7,<5", "esp-idf-nvs-partition-gen>=0.1.2"]
DEFAULT_SERVER = "ws://assistant.local:8765/v1/device"
SAFE_BAUD = 460800
LAST = os.path.join(os.path.expanduser("~"), ".config", "korvo-flash", "last.json")
# USB-UART bridges found on ESP32 boards (the Korvo uses a Silicon Labs CP210x)
KNOWN_VIDS = {0x10C4: "Silicon Labs CP210x", 0x1A86: "WCH CH34x", 0x0403: "FTDI", 0x303A: "Espressif USB"}


def die(msg, code=1):
    print("\nerror: " + msg, file=sys.stderr)
    sys.exit(code)


# ------------------------------------------------------------------ venv

def venv_python():
    return os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")


def in_venv():
    return os.path.realpath(sys.prefix) == os.path.realpath(VENV)


def ensure_venv(argv):
    """Re-runs this script inside .venv-flash, creating it if needed."""
    if in_venv():
        return
    py = venv_python()
    stamp = os.path.join(VENV, ".korvo-requirements")
    want = "\n".join(REQUIREMENTS)
    if not os.path.exists(py) or not os.path.exists(stamp) or open(stamp).read() != want:
        print("Setting up the flashing tools in %s (first run only)..." % VENV)
        if not os.path.exists(py):
            r = subprocess.run([sys.executable, "-m", "venv", VENV])
            if r.returncode != 0 or not os.path.exists(py):
                die("could not create a Python virtual environment. On Debian/Ubuntu: sudo apt install python3-venv")
        r = subprocess.run([py, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"] + REQUIREMENTS)
        if r.returncode != 0:
            die("pip could not install %s (no internet?)" % ", ".join(REQUIREMENTS))
        with open(stamp, "w") as f:
            f.write(want)
    os.execv(py, [py, os.path.abspath(__file__)] + argv)


# ------------------------------------------------------------------ ports

def find_ports():
    from serial.tools import list_ports
    out = []
    for p in list_ports.comports():
        if p.vid in KNOWN_VIDS:
            out.append((p.device, "%s%s" % (KNOWN_VIDS[p.vid], ", " + p.description if p.description else "")))
    return out


def no_board_help():
    lines = ["No ESP32 board found on the USB serial ports."]
    lines.append("  - Plug the micro-USB cable into the Korvo's USB-UART port (next to the power port; the")
    lines.append("    power-only port has no data), with a data cable, and turn the power switch on.")
    if sys.platform.startswith("linux"):
        lines.append("  - Linux: the CP210x driver is built in; check that /dev/ttyUSB0 appears (ls /dev/ttyUSB*).")
    elif sys.platform == "darwin":
        lines.append("  - macOS: look for /dev/cu.usbserial-* or /dev/cu.SLAB_USBtoUART; recent macOS has the")
        lines.append("    driver built in, older ones need the Silicon Labs CP210x VCP driver.")
    else:
        lines.append("  - Windows: install the Silicon Labs CP210x VCP driver if no COM port shows up in the")
        lines.append("    Device Manager.")
    lines.append("  - Or give the port: --port /dev/ttyUSB0 (COM3 on Windows).")
    return "\n".join(lines)


def check_access(port):
    if sys.platform.startswith("linux") and os.path.exists(port) and not os.access(port, os.R_OK | os.W_OK):
        group = "uucp" if os.path.exists("/etc/arch-release") or shutil.which("pacman") else "dialout"
        die("no permission to open %s. Add yourself to the '%s' group, then log out and in again:\n"
            "  sudo usermod -aG %s $USER" % (port, group, group))


def pick_port(args):
    if args.port:
        check_access(args.port)
        return args.port
    ports = find_ports()
    if not ports:
        die(no_board_help(), 2)
    if len(ports) == 1:
        print("Board found on %s (%s)" % ports[0])
        check_access(ports[0][0])
        return ports[0][0]
    print("Several serial ports:")
    for i, (dev, desc) in enumerate(ports):
        print("  %d. %s  %s" % (i + 1, dev, desc))
    if args.yes or not sys.stdin.isatty():
        die("several boards connected: choose one with --port")
    while True:
        s = input("Which one? [1] ").strip() or "1"
        if s.isdigit() and 1 <= int(s) <= len(ports):
            check_access(ports[int(s) - 1][0])
            return ports[int(s) - 1][0]


# ------------------------------------------------------------------ settings

def load_last():
    try:
        with open(LAST) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_last(s):
    try:
        os.makedirs(os.path.dirname(LAST), exist_ok=True)
        with open(LAST, "w") as f:
            json.dump({k: v for k, v in s.items() if k != "wifi_pass"}, f, indent=2)
    except OSError:
        pass


def ask(prompt, default="", secret=False):
    if secret:
        v = getpass.getpass("%s%s: " % (prompt, " [keep]" if default else ""))
    else:
        v = input("%s%s: " % (prompt, " [%s]" % default if default else ""))
    return v if v else default


def gather_settings(args):
    last = load_last()
    s = {
        "wifi_ssid": args.ssid if args.ssid is not None else last.get("wifi_ssid", ""),
        "wifi_pass": args.password if args.password is not None else "",
        "server_url": args.server or last.get("server_url", DEFAULT_SERVER),
        "name": args.name if args.name is not None else last.get("name", "Korvo"),
        "room": args.room if args.room is not None else last.get("room", ""),
        "owner": args.owner if args.owner is not None else last.get("owner", ""),
        "led_bright": args.led_brightness if args.led_brightness is not None else last.get("led_bright", 60),
        "led_idle": int(args.led_idle_breathing) if args.led_idle_breathing else last.get("led_idle", 0),
    }
    interactive = sys.stdin.isatty() and not args.yes
    if interactive:
        print("\nSettings (Enter keeps the value in brackets):")
        if args.ssid is None:
            s["wifi_ssid"] = ask("  Wi-Fi network (SSID)", s["wifi_ssid"])
        if args.password is None:
            s["wifi_pass"] = ask("  Wi-Fi password (empty for an open network)", "", secret=True)
        if not args.server:
            s["server_url"] = ask("  Server URL", s["server_url"])
        if args.name is None:
            s["name"] = ask("  Device name", s["name"])
        if args.room is None:
            s["room"] = ask("  Room", s["room"])
    validate(s)
    return s


def validate(s):
    if not s["wifi_ssid"]:
        print("note: no Wi-Fi network given: the Korvo will open its setup access point (Korvo-Setup-XXXX).")
    if len(s["wifi_ssid"].encode()) > 32:
        die("the Wi-Fi network name is longer than 32 bytes")
    if s["wifi_pass"] and not 8 <= len(s["wifi_pass"].encode()) <= 63:
        die("a WPA password has 8 to 63 characters")
    if not (s["server_url"].startswith("ws://") or s["server_url"].startswith("wss://")):
        die("the server URL must start with ws:// or wss://, e.g. %s" % DEFAULT_SERVER)
    for k in ("wifi_ssid", "wifi_pass", "server_url", "name", "room", "owner"):
        if "\n" in s[k] or "\r" in s[k]:
            die("%s contains a line break" % k)
        if len(s[k].encode()) > 150:
            die("%s is too long" % k)
    if not 0 <= int(s["led_bright"]) <= 100:
        die("--led-brightness is 0 to 100")


def make_nvs(s, manifest, workdir):
    """The 'cfg' NVS partition image (namespace 'korvo') read by config.c."""
    import csv
    path_csv = os.path.join(workdir, "cfg.csv")
    path_bin = os.path.join(workdir, "cfg.bin")
    ns = manifest["cfg"]["namespace"]
    with open(path_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "type", "encoding", "value"])
        w.writerow([ns, "namespace", "", ""])
        for k in ("wifi_ssid", "wifi_pass", "server_url", "name", "room", "owner"):
            w.writerow([k, "data", "string", s[k]])
        w.writerow(["led_bright", "data", "u8", int(s["led_bright"])])
        w.writerow(["led_idle", "data", "u8", int(s["led_idle"])])
        w.writerow(["mic_gain", "data", "u8", 30])
        w.writerow(["dac_volume", "data", "u8", 80])
    r = subprocess.run([sys.executable, "-m", "esp_idf_nvs_partition_gen", "generate", path_csv, path_bin,
                        manifest["cfg"]["size"]], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(path_bin):
        die("could not generate the settings image:\n" + r.stdout + r.stderr)
    return path_bin


# ------------------------------------------------------------------ flashing

def load_manifest():
    path = os.path.join(DIST, "manifest.json")
    if not os.path.exists(path):
        die("dist/manifest.json is missing: run ./build.sh (needs ESP-IDF) or get the prebuilt dist/ folder")
    with open(path) as f:
        m = json.load(f)
    for img in m["images"]:
        p = os.path.join(DIST, img["file"])
        if not os.path.exists(p):
            die("dist/%s is missing" % img["file"])
        if hashlib.sha256(open(p, "rb").read()).hexdigest() != img["sha256"]:
            die("dist/%s does not match manifest.json (corrupted download?)" % img["file"])
    return m


def esptool(port, baud, *cmd, after="hard_reset", capture=False):
    full = [sys.executable, "-m", "esptool", "--chip", "esp32", "--port", port, "--baud", str(baud),
            "--before", "default_reset", "--after", after] + list(cmd)
    print("$ esptool " + " ".join(full[3:]))
    if capture:
        r = subprocess.run(full, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr
    return subprocess.run(full).returncode


def board_info(port, baud):
    """Chip, MAC and flash size, read with esptool (also checks the connection)."""
    import re
    rc, out = 1, ""
    for b in (baud, 115200):
        rc, out = esptool(port, b, "flash_id", after="no_reset", capture=True)
        if rc == 0:
            break
    if rc != 0:
        print(out[-1500:])
        die("cannot talk to the board. Put it in download mode by hand: hold the BOOT button, press and release\n"
            "RST, release BOOT, then run this again. Also try another USB cable or port.")
    info = {"chip": "", "mac": "", "flash_mb": 0}
    m = re.search(r"Chip is (.+)", out)
    info["chip"] = m.group(1).strip() if m else "ESP32"
    m = re.search(r"MAC: ([0-9a-f:]{17})", out)
    info["mac"] = m.group(1) if m else ""
    m = re.search(r"Detected flash size: (\d+)MB", out)
    info["flash_mb"] = int(m.group(1)) if m else 0
    return info


def flash(port, baud, pairs, erase=False, after="hard_reset"):
    def attempt(b):
        if erase and esptool(port, b, "erase_flash", after="no_reset") != 0:
            return 1
        args = ["write_flash", "--flash_mode", "keep", "--flash_freq", "keep", "--flash_size", "keep"]
        for off, path in pairs:
            args += [off, path]
        return esptool(port, b, *args, after=after)

    if attempt(baud) == 0:
        return
    if baud > 115200:
        print("\nRetrying at 115200 baud...")
        if attempt(115200) == 0:
            return
    die("flashing failed. If esptool could not connect, put the board in download mode by hand:\n"
        "  hold the BOOT button, press and release RST, release BOOT, then run this again.\n"
        "Also try another USB cable or port.")


def capture_boot(port, seconds, logpath):
    """Resets the board into the firmware and records its serial output."""
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.2
    s.dtr = s.rts = False
    try:
        s.open()
    except serial.SerialException as e:
        print("cannot open %s for the boot check: %s" % (port, e))
        return ""
    # EN low then high with IO0 high: a normal boot, from the first line
    s.rts = True
    time.sleep(0.12)
    s.rts = False
    data = bytearray()
    end = time.time() + seconds
    last_dot = 0
    try:
        while time.time() < end:
            data += s.read(4096)
            if time.time() - last_dot > 1:
                print(".", end="", flush=True)
                last_dot = time.time()
            text = data.decode("utf-8", "replace")
            # stop early once the outcome is clear
            if ("server: welcome" in text or "server refused" in text) and "templates:" in text:
                break
    except KeyboardInterrupt:
        pass
    finally:
        s.close()
    print()
    text = data.decode("utf-8", "replace")
    try:
        with open(logpath, "w") as f:
            f.write(text)
    except OSError:
        pass
    return text


def summarize(text, info, logpath):
    """Plain-language summary of the first seconds of the firmware."""
    import re
    lines = [l.rstrip("\r") for l in text.splitlines()]

    def find(pat):
        for l in lines:
            m = re.search(pat, l)
            if m:
                return m
        return None

    def last(pat):
        for l in reversed(lines):
            m = re.search(pat, l)
            if m:
                return m
        return None

    out = []
    ok = True
    if info.get("flash_mb"):
        out.append("Flash:      %d MB" % info["flash_mb"])
    boot = find(r"Korvo satellite (\S+)")
    psram = find(r"PSRAM: (\d+) KB")
    crash = find(r"Guru Meditation Error: (.*)") or find(r"(abort\(\) was called.*)") or \
        find(r"(Brownout detector was triggered)") or find(r"(Task watchdog got triggered.*)") or \
        find(r"(assert failed.*)")
    if not text.strip():
        out.append("Booted:     NO OUTPUT AT ALL on the serial port (power? cable? wrong port?)")
        ok = False
    elif not boot:
        ok = False
        out.append("Booted:     NO, the firmware did not start")
        if crash:
            out.append("            crash: " + crash.group(1).strip())
        rst = last(r"rst:(0x[0-9a-f]+ \([A-Z_]+\))")
        if rst:
            out.append("            last reset: " + rst.group(1))
    else:
        boots = sum(1 for l in lines if re.search(r"Korvo satellite \d", l))
        out.append("Booted:     yes, firmware %s%s" % (boot.group(1), "" if boots < 2 else
                                                       " (but it RESTARTED %d times)" % (boots - 1)))
        if crash:
            ok = False
            out.append("Crash:      " + crash.group(1).strip())
            bt = find(r"Backtrace:(.*)")
            if bt:
                out.append("            backtrace:" + bt.group(1)[:200])
    if psram:
        kb = int(psram.group(1))
        out.append("PSRAM:      %s" % ("%d MB" % (kb // 1024) if kb else "none (degraded mode: no earcons, short buffers)"))
    codecs = find(r"codecs ready") or find(r"(ES7210 .*not found.*|ES8311 .*not found.*|audio codecs failed.*)")
    if codecs:
        out.append("Audio:      " + ("codecs OK" if "ready" in codecs.group(0) else codecs.group(1)))
    ip = last(r'connected to "(.*)", ip (\d+\.\d+\.\d+\.\d+)')
    join_fail = last(r'cannot join "(.*)": reason (\d+) ?(.*)')
    if ip:
        out.append("Wi-Fi:      joined \"%s\", IP %s" % (ip.group(1), ip.group(2)))
    elif find(r"no Wi-Fi configured"):
        out.append("Wi-Fi:      no network configured: setup access point open (Korvo-Setup-XXXX)")
        ok = False
    elif join_fail:
        out.append("Wi-Fi:      cannot join \"%s\" (reason %s %s)" % (join_fail.group(1), join_fail.group(2),
                                                                     join_fail.group(3).strip()))
        ok = False
    elif boot:
        out.append("Wi-Fi:      not joined yet (no answer within the check time)")
        ok = False
    if find(r"setup access point"):
        out.append("            the setup access point is open (Korvo-Setup-XXXX, http://192.168.4.1)")
    if find(r"server: welcome"):
        out.append("Server:     connected and approved")
        tpl = last(r"templates: (\d+) of (\d+) usable")
        if tpl:
            out.append("Wake word:  %s of %s recordings usable" % (tpl.group(1), tpl.group(2)))
    elif find(r"waiting for approval"):
        dev = find(r"approve (korvo-[0-9a-f]+)")
        out.append("Server:     reached, WAITING FOR APPROVAL: approve %s on the server's Devices page" %
                   (dev.group(1) if dev else "the new korvo-... device"))
    elif find(r"server refused the device: (.*)"):
        out.append("Server:     refused: " + find(r"server refused the device: (.*)").group(1))
        ok = False
    elif find(r"link: connected to"):
        out.append("Server:     socket open, no answer yet")
    elif ip:
        err = find(r"E \(\d+\) (transport_base|esp-tls)[^:]*: (.*)") or \
            find(r"E \(\d+\) (websocket_client)[^:]*: (.*)")
        out.append("Server:     NOT reached%s" % (": " + err.group(2) if err else " (check the server URL / port)"))
        ok = False
    errors = [l for l in lines if re.match(r"E \(\d+\)", l) and "Corrupted dir pair" not in l]
    if errors:
        out.append("Errors:     %d logged, the last ones:" % len(errors))
        for l in errors[-4:]:
            out.append("            " + l[:150])
    if not ok and lines:
        out.append("Last lines:")
        for l in [l for l in lines if l.strip()][-8:]:
            out.append("            " + l[:150])
    print("\n--- Boot check " + "-" * 50)
    print("\n".join(out))
    print("Raw log:    %s" % logpath)
    print("-" * 65)
    return ok


def monitor(port):
    import serial
    print("\nSerial log of %s (115200 baud). Ctrl+C to quit.\n" % port)
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.2
    s.dtr = s.rts = False       # do not hold the chip in reset / download mode
    try:
        s.open()
    except serial.SerialException as e:
        die("cannot open %s: %s" % (port, e))
    try:
        while True:
            data = s.read(512)
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        print()
    finally:
        s.close()


def main(argv):
    ap = argparse.ArgumentParser(description="Flash the Korvo satellite (prebuilt images in dist/).",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--port", help="serial port (default: detected)")
    ap.add_argument("--baud", type=int, default=SAFE_BAUD, help="flashing speed (default %d)" % SAFE_BAUD)
    ap.add_argument("--ssid", help="Wi-Fi network")
    ap.add_argument("--password", help="Wi-Fi password")
    ap.add_argument("--server", help="server URL (default %s)" % DEFAULT_SERVER)
    ap.add_argument("--name", help="device name shown on the server")
    ap.add_argument("--room", help="room")
    ap.add_argument("--owner", help="household user this device belongs to (optional)")
    ap.add_argument("--led-brightness", type=int, help="LED ring brightness 0-100 (default 60)")
    ap.add_argument("--led-idle-breathing", action="store_true", help="dim breathing light when idle")
    ap.add_argument("--config-only", action="store_true", help="only rewrite the settings, not the firmware")
    ap.add_argument("--erase", action="store_true", help="erase the whole flash first (the device gets a new "
                    "identity and must be approved again)")
    ap.add_argument("--monitor", action="store_true", help="show the serial log after flashing")
    ap.add_argument("--monitor-only", action="store_true", help="only show the serial log")
    ap.add_argument("--nvs-out", metavar="FILE", help="only write the settings image to FILE (no board needed)")
    ap.add_argument("--list-ports", action="store_true", help="list the detected boards")
    ap.add_argument("--no-check", action="store_true", help="skip the boot check after flashing")
    ap.add_argument("--check-seconds", type=int, default=25, help="length of the boot check (default 25)")
    ap.add_argument("--check-only", action="store_true", help="only reset the board and run the boot check")
    ap.add_argument("-y", "--yes", action="store_true", help="no questions (use options and saved values)")
    args = ap.parse_args(argv)

    if args.list_ports:
        ports = find_ports()
        for dev, desc in ports:
            print("%s  %s" % (dev, desc))
        if not ports:
            print(no_board_help())
        return
    if args.monitor_only:
        monitor(pick_port(args))
        return
    if args.check_only:
        port = pick_port(args)
        logpath = os.path.join(HERE, "boot-check-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
        print("Resetting the board and reading its log for %d s" % args.check_seconds, end="")
        summarize(capture_boot(port, args.check_seconds, logpath), {}, logpath)
        return
    if args.config_only and args.erase:
        die("--config-only and --erase do not go together")

    manifest = load_manifest()
    print("Korvo satellite firmware %s (built %s)" % (manifest["version"], manifest["built"]))
    port = None if args.nvs_out else pick_port(args)
    info = {}
    if port:
        info = board_info(port, args.baud)
        mac = info["mac"].replace(":", "")
        print("Board: %s, flash %s, device id korvo-%s" % (info["chip"], "%d MB" % info["flash_mb"]
                                                          if info["flash_mb"] else "size unknown", mac))
        need = manifest.get("min_flash_mb", 4)
        if info["flash_mb"] and info["flash_mb"] < need:
            die("this module has %d MB of flash; the firmware needs %d MB" % (info["flash_mb"], need))
    settings = gather_settings(args)
    work = tempfile.mkdtemp(prefix="korvo-")
    try:
        cfg_bin = make_nvs(settings, manifest, work)
        if args.nvs_out:
            shutil.copy(cfg_bin, args.nvs_out)
            print("settings image written to %s (%d bytes, flash it at %s)" %
                  (args.nvs_out, os.path.getsize(args.nvs_out), manifest["cfg"]["offset"]))
            save_last(settings)
            return
        pairs = [] if args.config_only else [(i["offset"], os.path.join(DIST, i["file"])) for i in manifest["images"]]
        pairs.append((manifest["cfg"]["offset"], cfg_bin))
        if args.erase:
            print("\n--erase: the device identity (id and token) is wiped; the server will see a new device "
                  "(approve it again, and delete the old one on the Devices page).")
        flash(port, args.baud, pairs, erase=args.erase, after="hard_reset" if args.no_check else "no_reset")
        save_last(settings)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not args.no_check:
        logpath = os.path.join(HERE, "boot-check-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
        print("\nChecking the boot: reading the board's log for up to %d s" % args.check_seconds, end="")
        summarize(capture_boot(port, args.check_seconds, logpath), info, logpath)
    print("\nDone. The Korvo restarts%s." % ("" if args.config_only else " with the new firmware"))
    if not args.config_only:
        print("If it is new to the server, approve it on the Devices page (http://<server>:8090), then say "
              "the wake word.")
    if args.monitor:
        time.sleep(0.5)
        monitor(port)


if __name__ == "__main__":
    ensure_venv(sys.argv[1:])
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        sys.exit(130)
