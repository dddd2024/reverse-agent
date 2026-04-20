#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const uint8_t ENC_CONST[10] = {
    0x69, 0x8b, 0x8f, 0xb1, 0x8f, 0x3b, 0x4f, 0x99, 0x61, 0x72,
};

static const uint8_t TARGET[10] = {
    0x66, 0x00, 0x6c, 0x00, 0x61, 0x00, 0x67, 0x00, 0x7b, 0x00,
};

static const uint8_t B64_TABLE[64] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static inline int lower_ascii(int value) {
    if (value >= 0x41 && value <= 0x5a) {
        return value + 0x20;
    }
    return value;
}

static void build_key76(const uint8_t prefix8[8], uint8_t key[76]) {
    uint8_t candidate[14];
    uint8_t expanded[28];
    uint8_t raw[56];
    uint8_t b64[76];
    size_t i = 0;
    size_t out = 0;

    memcpy(candidate, prefix8, 8);
    memset(candidate + 8, 0x41, 6);

    for (i = 0; i < 14; ++i) {
        expanded[i * 2] = ((candidate[i] >> 4) & 0x0f) + 0x78;
        expanded[i * 2 + 1] = (candidate[i] & 0x0f) + 0x7a;
    }
    for (i = 0; i < 28; ++i) {
        raw[i * 2] = expanded[i];
        raw[i * 2 + 1] = 0;
    }

    for (i = 0; i < 56; i += 3) {
        uint32_t block = ((uint32_t)raw[i] << 16);
        int remain = (int)(56 - i);
        if (remain > 1) {
            block |= ((uint32_t)raw[i + 1] << 8);
        }
        if (remain > 2) {
            block |= raw[i + 2];
        }
        b64[out++] = B64_TABLE[(block >> 18) & 0x3f];
        b64[out++] = B64_TABLE[(block >> 12) & 0x3f];
        b64[out++] = (remain > 1) ? B64_TABLE[(block >> 6) & 0x3f] : '=';
        b64[out++] = (remain > 2) ? B64_TABLE[block & 0x3f] : '=';
    }

    for (i = 0; i < 76; ++i) {
        key[i] = (i & 1) == 0 ? b64[i >> 1] : 0;
    }
}

static void decrypt_prefix10(const uint8_t prefix8[8], uint8_t out[10]) {
    uint8_t key[76];
    uint8_t s[256];
    uint8_t i;
    uint8_t j = 0;
    int idx;

    build_key76(prefix8, key);
    for (idx = 0; idx < 256; ++idx) {
        s[idx] = (uint8_t)idx;
    }
    for (idx = 0; idx < 256; ++idx) {
        uint8_t si = s[idx];
        j = (uint8_t)(j + si + key[idx % 76]);
        s[idx] = s[j];
        s[j] = si;
    }

    i = 0;
    j = 0;
    for (idx = 0; idx < 10; ++idx) {
        uint8_t tmp;
        uint8_t ks;
        i = (uint8_t)(i + 1);
        j = (uint8_t)(j + s[i]);
        tmp = s[i];
        s[i] = s[j];
        s[j] = tmp;
        ks = s[(uint8_t)(s[i] + s[j])];
        out[idx] = ENC_CONST[idx] ^ ks;
    }
}

static int exact_prefix_len(const uint8_t raw[10]) {
    int exact = 0;
    int i;
    for (i = 0; i < 10; ++i) {
        if (lower_ascii(raw[i]) != lower_ascii(TARGET[i])) {
            break;
        }
        exact += 1;
    }
    return exact;
}

static int distance4(const uint8_t raw[10]) {
    int dist = 0;
    int i;
    for (i = 0; i < 4; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static int distance6(const uint8_t raw[10]) {
    int dist = 0;
    int i;
    for (i = 0; i < 6; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static int distance10(const uint8_t raw[10]) {
    int dist = 0;
    int i;
    for (i = 0; i < 10; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static int parse_hex_seed(const char *hex, uint8_t out[8]) {
    size_t i;
    if (strlen(hex) != 16) {
        return 0;
    }
    for (i = 0; i < 8; ++i) {
        char tmp[3];
        char *end = NULL;
        unsigned long value;
        tmp[0] = hex[i * 2];
        tmp[1] = hex[i * 2 + 1];
        tmp[2] = '\0';
        value = strtoul(tmp, &end, 16);
        if (end == NULL || *end != '\0' || value > 0xffUL) {
            return 0;
        }
        out[i] = (uint8_t)value;
    }
    return 1;
}

int main(int argc, char **argv) {
    uint8_t base[8];
    uint8_t cand[8];
    uint8_t raw[10];
    uint8_t best_cand[8];
    uint8_t best_raw[10];
    int p0;
    int p1;
    int v0;
    int v1;
    int best_exact;
    int best_dist4;
    int best_dist6;
    int best_dist10;

    if (argc != 4) {
        fprintf(stderr, "usage: %s p0 p1 base_hex16\n", argv[0]);
        return 2;
    }
    p0 = atoi(argv[1]);
    p1 = atoi(argv[2]);
    if (p0 < 0 || p0 >= 8 || p1 < 0 || p1 >= 8 || p0 == p1) {
        fprintf(stderr, "invalid positions\n");
        return 2;
    }
    if (!parse_hex_seed(argv[3], base)) {
        fprintf(stderr, "invalid base hex\n");
        return 2;
    }

    memcpy(best_cand, base, 8);
    decrypt_prefix10(base, best_raw);
    best_exact = exact_prefix_len(best_raw);
    best_dist4 = distance4(best_raw);
    best_dist6 = distance6(best_raw);
    best_dist10 = distance10(best_raw);

    printf(
        "START exact=%d dist4=%d dist6=%d dist10=%d cand=%02x%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
        best_exact,
        best_dist4,
        best_dist6,
        best_dist10,
        best_cand[0], best_cand[1], best_cand[2], best_cand[3],
        best_cand[4], best_cand[5], best_cand[6], best_cand[7],
        best_raw[0], best_raw[1], best_raw[2], best_raw[3], best_raw[4],
        best_raw[5], best_raw[6], best_raw[7], best_raw[8], best_raw[9]
    );
    fflush(stdout);

    for (v0 = 1; v0 <= 255; ++v0) {
        memcpy(cand, base, 8);
        cand[p0] = (uint8_t)v0;
        for (v1 = 1; v1 <= 255; ++v1) {
            int exact;
            int d4;
            int d6;
            int d10;
            cand[p1] = (uint8_t)v1;
            decrypt_prefix10(cand, raw);
            exact = exact_prefix_len(raw);
            d4 = distance4(raw);
            d6 = distance6(raw);
            d10 = distance10(raw);
            if (
                exact > best_exact ||
                (exact == best_exact && d4 < best_dist4) ||
                (exact == best_exact && d4 == best_dist4 && d6 < best_dist6) ||
                (exact == best_exact && d4 == best_dist4 && d6 == best_dist6 && d10 < best_dist10)
            ) {
                best_exact = exact;
                best_dist4 = d4;
                best_dist6 = d6;
                best_dist10 = d10;
                memcpy(best_cand, cand, 8);
                memcpy(best_raw, raw, 10);
                printf(
                    "BEST exact=%d dist4=%d dist6=%d dist10=%d cand=%02x%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
                    best_exact,
                    best_dist4,
                    best_dist6,
                    best_dist10,
                    best_cand[0], best_cand[1], best_cand[2], best_cand[3],
                    best_cand[4], best_cand[5], best_cand[6], best_cand[7],
                    best_raw[0], best_raw[1], best_raw[2], best_raw[3], best_raw[4],
                    best_raw[5], best_raw[6], best_raw[7], best_raw[8], best_raw[9]
                );
                fflush(stdout);
            }
        }
    }

    printf(
        "FINAL exact=%d dist4=%d dist6=%d dist10=%d cand=%02x%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
        best_exact,
        best_dist4,
        best_dist6,
        best_dist10,
        best_cand[0], best_cand[1], best_cand[2], best_cand[3],
        best_cand[4], best_cand[5], best_cand[6], best_cand[7],
        best_raw[0], best_raw[1], best_raw[2], best_raw[3], best_raw[4],
        best_raw[5], best_raw[6], best_raw[7], best_raw[8], best_raw[9]
    );
    return 0;
}
