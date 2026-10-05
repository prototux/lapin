#ifndef SAT_B64_H
#define SAT_B64_H

#include <stddef.h>
#include <stdint.h>

/* Decodes standard base64 (padding optional, whitespace ignored). Returns
 * the number of bytes written to out (at most cap), or -1 on bad input. */
int b64_decode(const char *in, size_t n, uint8_t *out, size_t cap);
static inline size_t b64_decoded_max(size_t n) { return n / 4 * 3 + 3; }

#endif
