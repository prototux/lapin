#ifndef SAT_TEMPLATES_H
#define SAT_TEMPLATES_H

#include "kws.h"

/*
 * Wake word templates from the server.
 *
 * The Korvo has no enrollment of its own: on each connection it sends
 * wake_templates_get and the server answers with one wake_template message
 * per recording made on the other satellites (16 kHz WAV, base64, ~85 KB),
 * then wake_templates_done. Each WAV is turned into DTW features as it
 * arrives (the WAV itself is not kept); once all are in, the model is
 * installed in the engine, the automatic threshold is computed
 * (kws_suggest_threshold, as the satellite does) and the features are saved
 * to flash so the wake word works offline after a reboot. Learned negatives
 * (wakes the server rejected) are kept across collections.
 */

/* Positives kept (the rest of KWS_MAX_TPL is for negatives). */
#define TPL_MAX_POS (KWS_MAX_TPL - KWS_MAX_NEG)

void tpl_init(const char *store_path);
/* Loads the saved model at boot and installs it in the engine. Returns the
 * number of templates, or -1 if there is no usable store. */
int tpl_load_store(void);

/* Collection from the server. */
void tpl_collect_begin(void);
/* Returns 0 if the recording was usable. */
int tpl_collect_add(const char *keyword, const char *name, const char *wav_b64, size_t b64_len,
                    int index, int count);
/* Installs the collected templates (count = what the server announced),
 * saves them and sends wake_words. */
void tpl_collect_done(int count);

/* Saves the engine's current model (after a negative was learned). */
void tpl_save_current(void);

/* Low level, for tests: file I/O of a model. */
int tpl_save_file(const char *path, const kws_model_t *m, float thr);
int tpl_load_file(const char *path, kws_model_t *m, float *thr);

#endif
