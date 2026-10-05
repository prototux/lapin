idf_component_register(SRCS "bench.c" "port_bench.c" INCLUDE_DIRS "."
                       REQUIRES satcore esp_timer esp_psram
                       EMBED_FILES "data/recordings.bin"
                       WHOLE_ARCHIVE)
