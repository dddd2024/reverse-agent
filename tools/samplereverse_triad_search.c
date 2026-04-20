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

static void build_key72(const uint8_t prefix7[7], uint8_t key[72]) {
    uint8_t candidate[13];
    uint8_t expanded[26];
    uint8_t raw[52];
    uint8_t b64[72];
    size_t i = 0;
    size_t out = 0;

    memcpy(candidate, prefix7, 7);
    memset(candidate + 7, 0x41, 6);

    for (i = 0; i < 13; ++i) {
        expanded[i * 2] = ((candidate[i] >> 4) & 0x0f) + 0x78;
        expanded[i * 2 + 1] = (candidate[i] & 0x0f) + 0x7a;
    }
    for (i = 0; i < 26; ++i) {
        raw[i * 2] = expanded[i];
        raw[i * 2 + 1] = 0;
    }

    for (i = 0; i < 52; i += 3) {
        uint32_t block = ((uint32_t)raw[i] << 16);
        int remain = (int)(52 - i);
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

    for (i = 0; i < 72; ++i) {
        key[i] = (i & 1) == 0 ? b64[i >> 1] : 0;
    }
}

static void decrypt_prefix10(const uint8_t prefix7[7], uint8_t out[10]) {
    uint8_t key[72];
    uint8_t s[256];
    uint8_t i;
    uint8_t j = 0;
    int idx;

    build_key72(prefix7, key);
    for (idx = 0; idx < 256; ++idx) {
        s[idx] = (uint8_t)idx;
    }
    for (idx = 0; idx < 256; ++idx) {
        uint8_t si = s[idx];
        j = (uint8_t)(j + si + key[idx % 72]);
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

static int distance10(const uint8_t raw[10]) {
    int dist = 0;
    int i;
    for (i = 0; i < 10; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static int distance4(const uint8_t raw[10]) {
    int dist = 0;
    int i;
    for (i = 0; i < 4; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

int main(int argc, char **argv) {
    uint8_t base[7] = {0x6f, 0x7e, 0xeb, 0xb7, 0xa2, 0x30, 0x37};
    int p0, p1, p2;
    const char *mode = "prefix";
    int v0, v1, v2;
    uint8_t cand[7];
    uint8_t raw[10];
    int best_exact = -1;
    int best_dist = 1 << 30;
    uint8_t best_prefix[7];
    uint8_t best_raw[10];

    if (argc != 4 && argc != 5 && argc != 7) {
        fprintf(stderr, "usage: %s p0 p1 p2 [prefix|dist4|dist6|dist10] [--base HEX14]\n", argv[0]);
        return 2;
    }

    p0 = atoi(argv[1]);
    p1 = atoi(argv[2]);
    p2 = atoi(argv[3]);
    if (argc == 5) {
        mode = argv[4];
    } else if (argc == 7) {
        if (strcmp(argv[4], "--base") == 0) {
            size_t i;
            const char *hex = argv[5];
            if (strlen(hex) != 14) {
                fprintf(stderr, "invalid --base hex length\n");
                return 2;
            }
            for (i = 0; i < 7; ++i) {
                char tmp[3];
                char *end = NULL;
                unsigned long value;
                tmp[0] = hex[i * 2];
                tmp[1] = hex[i * 2 + 1];
                tmp[2] = '\0';
                value = strtoul(tmp, &end, 16);
                if (end == NULL || *end != '\0' || value > 0xffUL) {
                    fprintf(stderr, "invalid --base hex content\n");
                    return 2;
                }
                base[i] = (uint8_t)value;
            }
            mode = argv[6];
        } else if (strcmp(argv[5], "--base") == 0) {
            size_t i;
            const char *hex = argv[6];
            if (strlen(hex) != 14) {
                fprintf(stderr, "invalid --base hex length\n");
                return 2;
            }
            mode = argv[4];
            for (i = 0; i < 7; ++i) {
                char tmp[3];
                char *end = NULL;
                unsigned long value;
                tmp[0] = hex[i * 2];
                tmp[1] = hex[i * 2 + 1];
                tmp[2] = '\0';
                value = strtoul(tmp, &end, 16);
                if (end == NULL || *end != '\0' || value > 0xffUL) {
                    fprintf(stderr, "invalid --base hex content\n");
                    return 2;
                }
                base[i] = (uint8_t)value;
            }
        } else {
            fprintf(stderr, "usage: %s p0 p1 p2 [prefix|dist4|dist6|dist10] [--base HEX14]\n", argv[0]);
            return 2;
        }
    }
    memcpy(best_prefix, base, 7);
    decrypt_prefix10(base, best_raw);
    best_exact = exact_prefix_len(best_raw);
    best_dist = distance10(best_raw);
    printf(
        "START exact=%d dist=%d cand=%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
        best_exact,
        best_dist,
        best_prefix[0],
        best_prefix[1],
        best_prefix[2],
        best_prefix[3],
        best_prefix[4],
        best_prefix[5],
        best_prefix[6],
        best_raw[0],
        best_raw[1],
        best_raw[2],
        best_raw[3],
        best_raw[4],
        best_raw[5],
        best_raw[6],
        best_raw[7],
        best_raw[8],
        best_raw[9]
    );
    fflush(stdout);

    for (v0 = 1; v0 <= 255; ++v0) {
        memcpy(cand, base, 7);
        cand[p0] = (uint8_t)v0;
        for (v1 = 1; v1 <= 255; ++v1) {
            cand[p1] = (uint8_t)v1;
            for (v2 = 1; v2 <= 255; ++v2) {
                int exact;
                int dist;
                cand[p2] = (uint8_t)v2;
                decrypt_prefix10(cand, raw);
                exact = exact_prefix_len(raw);
                dist = distance10(raw);
                if (
                    (strcmp(mode, "dist4") == 0 && distance4(raw) < distance4(best_raw)) ||
                    (strcmp(mode, "dist6") == 0 && distance4(raw) <= distance4(best_raw) && (
                        (abs((int)raw[0] - (int)TARGET[0]) +
                         abs((int)raw[1] - (int)TARGET[1]) +
                         abs((int)raw[2] - (int)TARGET[2]) +
                         abs((int)raw[3] - (int)TARGET[3]) +
                         abs((int)raw[4] - (int)TARGET[4]) +
                         abs((int)raw[5] - (int)TARGET[5])) <
                        (abs((int)best_raw[0] - (int)TARGET[0]) +
                         abs((int)best_raw[1] - (int)TARGET[1]) +
                         abs((int)best_raw[2] - (int)TARGET[2]) +
                         abs((int)best_raw[3] - (int)TARGET[3]) +
                         abs((int)best_raw[4] - (int)TARGET[4]) +
                         abs((int)best_raw[5] - (int)TARGET[5]))
                    )) ||
                    (strcmp(mode, "dist10") == 0 && dist < best_dist) ||
                    (strcmp(mode, "prefix") == 0 && (
                        exact > best_exact ||
                        (exact == best_exact && dist < best_dist)
                    ))
                ) {
                    best_exact = exact;
                    best_dist = dist;
                    memcpy(best_prefix, cand, 7);
                    memcpy(best_raw, raw, 10);
                    printf(
                        "BEST exact=%d dist=%d cand=%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
                        best_exact,
                        best_dist,
                        best_prefix[0],
                        best_prefix[1],
                        best_prefix[2],
                        best_prefix[3],
                        best_prefix[4],
                        best_prefix[5],
                        best_prefix[6],
                        best_raw[0],
                        best_raw[1],
                        best_raw[2],
                        best_raw[3],
                        best_raw[4],
                        best_raw[5],
                        best_raw[6],
                        best_raw[7],
                        best_raw[8],
                        best_raw[9]
                    );
                    fflush(stdout);
                }
                if (exact == 10) {
                    printf(
                        "FOUND cand=%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
                        cand[0],
                        cand[1],
                        cand[2],
                        cand[3],
                        cand[4],
                        cand[5],
                        cand[6],
                        raw[0],
                        raw[1],
                        raw[2],
                        raw[3],
                        raw[4],
                        raw[5],
                        raw[6],
                        raw[7],
                        raw[8],
                        raw[9]
                    );
                    return 0;
                }
            }
        }
    }

    printf(
        "FINAL exact=%d dist=%d cand=%02x%02x%02x%02x%02x%02x%02x raw=%02x%02x%02x%02x%02x%02x%02x%02x%02x%02x\n",
        best_exact,
        best_dist,
        best_prefix[0],
        best_prefix[1],
        best_prefix[2],
        best_prefix[3],
        best_prefix[4],
        best_prefix[5],
        best_prefix[6],
        best_raw[0],
        best_raw[1],
        best_raw[2],
        best_raw[3],
        best_raw[4],
        best_raw[5],
        best_raw[6],
        best_raw[7],
        best_raw[8],
        best_raw[9]
    );
    return 0;
}
