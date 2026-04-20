#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PREFIX_LEN 8
#define PREFIX_BYTES 10
#define NIBBLE_COUNT 15
#define ROW_CAPACITY 64

typedef struct {
    int nibbles[4];
    uint8_t cand[PREFIX_LEN];
    uint8_t raw[PREFIX_BYTES];
    int exact;
    int dist4;
    int dist6;
    int dist10;
} Row;

typedef struct {
    Row rows[ROW_CAPACITY];
    size_t len;
} RowPool;

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

static void evaluate_row(Row *row, const uint8_t cand[PREFIX_LEN], int input_len) {
    memcpy(row->cand, cand, PREFIX_LEN);
    decrypt_prefix10(cand, input_len, row->raw);
    row->exact = exact_prefix_len(row->raw);
    row->dist4 = distance4(row->raw);
    row->dist6 = distance6(row->raw);
    row->dist10 = distance10(row->raw);
}

static int better_row(const Row *a, const Row *b) {
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

static int same_raw(const Row *a, const Row *b) {
    return memcmp(a->raw, b->raw, PREFIX_BYTES) == 0;
}

static void sort_pool(RowPool *pool) {
    size_t i;
    size_t j;
    for (i = 0; i < pool->len; ++i) {
        for (j = i + 1; j < pool->len; ++j) {
            if (better_row(&pool->rows[j], &pool->rows[i])) {
                Row tmp = pool->rows[i];
                pool->rows[i] = pool->rows[j];
                pool->rows[j] = tmp;
            }
        }
    }
}

static void insert_row(RowPool *pool, const Row *row) {
    size_t i;
    for (i = 0; i < pool->len; ++i) {
        if (same_raw(&pool->rows[i], row)) {
            if (better_row(row, &pool->rows[i])) {
                pool->rows[i] = *row;
                sort_pool(pool);
            }
            return;
        }
    }
    if (pool->len < ROW_CAPACITY) {
        pool->rows[pool->len++] = *row;
        sort_pool(pool);
        return;
    }
    if (better_row(row, &pool->rows[pool->len - 1])) {
        pool->rows[pool->len - 1] = *row;
        sort_pool(pool);
    }
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

static void print_row(const char *label, const Row *row) {
    char cand_hex[PREFIX_LEN * 2 + 1];
    char raw_hex[PREFIX_BYTES * 2 + 1];
    bytes_to_hex(row->cand, PREFIX_LEN, cand_hex);
    bytes_to_hex(row->raw, PREFIX_BYTES, raw_hex);
    printf(
        "%s exact=%d dist4=%d dist6=%d dist10=%d cand8=%s raw=%s combo=[%d,%d,%d,%d]\n",
        label,
        row->exact,
        row->dist4,
        row->dist6,
        row->dist10,
        cand_hex,
        raw_hex,
        row->nibbles[0],
        row->nibbles[1],
        row->nibbles[2],
        row->nibbles[3]
    );
}

static int write_json(const char *out_path, const char *base_hex, int input_len, uint64_t eval_count, const RowPool *pool) {
    FILE *fp = fopen(out_path, "wb");
    size_t i;
    if (!fp) {
        fprintf(stderr, "failed to open %s for writing\n", out_path);
        return 0;
    }
    fprintf(fp, "{\n");
    fprintf(fp, "  \"base\": \"%s\",\n", base_hex);
    fprintf(fp, "  \"input_len\": %d,\n", input_len);
    fprintf(fp, "  \"evaluations\": %llu,\n", (unsigned long long)eval_count);
    fprintf(fp, "  \"best\": ");
    if (pool->len > 0) {
        char cand_hex[PREFIX_LEN * 2 + 1];
        char raw_hex[PREFIX_BYTES * 2 + 1];
        bytes_to_hex(pool->rows[0].cand, PREFIX_LEN, cand_hex);
        bytes_to_hex(pool->rows[0].raw, PREFIX_BYTES, raw_hex);
        fprintf(
            fp,
            "{\"cand8_hex\":\"%s\",\"candidate_hex\":\"%s%s\",\"raw\":\"%s\",\"exact\":%d,\"dist4\":%d,\"dist6\":%d,\"dist10\":%d,"
            "\"combo\":[%d,%d,%d,%d]}",
            cand_hex,
            cand_hex,
            input_len == 14 ? "414141414141" : "41414141414141",
            raw_hex,
            pool->rows[0].exact,
            pool->rows[0].dist4,
            pool->rows[0].dist6,
            pool->rows[0].dist10,
            pool->rows[0].nibbles[0],
            pool->rows[0].nibbles[1],
            pool->rows[0].nibbles[2],
            pool->rows[0].nibbles[3]
        );
    } else {
        fprintf(fp, "null");
    }
    fprintf(fp, ",\n  \"rows\": [\n");
    for (i = 0; i < pool->len; ++i) {
        char cand_hex[PREFIX_LEN * 2 + 1];
        char raw_hex[PREFIX_BYTES * 2 + 1];
        bytes_to_hex(pool->rows[i].cand, PREFIX_LEN, cand_hex);
        bytes_to_hex(pool->rows[i].raw, PREFIX_BYTES, raw_hex);
        fprintf(
            fp,
            "    {\"cand8_hex\":\"%s\",\"candidate_hex\":\"%s%s\",\"raw\":\"%s\",\"exact\":%d,\"dist4\":%d,\"dist6\":%d,\"dist10\":%d,"
            "\"combo\":[%d,%d,%d,%d]}%s\n",
            cand_hex,
            cand_hex,
            input_len == 14 ? "414141414141" : "41414141414141",
            raw_hex,
            pool->rows[i].exact,
            pool->rows[i].dist4,
            pool->rows[i].dist6,
            pool->rows[i].dist10,
            pool->rows[i].nibbles[0],
            pool->rows[i].nibbles[1],
            pool->rows[i].nibbles[2],
            pool->rows[i].nibbles[3],
            i + 1 < pool->len ? "," : ""
        );
    }
    fprintf(fp, "  ]\n}\n");
    fclose(fp);
    return 1;
}

int main(int argc, char **argv) {
    const char *base_hex = NULL;
    const char *out_json = NULL;
    uint8_t base[PREFIX_LEN];
    RowPool pool = {{{0}}, 0};
    Row best;
    uint64_t eval_count = 0;
    int input_len = 0;
    int a, b, c, d;

    for (a = 1; a < argc; ++a) {
        if (strcmp(argv[a], "--base") == 0 && a + 1 < argc) {
            base_hex = argv[++a];
        } else if (strcmp(argv[a], "--input-len") == 0 && a + 1 < argc) {
            input_len = atoi(argv[++a]);
        } else if (strcmp(argv[a], "--out-json") == 0 && a + 1 < argc) {
            out_json = argv[++a];
        } else {
            fprintf(stderr, "usage: %s --base HEX16 --input-len 14|15 --out-json PATH\n", argv[0]);
            return 2;
        }
    }
    if (!base_hex || !out_json || (input_len != 14 && input_len != 15)) {
        fprintf(stderr, "usage: %s --base HEX16 --input-len 14|15 --out-json PATH\n", argv[0]);
        return 2;
    }
    if (!parse_hex_seed(base_hex, base)) {
        fprintf(stderr, "invalid base hex\n");
        return 2;
    }

    memset(&best, 0, sizeof(best));
    evaluate_row(&best, base, input_len);
    best.nibbles[0] = best.nibbles[1] = best.nibbles[2] = best.nibbles[3] = -1;
    insert_row(&pool, &best);
    print_row("START", &best);

    for (a = 0; a < NIBBLE_COUNT; ++a) {
        for (b = a + 1; b < NIBBLE_COUNT; ++b) {
            for (c = b + 1; c < NIBBLE_COUNT; ++c) {
                for (d = c + 1; d < NIBBLE_COUNT; ++d) {
                    int n0, n1, n2, n3;
                    for (n0 = 0; n0 < 16; ++n0) {
                        for (n1 = 0; n1 < 16; ++n1) {
                            for (n2 = 0; n2 < 16; ++n2) {
                                for (n3 = 0; n3 < 16; ++n3) {
                                    uint8_t cand[PREFIX_LEN];
                                    Row row;
                                    memcpy(cand, base, PREFIX_LEN);
                                    set_nibble(cand, a, n0);
                                    set_nibble(cand, b, n1);
                                    set_nibble(cand, c, n2);
                                    set_nibble(cand, d, n3);
                                    evaluate_row(&row, cand, input_len);
                                    row.nibbles[0] = a;
                                    row.nibbles[1] = b;
                                    row.nibbles[2] = c;
                                    row.nibbles[3] = d;
                                    insert_row(&pool, &row);
                                    eval_count += 1;
                                    if (better_row(&row, &best)) {
                                        best = row;
                                        print_row("BEST", &best);
                                        fflush(stdout);
                                    }
                                }
                            }
                        }
                    }
                    if ((eval_count % 4000000ULL) == 0ULL) {
                        printf("checkpoint evals=%llu combo=[%d,%d,%d,%d]\n", (unsigned long long)eval_count, a, b, c, d);
                        print_row("CUR", &best);
                        fflush(stdout);
                    }
                }
            }
        }
    }

    print_row("FINAL", &best);
    sort_pool(&pool);
    if (!write_json(out_json, base_hex, input_len, eval_count, &pool)) {
        return 1;
    }
    return 0;
}
