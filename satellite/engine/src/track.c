#include "track.h"

#include <string.h>

#define MEAS_STD 9.0f      /* degrees, at full confidence */

void tracker_init(tracker_t *t, float gate_deg)
{
    memset(t, 0, sizeof *t);
    t->gate_deg = gate_deg;
    t->P[0][0] = 180.0f * 180.0f;
    t->P[1][1] = 100.0f;
}

void tracker_predict(tracker_t *t, float dt)
{
    t->frame++;
    t->th += t->om * dt;
    t->om *= 0.97f;                         /* a talker stops more than drifts */
    /* P = F P F' + Q, F = [1 dt; 0 1] */
    float p00 = t->P[0][0] + dt * (t->P[1][0] + t->P[0][1]) + dt * dt * t->P[1][1];
    float p01 = t->P[0][1] + dt * t->P[1][1];
    float p11 = t->P[1][1];
    const float q_th = 2.0f, q_om = 40.0f;  /* per second */
    t->P[0][0] = fminf(p00 + q_th * dt * 60.0f, 180.0f * 180.0f);
    t->P[0][1] = t->P[1][0] = p01;
    t->P[1][1] = fminf(p11 + q_om * dt * 60.0f, 400.0f);

    for (int a = 0; a < NANG; a++)
        t->static_map[a] *= 0.9995f;        /* ~30 s memory */
}

int tracker_is_static(const tracker_t *t, float az)
{
    int i = ang_index(az);
    float v = fmaxf(t->static_map[i], fmaxf(t->static_map[(i + 1) % NANG],
                                             t->static_map[(i + NANG - 1) % NANG]));
    return v > 0.5f;
}

static void kalman(tracker_t *t, float z, float conf)
{
    float y = angdiff(z, t->th);
    float r = MEAS_STD / fmaxf(conf, 0.1f);
    float S = t->P[0][0] + r * r;
    float k0 = t->P[0][0] / S, k1 = t->P[1][0] / S;
    t->th += k0 * y;
    t->om = clampf(t->om + k1 * y, -180.0f, 180.0f);
    float p00 = (1 - k0) * t->P[0][0], p01 = (1 - k0) * t->P[0][1];
    float p11 = t->P[1][1] - k1 * t->P[0][1];
    t->P[0][0] = p00;
    t->P[0][1] = t->P[1][0] = p01;
    t->P[1][1] = p11;
}

void tracker_update(tracker_t *t, const doa_t *d, int speech, int quiet, float min_conf)
{
    if (d->npeaks == 0)
        return;
    /* Directions that stay active are static interferers: fast without
     * speech, very slowly with it (a TV talks all the time; a person talking
     * for half a minute in one place does not get there). */
    if (d->confidence > min_conf && (speech || quiet)) {
        float rate = speech ? 0.0004f : 0.02f;
        for (int p = 0; p < d->npeaks; p++) {
            int i = ang_index(d->peak[p].az);
            t->static_map[i] = fminf(1.0f, t->static_map[i] + rate * d->confidence);
        }
    }
    if (!speech)
        return;
    if (d->confidence < min_conf)
        return;

    /* candidate: the non-static peak closest to the track */
    int best = -1;
    float bestd = 1e9f;
    for (int p = 0; p < d->npeaks; p++) {
        if (tracker_is_static(t, d->peak[p].az) && t->frame > t->locked_until)
            continue;
        float dd = t->valid ? fabsf(angdiff(d->peak[p].az, t->th)) : (float)p;
        if (dd < bestd) {
            bestd = dd;
            best = p;
        }
    }
    if (best < 0)
        return;
    float z = d->peak[best].az, conf = d->confidence;

    if (!t->valid) {
        tracker_lock(t, z);
        t->locked_until = 0;
        return;
    }
    float gate = t->frame < t->locked_until ? t->gate_deg * 0.6f : t->gate_deg;
    if (bestd <= gate) {
        kalman(t, z, conf);
        t->alt_count = 0;
        return;
    }
    /* outside the gate: re-acquire if it persists (~0.4 s of speech) */
    if (t->alt_count > 0 && fabsf(angdiff(z, t->alt_az)) < 25.0f) {
        t->alt_az = wrap360(t->alt_az + 0.3f * angdiff(z, t->alt_az));
        if (++t->alt_count >= 25 && t->frame >= t->locked_until) {
            tracker_lock(t, t->alt_az);
            t->locked_until = 0;
        }
    } else {
        t->alt_az = z;
        t->alt_count = 1;
    }
}

void tracker_lock(tracker_t *t, float az)
{
    t->th = az;
    t->om = 0;
    t->P[0][0] = 15.0f * 15.0f;
    t->P[0][1] = t->P[1][0] = 0;
    t->P[1][1] = 50.0f;
    t->valid = 1;
    t->alt_count = 0;
    t->locked_until = t->frame + 90;        /* ~1.5 s */
    /* the user just spoke from there: not an interferer */
    int c = ang_index(az);
    for (int k = -3; k <= 3; k++)
        t->static_map[(c + k + NANG) % NANG] = 0;
}

float tracker_angle(const tracker_t *t) { return wrap360(t->th); }
