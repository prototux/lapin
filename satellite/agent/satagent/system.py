"""Board health figures for telemetry and the web UI."""

import glob
import os
import subprocess
import time

_cpu_prev = None


def cpu_temp():
    for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            with open(path) as f:
                return int(f.read()) / 1000
        except (OSError, ValueError):
            pass
    return None


def cpu_percent():
    """Whole-system CPU use since the previous call."""
    global _cpu_prev
    try:
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:]]
    except OSError:
        return None
    idle, total = v[3] + v[4], sum(v)
    prev, _cpu_prev = _cpu_prev, (idle, total)
    if not prev or total == prev[1]:
        return None
    return round(100 * (1 - (idle - prev[0]) / (total - prev[1])), 1)


def memory():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) // 1024
    except OSError:
        return {}
    return {"total_mb": info.get("MemTotal"), "available_mb": info.get("MemAvailable")}


def uptime():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except OSError:
        return None


def snapshot():
    return {"cpu_temp": cpu_temp(), "cpu": cpu_percent(), "load": os.getloadavg()[0],
            "mem": memory(), "uptime": uptime(), "time": time.time()}


def journal(unit, lines=200):
    try:
        out = subprocess.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout or out.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)


def restart(unit):
    """Restarts one of our own units (allowed by the sudoers rule installed with the satellite)."""
    return subprocess.run(["sudo", "-n", "systemctl", "restart", unit], capture_output=True,
                          text=True, timeout=20)
