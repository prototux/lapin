# Portable core: also compiled on a PC by test/Makefile.
idf_component_register(
    SRCS "src/fft.c" "src/ns.c" "src/kws.c" "src/resample.c" "src/mixer.c" "src/engine.c"
         "src/proto.c" "src/templates.c" "src/b64.c" "src/anim.c"
         "src/ota.c" "src/sha256.c"
    INCLUDE_DIRS "include"
    REQUIRES espressif__cjson)
# complex arithmetic without the C99 NaN/inf corner cases (no __mulsc3 calls
# in the FFT), and speed over strict IEEE in the DSP
target_compile_options(${COMPONENT_LIB} PRIVATE -O2 -fcx-limited-range -fno-math-errno -Wall -Wextra -Wno-unused-parameter)
