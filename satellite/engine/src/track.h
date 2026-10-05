#ifndef SATD_TRACK_H
#define SATD_TRACK_H

#include "beam.h"

/* Speaker tracker: constant-velocity Kalman filter on the bearing, updated
 * with SRP-PHAT peaks only on speech frames. A slowly decaying map of the
 * directions that stay active without speech (TV, fan, dishwasher) marks
 * static interferers whose measurements are ignored. */
typedef struct {
    float th, om;               /* bearing (deg, unwrapped) and velocity (deg/s) */
    float P[2][2];
    int valid;
    float static_map[NANG];     /* 0..1 per direction */
    float gate_deg;
    /* re-acquisition: consistent measurements outside the gate */
    float alt_az;
    int alt_count;
    int64_t locked_until;       /* frames; tighter tracking right after a wake */
    int64_t frame;
} tracker_t;

void tracker_init(tracker_t *t, float gate_deg);
void tracker_predict(tracker_t *t, float dt);
/* Feeds one DOA analysis. speech: the frame contains speech; quiet: no speech
 * for a while (only then are localized sounds taken as static interferers). */
void tracker_update(tracker_t *t, const doa_t *d, int speech, int quiet, float min_conf);
void tracker_lock(tracker_t *t, float az);
float tracker_angle(const tracker_t *t);
int tracker_is_static(const tracker_t *t, float az);

#endif
