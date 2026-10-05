#pragma once

#include <stdint.h>

void link_start(void);
void link_set_wifi(int up);
int link_send_text(const char *json);
int link_send_audio(const int16_t *pcm, int samples);
int link_online(void);
int link_socket_up(void);
uint32_t link_tx_dropped(void);
int64_t link_online_since_us(void);
