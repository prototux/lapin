idf_component_register(SRCS "ota_test.c" "../../../main/ota_esp.c" INCLUDE_DIRS "."
                       REQUIRES satcore app_update esp_timer
                       EMBED_FILES "../../../dist/korvo_satellite.bin"
                       WHOLE_ARCHIVE)
