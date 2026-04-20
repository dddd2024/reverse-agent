#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PREFIX_LEN 7
#define PREFIX_BYTES 10
#define ELITE_CAPACITY 32
#define PER_BUCKET_CAP 6
#define DEFAULT_MAX_EVALS 3000000ULL
#define DEFAULT_SEED 20260420ULL
#define FAR_JUMP_INTERVAL 200000ULL
#define BUCKET_BYTES 4

typedef struct {
    uint8_t cand[PREFIX_LEN];
    uint8_t lhs[PREFIX_BYTES];
    int exact_prefix_len;
    int distance4;
    int distance6;
    int distance10;
} Entry;

typedef struct {
    Entry entries[ELITE_CAPACITY];
    size_t len;
} Pool;

typedef struct {
    Entry entry;
    const char *source_pool;
} TaggedEntry;

typedef struct {
    TaggedEntry entries[ELITE_CAPACITY];
    size_t len;
} TaggedPool;

static const uint8_t ENC_CONST[PREFIX_BYTES] = {
    0x69, 0x8b, 0x8f, 0xb1, 0x8f, 0x3b, 0x4f, 0x99, 0x61, 0x72,
};

static const uint8_t TARGET[PREFIX_BYTES] = {
    0x66, 0x00, 0x6c, 0x00, 0x61, 0x00, 0x67, 0x00, 0x7b, 0x00,
};

static const uint8_t B64_TABLE[64] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static const char *POOL_NAMES[3] = {"prefix", "dist4", "dist6"};

static const char *FIXED_SEEDS[] = {
    "6f7eebb7a23037",
    "6f9debb74a3837",
    "6f7ec7b7a228a2",
    "d67eebb7ae7337",
    "6f7eeb6d863090",
    "6f93e0cfa23037",
    "6f7eeb5dad4237",
    "017eebb7043021",
    "6f7eeb76a24754",
    "163febb7a24737",
    "6f99ebb7cd30d0",
    "6f7eebb92f30a1",
};

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

static uint32_t bucket_key(const Entry *entry) {
    return ((uint32_t)entry->lhs[0] << 24)
        | ((uint32_t)entry->lhs[1] << 16)
        | ((uint32_t)entry->lhs[2] << 8)
        | (uint32_t)entry->lhs[3];
}

static void build_key72(const uint8_t prefix7[PREFIX_LEN], uint8_t key[72]) {
    uint8_t candidate[13];
    uint8_t expanded[26];
    uint8_t raw[52];
    uint8_t b64[72];
    size_t i = 0;
    size_t out = 0;

    memcpy(candidate, prefix7, PREFIX_LEN);
    memset(candidate + PREFIX_LEN, 0x41, 6);

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

static void decrypt_prefix10(const uint8_t prefix7[PREFIX_LEN], uint8_t out[PREFIX_BYTES]) {
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

static void evaluate_entry(const uint8_t cand[PREFIX_LEN], Entry *entry) {
    memcpy(entry->cand, cand, PREFIX_LEN);
    decrypt_prefix10(cand, entry->lhs);
    entry->exact_prefix_len = exact_prefix_len(entry->lhs);
    entry->distance4 = distance4(entry->lhs);
    entry->distance6 = distance6(entry->lhs);
    entry->distance10 = distance10(entry->lhs);
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

static int same_lhs_prefix(const Entry *a, const Entry *b) {
    return memcmp(a->lhs, b->lhs, PREFIX_BYTES) == 0;
}

static int same_candidate(const Entry *a, const Entry *b) {
    return memcmp(a->cand, b->cand, PREFIX_LEN) == 0;
}

static int compare_candidate_lex(const Entry *a, const Entry *b) {
    int cmp = memcmp(a->cand, b->cand, PREFIX_LEN);
    if (cmp < 0) {
        return 1;
    }
    if (cmp > 0) {
        return -1;
    }
    return 0;
}

static int compare_prefix_mode(const Entry *a, const Entry *b) {
    if (a->exact_prefix_len != b->exact_prefix_len) {
        return a->exact_prefix_len > b->exact_prefix_len ? 1 : -1;
    }
    if (a->distance10 != b->distance10) {
        return a->distance10 < b->distance10 ? 1 : -1;
    }
    if (a->distance4 != b->distance4) {
        return a->distance4 < b->distance4 ? 1 : -1;
    }
    return compare_candidate_lex(a, b);
}

static int compare_dist4_mode(const Entry *a, const Entry *b) {
    if (a->distance4 != b->distance4) {
        return a->distance4 < b->distance4 ? 1 : -1;
    }
    if (a->exact_prefix_len != b->exact_prefix_len) {
        return a->exact_prefix_len > b->exact_prefix_len ? 1 : -1;
    }
    return compare_candidate_lex(a, b);
}

static int compare_dist6_mode(const Entry *a, const Entry *b) {
    if (a->distance6 != b->distance6) {
        return a->distance6 < b->distance6 ? 1 : -1;
    }
    if (a->exact_prefix_len != b->exact_prefix_len) {
        return a->exact_prefix_len > b->exact_prefix_len ? 1 : -1;
    }
    if (a->distance4 != b->distance4) {
        return a->distance4 < b->distance4 ? 1 : -1;
    }
    return compare_candidate_lex(a, b);
}

static int better_for_mode(const Entry *a, const Entry *b, int mode) {
    if (mode == 0) {
        return compare_prefix_mode(a, b) > 0;
    }
    if (mode == 1) {
        return compare_dist4_mode(a, b) > 0;
    }
    return compare_dist6_mode(a, b) > 0;
}

static void sort_pool(Pool *pool, int mode) {
    size_t i;
    size_t j;
    for (i = 0; i < pool->len; ++i) {
        for (j = i + 1; j < pool->len; ++j) {
            if (better_for_mode(&pool->entries[j], &pool->entries[i], mode)) {
                Entry tmp = pool->entries[i];
                pool->entries[i] = pool->entries[j];
                pool->entries[j] = tmp;
            }
        }
    }
}

static void dedupe_pool(Pool *pool, int mode) {
    Pool deduped;
    uint32_t bucket_keys[ELITE_CAPACITY];
    int bucket_counts[ELITE_CAPACITY];
    size_t bucket_len = 0;
    size_t i;
    deduped.len = 0;
    sort_pool(pool, mode);
    for (i = 0; i < pool->len; ++i) {
        size_t j;
        int duplicate = 0;
        uint32_t key = bucket_key(&pool->entries[i]);
        int *bucket_count_ptr = NULL;
        for (j = 0; j < deduped.len; ++j) {
            if (same_lhs_prefix(&pool->entries[i], &deduped.entries[j])) {
                duplicate = 1;
                break;
            }
        }
        if (duplicate) {
            continue;
        }
        for (j = 0; j < bucket_len; ++j) {
            if (bucket_keys[j] == key) {
                bucket_count_ptr = &bucket_counts[j];
                break;
            }
        }
        if (!bucket_count_ptr && bucket_len < ELITE_CAPACITY) {
            bucket_keys[bucket_len] = key;
            bucket_counts[bucket_len] = 0;
            bucket_count_ptr = &bucket_counts[bucket_len];
            bucket_len += 1;
        }
        if (bucket_count_ptr && *bucket_count_ptr >= PER_BUCKET_CAP) {
            continue;
        }
        deduped.entries[deduped.len++] = pool->entries[i];
        if (bucket_count_ptr) {
            *bucket_count_ptr += 1;
        }
        if (deduped.len >= ELITE_CAPACITY) {
            break;
        }
    }
    *pool = deduped;
}

static void insert_entry(Pool *pool, const Entry *entry, int mode) {
    size_t i;
    for (i = 0; i < pool->len; ++i) {
        if (same_lhs_prefix(&pool->entries[i], entry) || same_candidate(&pool->entries[i], entry)) {
            if (better_for_mode(entry, &pool->entries[i], mode)) {
                pool->entries[i] = *entry;
                sort_pool(pool, mode);
            }
            return;
        }
    }
    if (pool->len < ELITE_CAPACITY) {
        pool->entries[pool->len++] = *entry;
        sort_pool(pool, mode);
        return;
    }
    if (better_for_mode(entry, &pool->entries[pool->len - 1], mode)) {
        pool->entries[pool->len - 1] = *entry;
        sort_pool(pool, mode);
    }
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

static void record_entry(const Entry *entry, Pool pools[3]) {
    insert_entry(&pools[0], entry, 0);
    insert_entry(&pools[1], entry, 1);
    insert_entry(&pools[2], entry, 2);
}

static int weak_progress_hit(const Pool pools[3]) {
    size_t mode;
    size_t idx;
    for (mode = 0; mode < 3; ++mode) {
        for (idx = 0; idx < pools[mode].len; ++idx) {
            const Entry *entry = &pools[mode].entries[idx];
            if (
                entry->exact_prefix_len >= 3 ||
                entry->distance4 <= 3 ||
                entry->distance6 <= 330
            ) {
                return 1;
            }
        }
    }
    return 0;
}

static int strong_success_hit(const Pool pools[3]) {
    size_t mode;
    size_t idx;
    for (mode = 0; mode < 3; ++mode) {
        for (idx = 0; idx < pools[mode].len; ++idx) {
            const Entry *entry = &pools[mode].entries[idx];
            if (
                entry->exact_prefix_len >= 4 ||
                entry->distance4 <= 2 ||
                memcmp(entry->lhs, TARGET, BUCKET_BYTES) == 0
            ) {
                return 1;
            }
        }
    }
    return 0;
}

static const Entry *pick_entry_bucketed(const Pool *pool, uint64_t *rng) {
    uint32_t keys[ELITE_CAPACITY];
    size_t indices[ELITE_CAPACITY];
    size_t key_count = 0;
    size_t idx;
    if (pool->len == 0) {
        return NULL;
    }
    for (idx = 0; idx < pool->len; ++idx) {
        uint32_t key = bucket_key(&pool->entries[idx]);
        size_t j;
        int seen = 0;
        for (j = 0; j < key_count; ++j) {
            if (keys[j] == key) {
                seen = 1;
                break;
            }
        }
        if (!seen && key_count < ELITE_CAPACITY) {
            keys[key_count] = key;
            indices[key_count] = idx;
            key_count += 1;
        }
    }
    if (key_count == 0) {
        return &pool->entries[rand_range(rng, (int)pool->len)];
    }
    {
        uint32_t chosen_key = keys[rand_range(rng, (int)key_count)];
        size_t matching[ELITE_CAPACITY];
        size_t matching_len = 0;
        for (idx = 0; idx < pool->len; ++idx) {
            if (bucket_key(&pool->entries[idx]) == chosen_key) {
                matching[matching_len++] = idx;
            }
        }
        if (matching_len == 0) {
            return &pool->entries[rand_range(rng, (int)pool->len)];
        }
        return &pool->entries[matching[rand_range(rng, (int)matching_len)]];
    }
}

static void randomize_bytes(uint8_t out[PREFIX_LEN], uint64_t *rng, int min_changes, int max_changes) {
    int used[PREFIX_LEN] = {0};
    int changes = min_changes + rand_range(rng, max_changes - min_changes + 1);
    int idx;
    for (idx = 0; idx < changes; ++idx) {
        int pos = rand_range(rng, PREFIX_LEN);
        while (used[pos]) {
            pos = rand_range(rng, PREFIX_LEN);
        }
        used[pos] = 1;
        out[pos] = (uint8_t)(1 + rand_range(rng, 255));
    }
}

static void mutate_candidate(
    uint8_t out[PREFIX_LEN],
    const Pool pools[3],
    uint64_t *rng,
    int min_changes,
    int max_changes
) {
    const Entry *base = pick_entry_bucketed(&pools[rand_range(rng, 3)], rng);
    int idx;
    if (base) {
        memcpy(out, base->cand, PREFIX_LEN);
    } else {
        for (idx = 0; idx < PREFIX_LEN; ++idx) {
            out[idx] = (uint8_t)(1 + rand_range(rng, 255));
        }
    }
    randomize_bytes(out, rng, min_changes, max_changes);
}

static void crossover_candidate(
    uint8_t out[PREFIX_LEN],
    const Pool pools[3],
    uint64_t *rng
) {
    const Entry *a = pick_entry_bucketed(&pools[rand_range(rng, 3)], rng);
    const Entry *b = pick_entry_bucketed(&pools[rand_range(rng, 3)], rng);
    int idx;
    int differ = 0;
    if (!a || !b) {
        mutate_candidate(out, pools, rng, 3, 5);
        return;
    }
    for (idx = 0; idx < PREFIX_LEN; ++idx) {
        out[idx] = (rand_range(rng, 2) == 0) ? a->cand[idx] : b->cand[idx];
        if (a->cand[idx] != b->cand[idx]) {
            differ = 1;
        }
    }
    if (!differ) {
        randomize_bytes(out, rng, 2, 3);
    }
}

static void top_candidate_positions(const Entry *entry, int positions[3], int *count) {
    int scores[PREFIX_LEN];
    int idx;
    int chosen = 0;
    for (idx = 0; idx < PREFIX_LEN; ++idx) {
        int first = idx < PREFIX_BYTES ? abs((int)entry->lhs[idx] - (int)TARGET[idx]) : 0;
        int second = (idx + 1) < PREFIX_BYTES ? abs((int)entry->lhs[idx + 1] - (int)TARGET[idx + 1]) : 0;
        scores[idx] = first + second;
    }
    while (chosen < 3) {
        int best_pos = -1;
        int best_score = -1;
        for (idx = 0; idx < PREFIX_LEN; ++idx) {
            int already = 0;
            int j;
            for (j = 0; j < chosen; ++j) {
                if (positions[j] == idx) {
                    already = 1;
                    break;
                }
            }
            if (!already && scores[idx] > best_score) {
                best_score = scores[idx];
                best_pos = idx;
            }
        }
        if (best_pos < 0) {
            break;
        }
        positions[chosen++] = best_pos;
    }
    *count = chosen;
}

static void coordinate_step(
    Pool pools[3],
    uint64_t *rng,
    int mode,
    uint64_t *eval_count,
    uint64_t max_evals
) {
    const Entry *base = pick_entry_bucketed(&pools[mode], rng);
    uint8_t cand[PREFIX_LEN];
    int positions[3];
    int pos_count = 0;
    int pi;
    if (!base || *eval_count >= max_evals) {
        return;
    }
    memcpy(cand, base->cand, PREFIX_LEN);
    top_candidate_positions(base, positions, &pos_count);
    for (pi = 0; pi < pos_count && *eval_count < max_evals; ++pi) {
        int pos = positions[pi];
        uint8_t original = cand[pos];
        Entry best_local = *base;
        int value;
        for (value = 1; value <= 255 && *eval_count < max_evals; ++value) {
            Entry trial;
            cand[pos] = (uint8_t)value;
            evaluate_entry(cand, &trial);
            record_entry(&trial, pools);
            *eval_count += 1;
            if (better_for_mode(&trial, &best_local, mode)) {
                best_local = trial;
            }
        }
        memcpy(cand, best_local.cand, PREFIX_LEN);
        cand[pos] = best_local.cand[pos];
        if (best_local.cand[pos] == original) {
            cand[pos] = original;
        }
    }
}

static void far_jump_step(Pool pools[3], uint64_t *rng, uint64_t *eval_count, uint64_t max_evals) {
    int mode;
    for (mode = 0; mode < 3 && *eval_count < max_evals; ++mode) {
        const Entry *base = pick_entry_bucketed(&pools[mode], rng);
        uint8_t cand[PREFIX_LEN];
        Entry trial;
        if (!base) {
            continue;
        }
        memcpy(cand, base->cand, PREFIX_LEN);
        randomize_bytes(cand, rng, 5, 7);
        evaluate_entry(cand, &trial);
        record_entry(&trial, pools);
        *eval_count += 1;
    }
}

static void print_entry_summary(const char *label, const Entry *entry) {
    char cand_hex[PREFIX_LEN * 2 + 1];
    char lhs_hex[PREFIX_BYTES * 2 + 1];
    bytes_to_hex(entry->cand, PREFIX_LEN, cand_hex);
    bytes_to_hex(entry->lhs, PREFIX_BYTES, lhs_hex);
    printf(
        "%s cand7=%s lhs=%s exact=%d dist4=%d dist6=%d dist10=%d\n",
        label,
        cand_hex,
        lhs_hex,
        entry->exact_prefix_len,
        entry->distance4,
        entry->distance6,
        entry->distance10
    );
}

static void write_entry_json(FILE *fp, const Entry *entry, const char *source_pool) {
    char cand_hex[PREFIX_LEN * 2 + 1];
    char lhs_hex[PREFIX_BYTES * 2 + 1];
    bytes_to_hex(entry->cand, PREFIX_LEN, cand_hex);
    bytes_to_hex(entry->lhs, PREFIX_BYTES, lhs_hex);
    fprintf(
        fp,
        "{\"cand7_hex\":\"%s\",\"candidate_hex\":\"%s414141414141\",\"lhs_prefix_hex\":\"%s\","
        "\"exact_prefix_len\":%d,\"distance4\":%d,\"distance6\":%d,\"distance10\":%d",
        cand_hex,
        cand_hex,
        lhs_hex,
        entry->exact_prefix_len,
        entry->distance4,
        entry->distance6,
        entry->distance10
    );
    if (source_pool) {
        fprintf(fp, ",\"source_pool\":\"%s\"", source_pool);
    }
    fprintf(fp, "}");
}

static void insert_tagged_entry(TaggedPool *pool, const Entry *entry, const char *source_pool) {
    size_t i;
    for (i = 0; i < pool->len; ++i) {
        if (same_lhs_prefix(&pool->entries[i].entry, entry) || same_candidate(&pool->entries[i].entry, entry)) {
            if (compare_prefix_mode(entry, &pool->entries[i].entry) > 0) {
                pool->entries[i].entry = *entry;
                pool->entries[i].source_pool = source_pool;
            }
            return;
        }
    }
    if (pool->len < ELITE_CAPACITY) {
        pool->entries[pool->len].entry = *entry;
        pool->entries[pool->len].source_pool = source_pool;
        pool->len += 1;
    }
}

static void build_elite_union(const Pool pools[3], TaggedPool *elite_union) {
    size_t mode;
    size_t idx;
    elite_union->len = 0;
    for (mode = 0; mode < 3; ++mode) {
        for (idx = 0; idx < pools[mode].len; ++idx) {
            insert_tagged_entry(elite_union, &pools[mode].entries[idx], POOL_NAMES[mode]);
        }
    }
}

static void sort_tagged_pool(TaggedPool *pool) {
    size_t i;
    size_t j;
    for (i = 0; i < pool->len; ++i) {
        for (j = i + 1; j < pool->len; ++j) {
            if (compare_prefix_mode(&pool->entries[j].entry, &pool->entries[i].entry) > 0) {
                TaggedEntry tmp = pool->entries[i];
                pool->entries[i] = pool->entries[j];
                pool->entries[j] = tmp;
            }
        }
    }
}

static int write_json_result(
    const char *out_path,
    const Pool pools[3],
    uint64_t eval_count,
    uint64_t seed
) {
    FILE *fp;
    TaggedPool elite_union;
    size_t idx;
    int success;
    build_elite_union(pools, &elite_union);
    sort_tagged_pool(&elite_union);
    success = strong_success_hit(pools);
    fp = fopen(out_path, "wb");
    if (!fp) {
        fprintf(stderr, "failed to open %s for writing\n", out_path);
        return 0;
    }

    fprintf(fp, "{\n");
    fprintf(fp, "  \"success\": %s,\n", success ? "true" : "false");
    fprintf(fp, "  \"summary\": \"samplereverse global optimizer completed.\",\n");
    fprintf(fp, "  \"seed\": %llu,\n", (unsigned long long)seed);
    fprintf(fp, "  \"evaluations\": %llu,\n", (unsigned long long)eval_count);
    fprintf(fp, "  \"best_prefix\": ");
    write_entry_json(fp, &pools[0].entries[0], "prefix");
    fprintf(fp, ",\n");
    fprintf(fp, "  \"best_dist4\": ");
    write_entry_json(fp, &pools[1].entries[0], "dist4");
    fprintf(fp, ",\n");
    fprintf(fp, "  \"best_dist6\": ");
    write_entry_json(fp, &pools[2].entries[0], "dist6");
    fprintf(fp, ",\n");
    fprintf(fp, "  \"elite_prefixes\": [\n");
    for (idx = 0; idx < elite_union.len; ++idx) {
        fprintf(fp, "    ");
        write_entry_json(fp, &elite_union.entries[idx].entry, elite_union.entries[idx].source_pool);
        fprintf(fp, "%s\n", idx + 1 < elite_union.len ? "," : "");
    }
    fprintf(fp, "  ]\n");
    fprintf(fp, "}\n");
    fclose(fp);
    return 1;
}

int main(int argc, char **argv) {
    const char *out_json = NULL;
    uint64_t max_evals = DEFAULT_MAX_EVALS;
    uint64_t seed = DEFAULT_SEED;
    uint64_t rng = DEFAULT_SEED;
    Pool pools[3] = {{{0}}, {{0}}, {{0}}};
    uint64_t eval_count = 0;
    uint64_t next_dedupe = 1000000ULL;
    uint64_t next_far_jump = FAR_JUMP_INTERVAL;
    int weak_logged = 0;
    int i;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--out-json") == 0 && i + 1 < argc) {
            out_json = argv[++i];
        } else if (strcmp(argv[i], "--max-evals") == 0 && i + 1 < argc) {
            max_evals = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            seed = strtoull(argv[++i], NULL, 10);
        } else {
            fprintf(stderr, "usage: %s --out-json PATH [--max-evals N] [--seed N]\n", argv[0]);
            return 2;
        }
    }
    if (!out_json) {
        fprintf(stderr, "missing --out-json\n");
        return 2;
    }

    rng = seed ? seed : DEFAULT_SEED;

    for (i = 0; i < (int)(sizeof(FIXED_SEEDS) / sizeof(FIXED_SEEDS[0])); ++i) {
        uint8_t cand[PREFIX_LEN];
        Entry entry;
        if (!parse_hex_seed(FIXED_SEEDS[i], cand)) {
            continue;
        }
        evaluate_entry(cand, &entry);
        record_entry(&entry, pools);
        eval_count += 1;
    }
    for (i = 0; i < 64; ++i) {
        uint8_t cand[PREFIX_LEN];
        Entry entry;
        int idx;
        for (idx = 0; idx < PREFIX_LEN; ++idx) {
            cand[idx] = (uint8_t)(1 + rand_range(&rng, 255));
        }
        evaluate_entry(cand, &entry);
        record_entry(&entry, pools);
        eval_count += 1;
    }

    while (eval_count < max_evals) {
        int exploration_phase = eval_count < (max_evals * 2ULL) / 5ULL;
        int op = rand_range(&rng, 100);

        if (eval_count >= next_far_jump) {
            far_jump_step(pools, &rng, &eval_count, max_evals);
            next_far_jump += FAR_JUMP_INTERVAL;
        }

        if (exploration_phase) {
            if (op < 50) {
                uint8_t cand[PREFIX_LEN];
                Entry entry;
                mutate_candidate(cand, pools, &rng, 3, 5);
                evaluate_entry(cand, &entry);
                record_entry(&entry, pools);
                eval_count += 1;
            } else if (op < 80) {
                uint8_t cand[PREFIX_LEN];
                Entry entry;
                crossover_candidate(cand, pools, &rng);
                evaluate_entry(cand, &entry);
                record_entry(&entry, pools);
                eval_count += 1;
            } else {
                coordinate_step(pools, &rng, rand_range(&rng, 3), &eval_count, max_evals);
            }
        } else {
            if (op < 35) {
                uint8_t cand[PREFIX_LEN];
                Entry entry;
                mutate_candidate(cand, pools, &rng, 2, 4);
                evaluate_entry(cand, &entry);
                record_entry(&entry, pools);
                eval_count += 1;
            } else if (op < 60) {
                uint8_t cand[PREFIX_LEN];
                Entry entry;
                crossover_candidate(cand, pools, &rng);
                evaluate_entry(cand, &entry);
                record_entry(&entry, pools);
                eval_count += 1;
            } else {
                coordinate_step(pools, &rng, rand_range(&rng, 3), &eval_count, max_evals);
            }
        }

        if (!weak_logged && weak_progress_hit(pools)) {
            puts("threshold_hit=1");
            print_entry_summary("threshold_best_prefix", &pools[0].entries[0]);
            print_entry_summary("threshold_best_dist4", &pools[1].entries[0]);
            print_entry_summary("threshold_best_dist6", &pools[2].entries[0]);
            fflush(stdout);
            weak_logged = 1;
        }

        if (eval_count >= next_dedupe) {
            dedupe_pool(&pools[0], 0);
            dedupe_pool(&pools[1], 1);
            dedupe_pool(&pools[2], 2);
            printf("checkpoint evals=%llu\n", (unsigned long long)eval_count);
            print_entry_summary("best_prefix", &pools[0].entries[0]);
            print_entry_summary("best_dist4", &pools[1].entries[0]);
            print_entry_summary("best_dist6", &pools[2].entries[0]);
            fflush(stdout);
            next_dedupe += 1000000ULL;
        }
    }

    dedupe_pool(&pools[0], 0);
    dedupe_pool(&pools[1], 1);
    dedupe_pool(&pools[2], 2);
    print_entry_summary("final_best_prefix", &pools[0].entries[0]);
    print_entry_summary("final_best_dist4", &pools[1].entries[0]);
    print_entry_summary("final_best_dist6", &pools[2].entries[0]);
    fflush(stdout);

    if (!write_json_result(out_json, pools, eval_count, seed)) {
        return 1;
    }
    return 0;
}
