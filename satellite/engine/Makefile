# satd - satellite audio engine
#   make            build ./satd
#   make install    install to $(PREFIX)/bin (default /usr/local)

CC      ?= gcc
PREFIX  ?= /usr/local
ARCH    := $(shell uname -m)

ifneq (,$(filter armv7l armv7 armhf,$(ARCH)))
  ARCHFLAGS ?= -mcpu=cortex-a7 -mfpu=neon-vfpv4 -mfloat-abi=hard
endif

CFLAGS  ?= -O2
CFLAGS  += -std=gnu11 -Wall -Wextra -Wno-unused-parameter -ffast-math -fno-finite-math-only \
           -pthread $(ARCHFLAGS) $(shell pkg-config --cflags alsa speexdsp)
LDLIBS  += $(shell pkg-config --libs alsa speexdsp) -lm -pthread

SRC := $(wildcard src/*.c)
OBJ := $(SRC:.c=.o)

satd: $(OBJ)
	$(CC) $(CFLAGS) -o $@ $(OBJ) $(LDLIBS)

src/%.o: src/%.c $(wildcard src/*.h)
	$(CC) $(CFLAGS) -c -o $@ $<

install: satd
	install -D -m 0755 satd $(DESTDIR)$(PREFIX)/bin/satd

clean:
	rm -f satd src/*.o

.PHONY: install clean
