#!/usr/bin/env bash
# Rebuilds the firmware and refreshes dist/ (the prebuilt images flash.sh uses).
# Needs ESP-IDF v5.5 (default ~/esp/esp-idf, or $IDF_PATH). Builds with at most
# $JOBS (4) parallel jobs inside a 6 GB memory cap when systemd is available.
set -euo pipefail
cd "$(dirname "$0")"
IDF=${IDF_PATH:-$HOME/esp/esp-idf}
JOBS=${JOBS:-4}
if [ ! -f "$IDF/export.sh" ]; then
    echo "ESP-IDF not found in $IDF (set IDF_PATH). Install it with:" >&2
    echo "  git clone -b v5.5.5 --recursive --depth 1 --shallow-submodules https://github.com/espressif/esp-idf.git ~/esp/esp-idf" >&2
    echo "  ~/esp/esp-idf/install.sh esp32" >&2
    exit 1
fi
# shellcheck disable=SC1091
. "$IDF/export.sh" >/dev/null

run() {
    if command -v systemd-run >/dev/null && systemd-run --user --scope --quiet true 2>/dev/null; then
        systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 --quiet "$@"
    else
        "$@"
    fi
}

if command -v free >/dev/null; then
    avail=$(free -g | awk '/^Mem:/{print $7}')
    [ "${avail:-99}" -lt 8 ] && echo "warning: only ${avail} GB of memory available" >&2
fi

[ -f build/build.ninja ] || run idf.py -B build reconfigure
run ninja -C build -j"$JOBS"

mkdir -p dist
cp build/bootloader/bootloader.bin dist/bootloader.bin
cp build/partition_table/partition-table.bin dist/partition-table.bin
cp build/korvo_satellite.bin dist/korvo_satellite.bin
cp build/ota_data_initial.bin dist/ota_data_initial.bin
# symbols, to decode the crash backtraces the device reports (xtensa-esp32-elf-addr2line -e)
xtensa-esp32-elf-strip --strip-debug -o dist/korvo_satellite.elf build/korvo_satellite.elf
python - <<'PY'
import csv, hashlib, json, os, re, subprocess, datetime
d = "dist"
args = open("build/flash_args").read().split("\n")
mode = re.search(r"--flash_mode (\S+)", args[0]).group(1)
freq = re.search(r"--flash_freq (\S+)", args[0]).group(1)
size = re.search(r"--flash_size (\S+)", args[0]).group(1)
names = {"bootloader/bootloader.bin": "bootloader.bin", "partition_table/partition-table.bin": "partition-table.bin",
         "korvo_satellite.bin": "korvo_satellite.bin", "ota_data_initial.bin": "ota_data_initial.bin"}
images = []
for line in args[1:]:
    if line.strip():
        off, f = line.split()
        name = names[f]
        images.append({"offset": off, "file": name,
                       "sha256": hashlib.sha256(open(os.path.join(d, name), "rb").read()).hexdigest()})
images.sort(key=lambda i: int(i["offset"], 16))
parts = {}
for row in csv.reader(l for l in open("partitions.csv") if l.strip() and not l.startswith("#")):
    row = [c.strip() for c in row]
    parts[row[0]] = {"offset": row[3], "size": row[4]}
ver = re.search(r'CONFIG_APP_PROJECT_VER="([^"]*)"', open("sdkconfig").read()).group(1)
idf = subprocess.run(["git", "-C", os.environ["IDF_PATH"], "describe", "--tags"], capture_output=True,
                     text=True).stdout.strip()
m = {"name": "korvo-satellite", "version": ver, "chip": "esp32", "board": "ESP32-Korvo V1.1",
     "built": datetime.datetime.now().isoformat(timespec="seconds"), "idf": idf,
     "flash": {"mode": mode, "freq": freq, "size": size}, "images": images,
     "cfg": {"partition": "cfg", "offset": parts["cfg"]["offset"], "size": parts["cfg"]["size"], "namespace": "korvo"},
     "min_flash_mb": (max(int(p["offset"], 16) + int(p["size"], 16) for p in parts.values()) + 0xFFFFF) // 0x100000,
     "ota": {"file": "korvo_satellite.bin", "size": os.path.getsize(os.path.join(d, "korvo_satellite.bin")),
             "sha256": hashlib.sha256(open(os.path.join(d, "korvo_satellite.bin"), "rb").read()).hexdigest(),
             "slot_size": int(parts["ota_0"]["size"], 16)},
     "merged": "korvo-satellite-full.bin"}
json.dump(m, open(os.path.join(d, "manifest.json"), "w"), indent=2)
print("dist/manifest.json: version %s, %s" % (ver, ", ".join("%s@%s" % (i["file"], i["offset"]) for i in images)))
PY
# one image of everything except the settings (for other flashing tools, at 0x0)
python -m esptool --chip esp32 merge_bin -o dist/korvo-satellite-full.bin \
    --flash_mode dio --flash_freq 80m --flash_size 4MB \
    0x1000 dist/bootloader.bin 0x8000 dist/partition-table.bin 0xf000 dist/ota_data_initial.bin \
    0x20000 dist/korvo_satellite.bin >/dev/null
idf.py -B build size 2>/dev/null | grep -E "Total image size|DRAM |IRAM " || true
ls -l dist
