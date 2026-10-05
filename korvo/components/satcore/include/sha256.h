#ifndef SAT_SHA256_H
#define SAT_SHA256_H
#include <stddef.h>
#include <stdint.h>
/* Small portable SHA-256 (FIPS 180-4), for checking update images. */
typedef struct {
    uint32_t h[8];
    uint64_t len;
    uint8_t buf[64];
    size_t n;
} sha256_t;
void sha256_init(sha256_t *s);
void sha256_update(sha256_t *s, const void *data, size_t len);
void sha256_final(sha256_t *s, uint8_t out[32]);
#endif
