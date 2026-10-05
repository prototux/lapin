/* ESP32-Korvo V1.1 audio codecs (see board.h for the sources of the pins). */
#include "board.h"

#include <string.h>

#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"
#include "esp_heap_caps.h"
#include "esp_log.h"

static const char *TAG = "board";

#define DAC_DMA_DESC   8       /* 40 ms of output buffering, 5 ms each (7.7 KB; 60 ms */
#define DAC_DMA_FRAMES 240      /* on 1.1.8 ran internal RAM out, before the stacks moved to PSRAM) */

static i2c_master_bus_handle_t i2c_bus;
static i2s_chan_handle_t tx_chan, rx_chan;
static esp_codec_dev_handle_t mic_dev, spk_dev;
static int16_t *spk_buf;        /* stereo frames for the DAC */

/* the DMA ran out of data to send: the play task was late (a click) */
static volatile uint32_t tx_underflows;
static IRAM_ATTR bool on_tx_underflow(i2s_chan_handle_t h, i2s_event_data_t *e, void *ctx)
{
    tx_underflows++;
    return false;
}
uint32_t board_tx_underflows(void) { return tx_underflows; }

static esp_err_t i2s_init(void)
{
    /* DAC: I2S0, 48 kHz, 16-bit stereo; the ES8311 runs from BCLK (no MCLK
     * output here: GPIO 0 carries the ES7210's MCLK from I2S1) */
    i2s_chan_config_t tx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    tx_cfg.auto_clear = true;
    tx_cfg.dma_desc_num = DAC_DMA_DESC;
    tx_cfg.dma_frame_num = DAC_DMA_FRAMES;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&tx_cfg, &tx_chan, NULL), TAG, "i2s0");
    i2s_std_config_t tx_std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_SPK_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED, .bclk = BOARD_DAC_SCLK, .ws = BOARD_DAC_LRCK,
                     .dout = BOARD_DAC_DOUT, .din = I2S_GPIO_UNUSED},
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(tx_chan, &tx_std), TAG, "i2s0 std");
    i2s_event_callbacks_t cbs = {.on_send_q_ovf = on_tx_underflow};
    i2s_channel_register_event_callback(tx_chan, &cbs, NULL);
    ESP_RETURN_ON_ERROR(i2s_channel_enable(tx_chan), TAG, "i2s0 enable");

    /* ADC: I2S1, 16 kHz, 2 x 32-bit slots carrying the ES7210's 4 x 16-bit
     * channels (TDM), MCLK on GPIO 0 */
    i2s_chan_config_t rx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    rx_cfg.dma_desc_num = 6;
    rx_cfg.dma_frame_num = 256;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&rx_cfg, NULL, &rx_chan), TAG, "i2s1");
    i2s_std_config_t rx_std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(BOARD_MIC_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = BOARD_ADC_MCLK, .bclk = BOARD_ADC_SCLK, .ws = BOARD_ADC_LRCK,
                     .dout = I2S_GPIO_UNUSED, .din = BOARD_ADC_DIN},
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(rx_chan, &rx_std), TAG, "i2s1 std");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(rx_chan), TAG, "i2s1 enable");
    return ESP_OK;
}

esp_err_t board_init(int mic_gain_db, int dac_volume)
{
    i2c_master_bus_config_t bus = {
        .i2c_port = I2C_NUM_0,
        .scl_io_num = BOARD_I2C_SCL,
        .sda_io_num = BOARD_I2C_SDA,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus, &i2c_bus), TAG, "i2c");
    /* both codecs must answer (7-bit addresses: ES7210 0x40, ES8311 0x18) */
    if (i2c_master_probe(i2c_bus, ES7210_CODEC_DEFAULT_ADDR >> 1, 100) != ESP_OK) {
        ESP_LOGE(TAG, "ES7210 (microphone ADC) not found on I2C: is the mic board's FPC cable connected?");
        return ESP_ERR_NOT_FOUND;
    }
    if (i2c_master_probe(i2c_bus, ES8311_CODEC_DEFAULT_ADDR >> 1, 100) != ESP_OK) {
        ESP_LOGE(TAG, "ES8311 (speaker DAC) not found on I2C");
        return ESP_ERR_NOT_FOUND;
    }
    ESP_RETURN_ON_ERROR(i2s_init(), TAG, "i2s");

    /* ES7210: the 4 inputs (3 mics + the DAC loopback on MIC3) */
    audio_codec_i2s_cfg_t mi2s = {.port = I2S_NUM_1, .rx_handle = rx_chan, .tx_handle = NULL};
    const audio_codec_data_if_t *mdata = audio_codec_new_i2s_data(&mi2s);
    audio_codec_i2c_cfg_t mi2c = {.port = I2C_NUM_0, .addr = ES7210_CODEC_DEFAULT_ADDR, .bus_handle = i2c_bus,
                                  .clock_speed_hz = BOARD_I2C_HZ};
    const audio_codec_ctrl_if_t *mctrl = audio_codec_new_i2c_ctrl(&mi2c);
    es7210_codec_cfg_t es7210 = {
        .ctrl_if = mctrl,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3 | ES7210_SEL_MIC4,
    };
    const audio_codec_if_t *mcodec = es7210_codec_new(&es7210);
    esp_codec_dev_cfg_t mcfg = {.dev_type = ESP_CODEC_DEV_TYPE_IN, .codec_if = mcodec, .data_if = mdata};
    mic_dev = esp_codec_dev_new(&mcfg);
    if (!mic_dev)
        return ESP_FAIL;
    esp_codec_dev_sample_info_t mfs = {.bits_per_sample = 32, .channel = 2, .sample_rate = BOARD_MIC_RATE};
    if (esp_codec_dev_open(mic_dev, &mfs) != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "ES7210 not answering");
        return ESP_FAIL;
    }
    for (int ch = 0; ch < 4; ch++)
        esp_codec_dev_set_in_channel_gain(mic_dev, ESP_CODEC_DEV_MAKE_CHANNEL_MASK(ch), (float)mic_gain_db);

    /* ES8311 DAC + power amplifier */
    audio_codec_i2s_cfg_t si2s = {.port = I2S_NUM_0, .rx_handle = NULL, .tx_handle = tx_chan};
    const audio_codec_data_if_t *sdata = audio_codec_new_i2s_data(&si2s);
    audio_codec_i2c_cfg_t si2c = {.port = I2C_NUM_0, .addr = ES8311_CODEC_DEFAULT_ADDR, .bus_handle = i2c_bus,
                                  .clock_speed_hz = BOARD_I2C_HZ};
    const audio_codec_ctrl_if_t *sctrl = audio_codec_new_i2c_ctrl(&si2c);
    const audio_codec_gpio_if_t *gpio = audio_codec_new_gpio();
    es8311_codec_cfg_t es8311 = {
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .ctrl_if = sctrl,
        .gpio_if = gpio,
        .pa_pin = BOARD_PA_EN,
        .use_mclk = false,
    };
    const audio_codec_if_t *scodec = es8311_codec_new(&es8311);
    esp_codec_dev_cfg_t scfg = {.dev_type = ESP_CODEC_DEV_TYPE_OUT, .codec_if = scodec, .data_if = sdata};
    spk_dev = esp_codec_dev_new(&scfg);
    if (!spk_dev)
        return ESP_FAIL;
    esp_codec_dev_sample_info_t sfs = {.bits_per_sample = 16, .channel = 2, .sample_rate = BOARD_SPK_RATE};
    if (esp_codec_dev_open(spk_dev, &sfs) != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "ES8311 not answering");
        return ESP_FAIL;
    }
    esp_codec_dev_set_out_vol(spk_dev, dac_volume);
    spk_buf = heap_caps_malloc(sizeof(int16_t) * 2 * 960, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    ESP_LOGI(TAG, "codecs ready: ES7210 4 ch @ %d Hz (gain %d dB), ES8311 @ %d Hz (volume %d)", BOARD_MIC_RATE,
             mic_gain_db, BOARD_SPK_RATE, dac_volume);
    return spk_buf ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t board_read_mics(int16_t *buf, int frames)
{
    /* 2 x 32-bit slots per frame = 4 x int16 */
    return esp_codec_dev_read(mic_dev, buf, frames * BOARD_LANES * 2) == ESP_CODEC_DEV_OK ? ESP_OK : ESP_FAIL;
}

esp_err_t board_write_speaker(const int16_t *mono, int n)
{
    while (n > 0) {
        int k = n > 960 ? 960 : n;
        for (int i = 0; i < k; i++)
            spk_buf[2 * i] = spk_buf[2 * i + 1] = mono[i];
        if (esp_codec_dev_write(spk_dev, spk_buf, k * 4) != ESP_CODEC_DEV_OK)
            return ESP_FAIL;
        mono += k;
        n -= k;
    }
    return ESP_OK;
}

int board_speaker_latency_ms(void) { return DAC_DMA_DESC * DAC_DMA_FRAMES * 1000 / BOARD_SPK_RATE; }
