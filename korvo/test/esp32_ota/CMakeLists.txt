# The device side of the WebSocket update, on the ESP32 target under QEMU:
# ota.c + main/ota_esp.c write the real shipped firmware (dist/) into ota_1
# chunk by chunk, as the server's 0x03 frames would; it then boots, and since
# it never reaches a server (no welcome) the bootloader rolls back. See run_qemu.sh.
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ../../components/satcore)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(korvo_ota_test)
