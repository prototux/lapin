#pragma once
/* 12 WS2812 on the mic board: state animations (components/satcore/src/anim.c). */
void leds_start(int brightness, int idle_breathe);
void leds_set_config(int brightness, int idle_breathe);
void leds_set_progress(int active, float fraction);  /* firmware update */
void leds_event(const char *event, int arg);   /* engine UI events */
void leds_set_setup_mode(int on);             /* Wi-Fi setup access point open */
