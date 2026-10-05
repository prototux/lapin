# Runs the portable core (KWS, engine, mixer) on the ESP32 target, under
# Espressif's QEMU, on real wake word recordings: see run_qemu.sh.
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ../../components/satcore)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(korvo_bench)
