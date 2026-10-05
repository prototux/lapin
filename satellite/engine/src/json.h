#ifndef SATD_JSON_H
#define SATD_JSON_H

#include <stddef.h>

/* Minimal reader for the flat JSON objects of the IPC protocol: looks up a
 * top-level key and converts its value. Nested values are skipped correctly
 * but can only be fetched as raw text (json_get_raw). */
int json_has(const char *js, const char *key);
int json_get_str(const char *js, const char *key, char *out, size_t n);
int json_get_num(const char *js, const char *key, double *out);
int json_get_bool(const char *js, const char *key, int *out);
int json_get_num_array(const char *js, const char *key, double *out, int max);
int json_get_raw(const char *js, const char *key, const char **start, size_t *len);

/* Iterates the top-level members: returns 1 and fills key / raw value, 0 at the end.
 * *pos must start at 0. */
int json_next(const char *js, size_t *pos, char *key, size_t keyn,
              const char **val, size_t *vlen);

/* Appends s as a JSON string literal (with quotes) to out; returns the length written. */
size_t json_escape(char *out, size_t n, const char *s);

#endif
