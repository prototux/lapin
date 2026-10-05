#include "json.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *ws(const char *p)
{
    while (*p && isspace((unsigned char)*p))
        p++;
    return p;
}

/* Returns the end of the string literal starting at p (on the opening quote). */
static const char *skip_string(const char *p)
{
    p++;
    while (*p && *p != '"') {
        if (*p == '\\' && p[1])
            p++;
        p++;
    }
    return *p ? p + 1 : p;
}

/* Returns the end of the value starting at p. */
static const char *skip_value(const char *p)
{
    p = ws(p);
    if (*p == '"')
        return skip_string(p);
    if (*p == '{' || *p == '[') {
        int depth = 0;
        while (*p) {
            if (*p == '"') {
                p = skip_string(p);
                continue;
            }
            if (*p == '{' || *p == '[')
                depth++;
            else if (*p == '}' || *p == ']') {
                depth--;
                if (depth == 0)
                    return p + 1;
            }
            p++;
        }
        return p;
    }
    while (*p && *p != ',' && *p != '}' && *p != ']' && !isspace((unsigned char)*p))
        p++;
    return p;
}

/* Copies a string literal's content, decoding the common escapes. */
static void unescape(const char *p, const char *end, char *out, size_t n)
{
    size_t o = 0;
    for (p++; p < end - 1 && o + 1 < n; p++) {
        char c = *p;
        if (c == '\\' && p + 1 < end - 1) {
            c = *++p;
            switch (c) {
            case 'n': c = '\n'; break;
            case 't': c = '\t'; break;
            case 'r': c = '\r'; break;
            case 'b': c = '\b'; break;
            case 'f': c = '\f'; break;
            case 'u':
                /* keep ASCII, replace the rest */
                if (p + 4 < end) {
                    char hex[5] = {p[1], p[2], p[3], p[4], 0};
                    long v = strtol(hex, NULL, 16);
                    c = v < 128 ? (char)v : '?';
                    p += 4;
                }
                break;
            default: break;
            }
        }
        out[o++] = c;
    }
    out[o] = 0;
}

int json_next(const char *js, size_t *pos, char *key, size_t keyn,
              const char **val, size_t *vlen)
{
    const char *p = js + *pos;
    if (*pos == 0) {
        p = ws(p);
        if (*p != '{')
            return 0;
        p++;
    }
    p = ws(p);
    if (*p == ',')
        p = ws(p + 1);
    if (*p != '"')
        return 0;
    const char *kend = skip_string(p);
    unescape(p, kend, key, keyn);
    p = ws(kend);
    if (*p != ':')
        return 0;
    p = ws(p + 1);
    const char *vend = skip_value(p);
    *val = p;
    *vlen = (size_t)(vend - p);
    *pos = (size_t)(vend - js);
    return 1;
}

int json_get_raw(const char *js, const char *key, const char **start, size_t *len)
{
    size_t pos = 0;
    char k[64];
    const char *v;
    size_t vl;
    while (json_next(js, &pos, k, sizeof k, &v, &vl)) {
        if (strcmp(k, key) == 0) {
            *start = v;
            *len = vl;
            return 1;
        }
    }
    return 0;
}

int json_has(const char *js, const char *key)
{
    const char *v;
    size_t l;
    return json_get_raw(js, key, &v, &l);
}

int json_get_str(const char *js, const char *key, char *out, size_t n)
{
    const char *v;
    size_t l;
    if (!json_get_raw(js, key, &v, &l) || *v != '"')
        return 0;
    unescape(v, v + l, out, n);
    return 1;
}

int json_get_num(const char *js, const char *key, double *out)
{
    const char *v;
    size_t l;
    if (!json_get_raw(js, key, &v, &l))
        return 0;
    if (*v == 't' || *v == 'f') {
        *out = *v == 't';
        return 1;
    }
    char *end;
    double d = strtod(v, &end);
    if (end == v)
        return 0;
    *out = d;
    return 1;
}

int json_get_bool(const char *js, const char *key, int *out)
{
    const char *v;
    size_t l;
    if (!json_get_raw(js, key, &v, &l))
        return 0;
    if (strncmp(v, "true", 4) == 0)
        *out = 1;
    else if (strncmp(v, "false", 5) == 0)
        *out = 0;
    else {
        char *end;
        double d = strtod(v, &end);
        if (end == v)
            return 0;
        *out = d != 0;
    }
    return 1;
}

int json_get_num_array(const char *js, const char *key, double *out, int max)
{
    const char *v;
    size_t l;
    if (!json_get_raw(js, key, &v, &l) || *v != '[')
        return -1;
    const char *p = v + 1, *end = v + l;
    int n = 0;
    while (p < end && n < max) {
        p = ws(p);
        if (*p == ']')
            break;
        char *e;
        double d = strtod(p, &e);
        if (e == p)
            break;
        out[n++] = d;
        p = ws(e);
        if (*p == ',')
            p++;
    }
    return n;
}

size_t json_escape(char *out, size_t n, const char *s)
{
    size_t o = 0;
    if (n < 3)
        return 0;
    out[o++] = '"';
    for (; *s && o + 7 < n; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') {
            out[o++] = '\\';
            out[o++] = (char)c;
        } else if (c < 0x20) {
            o += (size_t)snprintf(out + o, n - o, "\\u%04x", c);
        } else {
            out[o++] = (char)c;
        }
    }
    out[o++] = '"';
    out[o] = 0;
    return o;
}
