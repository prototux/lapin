/*
 * ESP32-Korvo V1.1 hardware.
 *
 * Pins from Espressif's own support for this board:
 *  - esp-skainet components/hardware_driver/boards/include/
 *    esp32_korvo_v1_1_board.h and boards/esp32-korvo/bsp_board.c (current):
 *    I2C SCL 32 / SDA 19; I2S1 (ES7210 ADC, mics + playback reference)
 *    MCLK 0, SCLK 27, LRCK 26, DIN 36; I2S0 (ES8311 DAC) MCLK 0, SCLK 25,
 *    LRCK 22, DOUT 13; PA enable GPIO 12; ES7210 4 channels at 16 kHz read
 *    as 2 x 32-bit slots, gain 30 dB; ES8311 without MCLK.
 *  - esp-skainet history (commit a55beab "support ESP32-Corvo", 2020):
 *    WS2812 data on GPIO 33, 12 LEDs; buttons on ADC1 channel 3 (GPIO 39).
 *  - esp-skainet commit 440d02e (button driver): ADC ladder levels (11 dB
 *    attenuation): REC ~2.41 V, MODE ~1.99 V, PLAY ~1.66 V, SET ~1.11 V,
 *    VOL- ~0.82 V, VOL+ ~0.38 V.
 *  - Schematics ESP32-KORVO_V1.1 / ESP32-KORVO-MIC_V1.1: the DAC output is
 *    looped back to ES7210 MIC3 (AEC reference, i.e. exactly what drives the
 *    speaker), three analog mics on the other inputs, ESP32-WROVER-E
 *    (16 MB flash, 8 MB PSRAM).
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

#define BOARD_I2C_SCL     32
#define BOARD_I2C_SDA     19
#define BOARD_I2C_HZ      100000

#define BOARD_ADC_MCLK    0     /* shared by both codecs */
#define BOARD_ADC_SCLK    27
#define BOARD_ADC_LRCK    26
#define BOARD_ADC_DIN     36

#define BOARD_DAC_SCLK    25
#define BOARD_DAC_LRCK    22
#define BOARD_DAC_DOUT    13
#define BOARD_PA_EN       12

#define BOARD_LED_GPIO    33
#define BOARD_LED_COUNT   12
#define BOARD_BTN_GPIO    39    /* ADC1 channel 3 */

#define BOARD_MIC_RATE    16000
#define BOARD_LANES       4     /* ES7210: 4 x 16-bit lanes per frame */
#define BOARD_SPK_RATE    48000

esp_err_t board_init(int mic_gain_db, int dac_volume);
/* Reads `frames` frames of the 4 ES7210 lanes (int16, interleaved). */
esp_err_t board_read_mics(int16_t *buf, int frames);
/* Writes mono 48 kHz samples to the speaker (duplicated to both slots). */
esp_err_t board_write_speaker(const int16_t *mono, int n);
/* Output buffering of the I2S DMA (for the mixer's sync start). */
int board_speaker_latency_ms(void);

/* DMA underflows of the speaker output since boot (diagnosis) */
uint32_t board_tx_underflows(void);
