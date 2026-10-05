#ifndef SAT_ANIM_H
#define SAT_ANIM_H

/*
 * LED ring animations, a C port of the satellite's LED plugin
 * (satellite/agent/satagent/plugins/leds/anim.py): soft gaussian light,
 * eased motion, crossfaded states and transient overlays. The Korvo has no
 * direction of arrival, so "listening" lights the whole ring instead of a
 * lobe facing the talker.
 */
#define ANIM_N 12

typedef struct {
    float r, g, b;
} rgb_t;

enum anim_base { AB_IDLE, AB_LISTENING, AB_THINKING, AB_SPEAKING, AB_MUTED, AB_OFFLINE, AB_PENDING,
                 AB_ALARM, AB_NOTIFY, AB_SETUP, AB_UPDATE, AB_COUNT };
enum anim_overlay { AO_WAKE, AO_VOLUME, AO_MUTE, AO_ERROR, AO_SUCCESS, AO_BOOT, AO_COUNT };

typedef struct {
    float level;        /* voice level 0..1 (smoothed by anim_render) */
    float out;          /* playback level 0..1 */
    float volume;       /* 0..100 */
    int idle_breathe;   /* idle: 0 off, 1 dim breathing */
    float progress;     /* firmware update 0..1 */
} anim_ctx_t;

void anim_init(void);
void anim_set_base(int base);
int anim_get_base(void);
void anim_overlay(int ov, float arg);
/* Advances by dt seconds and renders the ring (linear light, 0..1). */
void anim_render(float dt, const anim_ctx_t *c, rgb_t out[ANIM_N]);

#endif
