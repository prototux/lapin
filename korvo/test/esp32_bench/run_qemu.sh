#!/usr/bin/env bash
# Builds the benchmark and runs it on Espressif's QEMU (ESP32 + 4 MB PSRAM)
# with -icount shift=0: one instruction per virtual nanosecond, so the
# "kinstr" figures it prints are instruction counts.
#   needs: ESP-IDF (IDF_PATH or ~/esp/esp-idf), qemu-xtensa
#   (python $IDF_PATH/tools/idf_tools.py install qemu-xtensa; it needs libslirp),
#   main/data/recordings.bin (make_recordings.py DIR_OF_WAVS)
set -euo pipefail
cd "$(dirname "$0")"
IDF=${IDF_PATH:-$HOME/esp/esp-idf}
. "$IDF/export.sh" >/dev/null
QEMU=${QEMU:-$(command -v qemu-system-xtensa || ls ~/.espressif/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa | head -1)}
[ -f build/build.ninja ] || idf.py -B build set-target esp32 >/dev/null
ninja -C build -j"${JOBS:-4}" >/dev/null
python -m esptool --chip esp32 merge_bin --fill-flash-size 4MB -o build/flash.bin --flash_mode dio \
    --flash_freq 40m --flash_size 4MB 0x1000 build/bootloader/bootloader.bin \
    0x8000 build/partition_table/partition-table.bin 0x10000 build/korvo_bench.bin >/dev/null
rm -f build/qemu.log
timeout 600 "$QEMU" -nographic -machine esp32 -m 4M -icount shift=0 -drive file=build/flash.bin,if=mtd,format=raw \
    -nic none -serial file:build/qemu.log -monitor none >/dev/null 2>&1 &
pid=$!
for _ in $(seq 1 120); do grep -q "BENCH: done" build/qemu.log 2>/dev/null && break; sleep 5; done
kill $pid 2>/dev/null || true
grep BENCH build/qemu.log
