"""User button (gpio-keys, BTN_0) through evdev, without dependencies."""

import glob
import logging
import os
import struct
import threading
import time

log = logging.getLogger("button")

EV_KEY = 1
EVENT = struct.Struct("llHHi")      # struct input_event: 16 bytes on armhf, 24 on 64-bit


def find_device(name="gpio-keys"):
    for path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        try:
            with open(path) as f:
                if f.read().strip() == name:
                    return "/dev/input/" + path.split("/")[4]
        except OSError:
            pass
    return None


class Button:
    """Calls on_press("short" | "long") on release; "long" fires as soon as
    the button has been held long enough."""

    def __init__(self, on_press, long_ms=1500, name="gpio-keys"):
        self.on_press = on_press
        self.long_ms = long_ms
        self.name = name
        self.device = None
        self.error = None
        threading.Thread(target=self._run, name="button", daemon=True).start()

    def _run(self):
        while True:
            self.device = find_device(self.name)
            if not self.device:
                self.error = "no %s input device" % self.name
                return
            try:
                fd = os.open(self.device, os.O_RDONLY)
            except OSError as e:
                self.error = "%s: %s (is the user in the 'input' group?)" % (self.device, e.strerror)
                log.warning(self.error)
                time.sleep(30)
                continue
            self.error = None
            log.info("button on %s", self.device)
            pressed_at = None
            fired = False
            timer = None
            try:
                while True:
                    data = os.read(fd, EVENT.size)
                    if len(data) < EVENT.size:
                        break
                    _, _, typ, code, value = EVENT.unpack(data)
                    if typ != EV_KEY:
                        continue
                    if value == 1:
                        pressed_at = time.monotonic()
                        fired = False

                        def long_fire(t0=pressed_at):
                            nonlocal fired
                            if pressed_at == t0 and not fired:
                                fired = True
                                self.on_press("long")
                        timer = threading.Timer(self.long_ms / 1000, long_fire)
                        timer.daemon = True
                        timer.start()
                    elif value == 0 and pressed_at is not None:
                        if timer:
                            timer.cancel()
                        if not fired:
                            self.on_press("short")
                        pressed_at = None
            except OSError as e:
                log.warning("button read failed: %s", e)
            finally:
                os.close(fd)
            time.sleep(2)
