#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PREFIX_LEN 8
#define PREFIX_BYTES 10
#define POOL_CAPACITY 64
#define DEFAULT_MAX_EVALS 200000000ULL
#define DEFAULT_SEED 20260420ULL

typedef struct {
    uint8_t cand[PREFIX_LEN];
    uint8_t lhs[PREFIX_BYTES];
    int exact_prefix_len;
    int distance6;
    int distance10;
} Entry;

typedef struct {
    Entry entries[POOL_CAPACITY];
    size_t len;
} Pool;

static const uint8_t ENC_CONST[PREFIX_BYTES] = {
    0x69, 0x8b, 0x8f, 0xb1, 0x8f, 0x3b, 0x4f, 0x99, 0x61, 0x72,
};

static const uint8_t TARGET[PREFIX_BYTES] = {
    0x66, 0x00, 0x6c, 0x00, 0x61, 0x00, 0x67, 0x00, 0x7b, 0x00,
};

static const uint8_t B64_TABLE[64] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static inline uint64_t next_rand(uint64_t *state) {
    uint64_t x = *state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    return x * UINT64_C(2685821657736338717);
}

static inline int rand_range(uint64_t *state, int limit) {
    return (int)(next_rand(state) % (uint64_t)limit);
}

static inline int lower_ascii(int value) {
    if (value >= 0x41 && value <= 0x5a) {
        return value + 0x20;
    }
    return value;
}

static void build_key80(const uint8_t prefix8[PREFIX_LEN], uint8_t key[80]) {
    uint8_t candidate[15];
    uint8_t expanded[30];
    uint8_t raw[60];
    uint8_t b64[80];
    size_t i = 0;
    size_t out = 0;

    memcpy(candidate, prefix8, PREFIX_LEN);
    memset(candidate + PREFIX_LEN, 0x41, 7);

    for (i = 0; i < 15; ++i) {
        expanded[i * 2] = ((candidate[i] >> 4) & 0x0f) + 0x78;
        expanded[i * 2 + 1] = (candidate[i] & 0x0f) + 0x7a;
    }
    for (i = 0; i < 30; ++i) {
        raw[i * 2] = expanded[i];
        raw[i * 2 + 1] = 0;
    }

    for (i = 0; i < 60; i += 3) {
        uint32_t block = ((uint32_t)raw[i] << 16);
        int remain = (int)(60 - i);
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

    for (i = 0; i < 80; ++i) {
        key[i] = (i & 1) == 0 ? b64[i >> 1] : 0;
    }
}

static void decrypt_prefix10(const uint8_t prefix8[PREFIX_LEN], uint8_t out[PREFIX_BYTES]) {
    uint8_t key[80];
    uint8_t s[256];
    uint8_t i;
    uint8_t j = 0;
    int idx;

    build_key80(prefix8, key);
    for (idx = 0; idx < 256; ++idx) {
        s[idx] = (uint8_t)idx;
    }
    for (idx = 0; idx < 256; ++idx) {
        uint8_t si = s[idx];
        j = (uint8_t)(j + si + key[idx % 80]);
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

static void evaluate_entry(const uint8_t cand[PREFIX_LEN], Entry *entry) {
    memcpy(entry->cand, cand, PREFIX_LEN);
    decrypt_prefix10(cand, entry->lhs);
    entry->exact_prefix_len = exact_prefix_len(entry->lhs);
    entry->distance6 = distance6(entry->lhs);
    entry->distance10 = distance10(entry->lhs);
}

static int better_entry(const Entry *a, const Entry *b) {
    if (a->exact_prefix_len != b->exact_prefix_len) {
        return a->exact_prefix_len > b->exact_prefix_len;
    }
    if (a->distance6 != b->distance6) {
        return a->distance6 < b->distance6;
    }
    if (a->distance10 != b->distance10) {
        return a->distance10 < b->distance10;
    }
    return memcmp(a->cand, b->cand, PREFIX_LEN) < 0;
}

static int same_prefix(const Entry *a, const Entry *b) {
    return memcmp(a->lhs, b->lhs, PREFIX_BYTES) == 0;
}

static void insert_pool(Pool *pool, const Entry *entry) {
    size_t i;
    for (i = 0; i < pool->len; ++i) {
        if (same_prefix(&pool->entries[i], entry)) {
            if (better_entry(entry, &pool->entries[i])) {
                pool->entries[i] = *entry;
            }
            return;
        }
    }
    if (pool->len < POOL_CAPACITY) {
        pool->entries[pool->len++] = *entry;
        return;
    }
    for (i = 0; i < pool->len; ++i) {
        if (better_entry(entry, &pool->entries[i])) {
            size_t j;
            for (j = pool->len - 1; j > i; --j) {
                pool->entries[j] = pool->entries[j - 1];
            }
            pool->entries[i] = *entry;
            return;
        }
    }
}

static void sort_pool(Pool *pool) {
    size_t i;
    size_t j;
    for (i = 0; i < pool->len; ++i) {
        for (j = i + 1; j < pool->len; ++j) {
            if (better_entry(&pool->entries[j], &pool->entries[i])) {
                Entry tmp = pool->entries[i];
                pool->entries[i] = pool->entries[j];
                pool->entries[j] = tmp;
            }
        }
    }
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

static void mutate_candidate(uint8_t cand[PREFIX_LEN], uint64_t *rng) {
    int used[PREFIX_LEN] = {0};
    int changes = 4 + rand_range(rng, 3);
    int idx;
    for (idx = 0; idx < changes; ++idx) {
        int pos = rand_range(rng, PREFIX_LEN);
        while (used[pos]) {
            pos = rand_range(rng, PREFIX_LEN);
        }
        used[pos] = 1;
        cand[pos] = (uint8_t)(1 + rand_range(rng, 255));
    }
}

int main(int argc, char **argv) {
    const char *out_json = NULL;
    const char *base_hex = "4a78f0eaeb4f13b0";
    uint64_t max_evals = DEFAULT_MAX_EVALS;
    uint64_t seed = DEFAULT_SEED;
    uint64_t rng = DEFAULT_SEED;
    uint8_t base[PREFIX_LEN];
    Entry best;
    Pool pool = {{{0}}, 0};
    uint64_t eval_count = 0;
    int i;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--out-json") == 0 && i + 1 < argc) {
            out_json = argv[++i];
        } else if (strcmp(argv[i], "--max-evals") == 0 && i + 1 < argc) {
            max_evals = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            seed = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--base") == 0 && i + 1 < argc) {
            base_hex = argv[++i];
        } else {
            fprintf(stderr, "usage: %s --out-json PATH [--max-evals N] [--seed N] [--base HEX16]\n", argv[0]);
            return 2;
        }
    }
    if (!out_json) {
        fprintf(stderr, "missing --out-json\n");
        return 2;
    }
    if (!parse_hex_seed(base_hex, base)) {
        fprintf(stderr, "invalid base hex\n");
        return 2;
    }

    rng = seed ? seed : DEFAULT_SEED;
    evaluate_entry(base, &best);
    insert_pool(&pool, &best);

    while (eval_count < max_evals) {
        const Entry *source = &pool.entries[rand_range(&rng, (int)pool.len)];
        uint8_t cand[PREFIX_LEN];
        Entry trial;
        memcpy(cand, source->cand, PREFIX_LEN);
        mutate_candidate(cand, &rng);
        evaluate_entry(cand, &trial);
        eval_count += 1;
        if (trial.exact_prefix_len >= 4) {
            insert_pool(&pool, &trial);
            sort_pool(&pool);
            if (better_entry(&trial, &best)) {
                best = trial;
            }
        }
    }

    sort_pool(&pool);
    {
        char cand_hex[PREFIX_LEN * 2 + 1];
        char raw_hex[PREFIX_BYTES * 2 + 1];
        bytes_to_hex(best.cand, PREFIX_LEN, cand_hex);
        bytes_to_hex(best.lhs, PREFIX_BYTES, raw_hex);
        printf(
            "FINAL exact=%d dist6=%d dist10=%d cand8=%s raw=%s\n",
            best.exact_prefix_len,
            best.distance6,
            best.distance10,
            cand_hex,
            raw_hex
        );
        if (out_json) {
            FILE *fp = fopen(out_json, "wb");
            if (!fp) {
                fprintf(stderr, "failed to open %s for writing\n", out_json);
                return 1;
            }
            fprintf(
                fp,
                "{\"base\":\"%s\",\"evaluations\":%llu,\"best\":{\"cand8_hex\":\"%s\",\"candidate_hex\":\"%s41414141414141\",\"raw\":\"%s\",\"exact\":%d,\"dist6\":%d,\"dist10\":%d}}\n",
                base_hex,
                (unsigned long long)eval_count,
                cand_hex,
                cand_hex,
                raw_hex,
                best.exact_prefix_len,
                best.distance6,
                best.distance10
            );
            fclose(fp);
        }
    }
    return 0;
}
