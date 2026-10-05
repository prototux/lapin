# Host build of the firmware's portable core (components/satcore) + tests.
CORE   = ../components/satcore
CJSON  = ../managed_components/espressif__cjson/cJSON
CC    ?= gcc
CFLAGS = -O2 -g -std=gnu11 -fcx-limited-range -fno-math-errno -Wall -Wextra -Wno-unused-parameter \
         -I$(CORE)/include -I$(CJSON) -pthread
SRC    = $(CORE)/src/fft.c $(CORE)/src/ns.c $(CORE)/src/kws.c $(CORE)/src/resample.c \
         $(CORE)/src/mixer.c $(CORE)/src/engine.c $(CORE)/src/proto.c $(CORE)/src/templates.c \
         $(CORE)/src/b64.c $(CORE)/src/anim.c $(CORE)/src/ota.c $(CORE)/src/sha256.c $(CJSON)/cJSON.c port_host.c
BINS   = hostsat test_kws test_mixer test_proto test_ota test_logbuf

all: $(BINS)

hostsat: hostsat.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^ -lm
test_kws: test_kws.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^ -lm
test_mixer: test_mixer.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^ -lm
test_proto: test_proto.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^ -lm
test_logbuf: test_logbuf.c ../main/logbuf.c
	$(CC) -O1 -g -Wall -Istubs -I../main -I$(CORE)/include -o $@ $^
test_ota: test_ota.c $(SRC)
	$(CC) $(CFLAGS) -o $@ $^ -lm

clean:
	rm -f $(BINS)
.PHONY: all clean

# reference: the satellite's own detector (read-only use of its sources)
SATSRC = ../../satellite/engine/src
ref_trace: ref_trace.c
	$(CC) -O2 -I$(SATSRC) -o $@ $< $(SATSRC)/kws.c $(SATSRC)/fft.c $(SATSRC)/ns.c -lm
