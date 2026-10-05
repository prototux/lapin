idf_component_register(
    SRCS "main.c" "mem.c" "logbuf.c" "board.c" "audio.c" "link.c" "port_esp.c" "ota_esp.c" "config.c" "wifi.c" "portal.c" "leds.c" "buttons.c"
    INCLUDE_DIRS "."
    REQUIRES satcore esp_driver_i2s esp_driver_i2c esp_adc esp_wifi esp_netif esp_event esp_http_server nvs_flash
             esp_timer esp_app_format esp_psram lwip app_update espcoredump
             espressif__esp-sr espressif__esp_codec_dev espressif__esp_websocket_client espressif__led_strip
             joltwallet__littlefs)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wno-unused-parameter)
# ESP-SR's prebuilt NS walks the whole heap on every chunk: make that a no-op
target_link_libraries(${COMPONENT_LIB} INTERFACE "-Wl,--wrap=heap_caps_check_integrity_all")
