#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PREFIX_LEN 8
#define PREFIX_BYTES 10

typedef struct {
    int positions[6];
    uint8_t cand[PREFIX_LEN];
    uint8_t raw[PREFIX_BYTES];
    int exact;
    int dist4;
    int dist6;
    int dist10;
} Result;

static const uint8_t ENC_CONST[PREFIX_BYTES] = {
    0x69, 0x8b, 0x8f, 0xb1, 0x8f, 0x3b, 0x4f, 0x99, 0x61, 0x72,
};

static const uint8_t TARGET[PREFIX_BYTES] = {
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

static int parse_hex_seed(const char *hex, uint8_t out[PREFIX_LEN]) {
    size_t i;
    if (strlen(hex) != PREFIX_LEN * 2) {
        return 0;
    }
    for (i = 0; i < PREFIX_LEN; ++i) {
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

static void bytes_to_hex(const uint8_t *src, size_t len, char *dst) {
    static const char digits[] = "0123456789abcdef";
    size_t i;
    for (i = 0; i < len; ++i) {
        dst[i * 2] = digits[(src[i] >> 4) & 0x0f];
        dst[i * 2 + 1] = digits[src[i] & 0x0f];
    }
    dst[len * 2] = '\0';
}

static void build_key(const uint8_t prefix8[PREFIX_LEN], int input_len, uint8_t *key, int key_len) {
    uint8_t candidate[15];
    uint8_t expanded[30];
    uint8_t raw[60];
    size_t expanded_len = (size_t)input_len * 2;
    size_t raw_len = expanded_len * 2;
    size_t i = 0;
    size_t out = 0;

    memcpy(candidate, prefix8, PREFIX_LEN);
    memset(candidate + PREFIX_LEN, 0x41, (size_t)input_len - PREFIX_LEN);

    for (i = 0; i < (size_t)input_len; ++i) {
        expanded[i * 2] = ((candidate[i] >> 4) & 0x0f) + 0x78;
        expanded[i * 2 + 1] = (candidate[i] & 0x0f) + 0x7a;
    }
    for (i = 0; i < expanded_len; ++i) {
        raw[i * 2] = expanded[i];
        raw[i * 2 + 1] = 0;
    }

    for (i = 0; i < raw_len; i += 3) {
        uint32_t block = ((uint32_t)raw[i] << 16);
        int remain = (int)(raw_len - i);
        if (remain > 1) {
            block |= ((uint32_t)raw[i + 1] << 8);
        }
        if (remain > 2) {
            block |= raw[i + 2];
        }
        key[out++] = B64_TABLE[(block >> 18) & 0x3f];
        key[out++] = B64_TABLE[(block >> 12) & 0x3f];
        key[out++] = (remain > 1) ? B64_TABLE[(block >> 6) & 0x3f] : '=';
        key[out++] = (remain > 2) ? B64_TABLE[block & 0x3f] : '=';
    }

    for (i = key_len; i > 0; --i) {
        key[i - 1] = ((i - 1) & 1) == 0 ? key[(i - 1) >> 1] : 0;
    }
}

static void decrypt_prefix10(const uint8_t prefix8[PREFIX_LEN], int input_len, uint8_t out[PREFIX_BYTES]) {
    uint8_t key[80];
    uint8_t s[256];
    uint8_t i;
    uint8_t j = 0;
    int key_len = input_len == 14 ? 76 : 80;
    int idx;

    build_key(prefix8, input_len, key, key_len);
    for (idx = 0; idx < 256; ++idx) {
        s[idx] = (uint8_t)idx;
    }
    for (idx = 0; idx < 256; ++idx) {
        uint8_t si = s[idx];
        j = (uint8_t)(j + si + key[idx % key_len]);
        s[idx] = s[j];
        s[j] = si;
    }

    i = 0;
    j = 0;
    for (idx = 0; idx < PREFIX_BYTES; ++idx) {
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

static int exact_prefix_len(const uint8_t raw[PREFIX_BYTES]) {
    int exact = 0;
    int i;
    for (i = 0; i < PREFIX_BYTES; ++i) {
        if (lower_ascii(raw[i]) != lower_ascii(TARGET[i])) {
            break;
        }
        exact += 1;
    }
    return exact;
}

static int distance4(const uint8_t raw[PREFIX_BYTES]) {
    int dist = 0;
    int i;
    for (i = 0; i < 4; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static int distance6(const uint8_t raw[PREFIX_BYTES]) {
    int dist = 0;
    int i;
    for (i = 0; i < 6; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static int distance10(const uint8_t raw[PREFIX_BYTES]) {
    int dist = 0;
    int i;
    for (i = 0; i < PREFIX_BYTES; ++i) {
        dist += abs((int)raw[i] - (int)TARGET[i]);
    }
    return dist;
}

static void evaluate_result(Result *result, const uint8_t cand[PREFIX_LEN], int input_len) {
    memcpy(result->cand, cand, PREFIX_LEN);
    decrypt_prefix10(cand, input_len, result->raw);
    result->exact = exact_prefix_len(result->raw);
    result->dist4 = distance4(result->raw);
    result->dist6 = distance6(result->raw);
    result->dist10 = distance10(result->raw);
}

static int better_result(const Result *a, const Result *b) {
    if (a->exact != b->exact) {
        return a->exact > b->exact;
    }
    if (a->dist4 != b->dist4) {
        return a->dist4 < b->dist4;
    }
    if (a->dist6 != b->dist6) {
        return a->dist6 < b->dist6;
    }
    if (a->dist10 != b->dist10) {
        return a->dist10 < b->dist10;
    }
    return memcmp(a->cand, b->cand, PREFIX_LEN) < 0;
}

static void set_nibble(uint8_t cand[PREFIX_LEN], int nibble_index, int value) {
    if (nibble_index < 14) {
        int byte_index = nibble_index / 2;
        if ((nibble_index & 1) == 0) {
            cand[byte_index] = (uint8_t)((cand[byte_index] & 0x0f) | ((value & 0x0f) << 4));
        } else {
            cand[byte_index] = (uint8_t)((cand[byte_index] & 0xf0) | (value & 0x0f));
        }
    } else {
        cand[7] = (uint8_t)((cand[7] & 0x0f) | ((value & 0x0f) << 4));
    }
}

static void print_result(const char *label, const Result *result) {
    char cand_hex[PREFIX_LEN * 2 + 1];
    char raw_hex[PREFIX_BYTES * 2 + 1];
    bytes_to_hex(result->cand, PREFIX_LEN, cand_hex);
    bytes_to_hex(result->raw, PREFIX_BYTES, raw_hex);
    printf(
        "%s exact=%d dist4=%d dist6=%d dist10=%d cand8=%s raw=%s combo=[%d,%d,%d,%d,%d,%d]\n",
        label,
        result->exact,
        result->dist4,
        result->dist6,
        result->dist10,
        cand_hex,
        raw_hex,
        result->positions[0],
        result->positions[1],
        result->positions[2],
        result->positions[3],
        result->positions[4],
        result->positions[5]
    );
}

int main(int argc, char **argv) {
    const char *base_hex = NULL;
    uint8_t base[PREFIX_LEN];
    Result best;
    uint64_t eval_count = 0;
    int input_len = 0;
    int p[6];
    int idx;

    if (argc != 11) {
        fprintf(stderr, "usage: %s --base HEX16 --input-len 14|15 p0 p1 p2 p3 p4 p5\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "--base") != 0 || strcmp(argv[3], "--input-len") != 0) {
        fprintf(stderr, "usage: %s --base HEX16 --input-len 14|15 p0 p1 p2 p3 p4 p5\n", argv[0]);
        return 2;
    }
    base_hex = argv[2];
    input_len = atoi(argv[4]);
    for (idx = 0; idx < 6; ++idx) {
        p[idx] = atoi(argv[5 + idx]);
        if (p[idx] < 0 || p[idx] >= 15) {
            fprintf(stderr, "invalid nibble index: %d\n", p[idx]);
            return 2;
        }
    }
    for (idx = 0; idx < 6; ++idx) {
        int j;
        for (j = idx + 1; j < 6; ++j) {
            if (p[idx] == p[j]) {
                fprintf(stderr, "duplicate nibble index\n");
                return 2;
            }
        }
    }
    if (input_len != 14 && input_len != 15) {
        fprintf(stderr, "input_len must be 14 or 15\n");
        return 2;
    }
    if (!parse_hex_seed(base_hex, base)) {
        fprintf(stderr, "invalid base hex\n");
        return 2;
    }

    evaluate_result(&best, base, input_len);
    for (idx = 0; idx < 6; ++idx) {
        best.positions[idx] = p[idx];
    }
    print_result("START", &best);

    {
        int n0, n1, n2, n3, n4, n5;
        for (n0 = 0; n0 < 16; ++n0) {
            for (n1 = 0; n1 < 16; ++n1) {
                for (n2 = 0; n2 < 16; ++n2) {
                    for (n3 = 0; n3 < 16; ++n3) {
                        for (n4 = 0; n4 < 16; ++n4) {
                            for (n5 = 0; n5 < 16; ++n5) {
                                uint8_t cand[PREFIX_LEN];
                                Result cur;
                                memcpy(cand, base, PREFIX_LEN);
                                set_nibble(cand, p[0], n0);
                                set_nibble(cand, p[1], n1);
                                set_nibble(cand, p[2], n2);
                                set_nibble(cand, p[3], n3);
                                set_nibble(cand, p[4], n4);
                                set_nibble(cand, p[5], n5);
                                evaluate_result(&cur, cand, input_len);
                                cur.positions[0] = p[0];
                                cur.positions[1] = p[1];
                                cur.positions[2] = p[2];
                                cur.positions[3] = p[3];
                                cur.positions[4] = p[4];
                                cur.positions[5] = p[5];
                                eval_count += 1;
                                if (better_result(&cur, &best)) {
                                    best = cur;
                                    print_result("BEST", &best);
                                    fflush(stdout);
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    printf("evaluations=%llu\n", (unsigned long long)eval_count);
    print_result("FINAL", &best);
    return 0;
}
