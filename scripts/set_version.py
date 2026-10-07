#!/usr/bin/env python3
"""Sets the version of every part of Lapin at once.

    scripts/set_version.py 1.3.0        # writes it into each part
    scripts/set_version.py              # prints the version of each part

The release workflow runs it with the tag's version (v1.3.0 -> 1.3.0) before
building, so the server, the satellites, the apps and the Korvo firmware all
report the release they come from. A suffix (1.3.0-rc1) is kept where the
format allows it; the Android versionCode and the desktop package version use
the numeric part only."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, pattern with one group around the version, how to write a version)
PLACES = [
    ("server/assistant/__init__.py", r'^__version__ = "([^"]*)"', '__version__ = "{v}"'),
    ("satellite/agent/satagent/__init__.py", r'^__version__ = "([^"]*)"', '__version__ = "{v}"'),
    ("satellite/engine/src/common.h", r'^#define SATD_VERSION "([^"]*)"', '#define SATD_VERSION "{v}"'),
    ("korvo/sdkconfig.defaults", r'^CONFIG_APP_PROJECT_VER="([^"]*)"', 'CONFIG_APP_PROJECT_VER="{v}"'),
    ("android/app/build.gradle.kts", r'^(\s*)versionCode = \d+', '{indent}versionCode = {code}'),
    ("android/app/build.gradle.kts", r'^\s*versionName = "([^"]*)"', '{indent}versionName = "{v}"'),
    ("desktop/lapin_desktop/__init__.py", r'^__version__ = "([^"]*)"', '__version__ = "{v}"'),
    ("desktop/pyproject.toml", r'^version = "([^"]*)"', 'version = "{core}"'),
]


def parse(v):
    m = re.fullmatch(r"v?((\d+)\.(\d+)\.(\d+))([-+][0-9A-Za-z.-]+)?", v)
    if not m:
        sys.exit("not a version: %r (expected 1.2.3 or 1.2.3-rc1)" % v)
    major, minor, patch = (int(x) for x in m.group(2, 3, 4))
    if minor > 99 or patch > 99:
        sys.exit("minor and patch must be below 100 (they make the Android versionCode)")
    return v.lstrip("v"), m.group(1), major * 10000 + minor * 100 + patch


def main():
    if len(sys.argv) < 2:
        for path, pattern, _ in PLACES:
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                m = re.search(pattern, f.read(), re.M)
            print("%-40s %s" % (path, m.group(0).strip() if m else "NOT FOUND"))
        return
    v, core, code = parse(sys.argv[1])
    for path, pattern, template in PLACES:
        full = os.path.join(ROOT, path)
        with open(full, encoding="utf-8") as f:
            text = f.read()

        def repl(m):
            indent = re.match(r"\s*", m.group(0)).group(0)
            return template.format(v=v, core=core, code=code, indent=indent)

        text, n = re.subn(pattern, repl, text, count=1, flags=re.M)
        if n != 1:
            sys.exit("%s: version line not found" % path)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    print("version %s (Android versionCode %d)" % (v, code))


if __name__ == "__main__":
    main()
