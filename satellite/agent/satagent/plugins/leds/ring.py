"""APA102 LED ring of the ReSpeaker Core v2: SPI output and power switch.

    LEDs        /dev/spidev0.1 (APA102, BGR, 5-bit global brightness)
    LED power   GPIO line "LED_PWR_N" (active low)
"""

import glob
import math
import os
import re
import shutil
import subprocess
import time

N = 12
GAMMA = 2.2


def encode_apa102(frame, brightness):
    """frame: N (r, g, b) floats 0..1 (perceptual). Uses the 5-bit per-LED
    current as extra dynamic range so dim colors keep 8-bit resolution."""
    data = bytearray(4)
    for r, g, b in frame:
        lin = [min(max(v, 0.0), 1.0) ** GAMMA * brightness for v in (r, g, b)]
        peak = max(lin)
        if peak <= 1e-5:
            data += b"\xe0\x00\x00\x00"
            continue
        level = min(31, max(1, math.ceil(peak * 31)))
        k = 255 * 31 / level
        data += bytes((0xE0 | level, min(255, round(lin[2] * k)),
                       min(255, round(lin[1] * k)), min(255, round(lin[0] * k))))
    data += b"\xff" * 4
    return bytes(data)


class LedPower:
    """LED_PWR_N through python3-libgpiod v2 if present, else a gpioset process."""

    def __init__(self, name="LED_PWR_N"):
        self.name = name
        self.request = None
        self.proc = None

    def on(self):
        try:
            import gpiod
            from gpiod.line import Direction, Value
        except ImportError:
            gpiod = None
        if gpiod is not None and hasattr(gpiod, "request_lines"):
            for path in sorted(glob.glob("/dev/gpiochip*")):
                try:
                    with gpiod.Chip(path) as chip:
                        offset = chip.line_offset_from_id(self.name)
                except (OSError, ValueError, LookupError):
                    continue
                settings = gpiod.LineSettings(direction=Direction.OUTPUT, active_low=True,
                                              output_value=Value.ACTIVE)
                self.request = gpiod.request_lines(path, consumer="satellite-leds",
                                                   config={offset: settings})
                self.offset = offset
                return
            raise RuntimeError("GPIO line %s not found" % self.name)
        if shutil.which("gpioset"):
            self.proc = subprocess.Popen(["gpioset", "--consumer", "satellite-leds", "--active-low",
                                          self.name + "=1"], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
            time.sleep(0.2)
            return
        raise RuntimeError("no way to switch LED_PWR_N (install python3-libgpiod)")

    def off(self):
        if self.request is not None:
            from gpiod.line import Value
            try:
                self.request.set_value(self.offset, Value.INACTIVE)
                self.request.release()
            except OSError:
                pass
            self.request = None
        if self.proc is not None:
            self.proc.terminate()
            self.proc = None


class Apa102:
    def __init__(self, path="/dev/spidev0.1", hz=8000000):
        try:
            import spidev
            m = re.search(r"spidev(\d+)\.(\d+)$", path)
            self.spi = spidev.SpiDev()
            self.spi.open(int(m.group(1)), int(m.group(2)))
            self.spi.max_speed_hz = hz
            self.spi.mode = 0
            self.write = lambda d: self.spi.xfer2(list(d))
        except ImportError:
            fd = os.open(path, os.O_WRONLY)
            self.write = lambda d: os.write(fd, d)

    def show(self, frame, brightness):
        self.write(encode_apa102(frame, brightness))


class NullStrip:
    """No hardware (development machine): preview only."""

    def show(self, frame, brightness):
        pass
