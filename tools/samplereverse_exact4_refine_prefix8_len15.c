#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PREFIX_LEN 8
#define PREFIX_BYTES 10
#define TARGET_WCHARS 5
#define POOL_CAPACITY 128
#define MAX_ANCHORS 8
#define MAX_TOP_ENTRIES 32
#define MAX_SNAPSHOTS 64
#define DEFAULT_MAX_EVALS 200000000ULL
#define DEFAULT_SEED 20260420ULL
#define DEFAULT_SNAPSHOT_INTERVAL 10000000ULL

typedef struct {
    uint8_t cand[PREFIX_LEN];
    uint8_t lhs[PREFIX_BYTES];
    uint8_t canonical[PREFIX_BYTES];
    int ci_exact_wchars;
    int ci_distance5;
    int raw_distance10;
} Entry;

typedef struct {
    Entry entries[POOL_CAPACITY];
    size_t len;
} Pool;

typedef struct {
    uint64_t evaluations;
    Entry best;
} Snapshot;

static const uint8_t ENC_CONST[PREFIX_BYTES] = {
    0x69, 0x8b, 0x8f, 0xb1, 0x8f, 0x3b, 0x4f, 0x99, 0x61, 0x72,
};

static const uint8_t TARGET[PREFIX_BYTES] = {
    0x66, 0x00, 0x6c, 0x00, 0x61, 0x00, 0x67, 0x00, 0x7b, 0x00,
};

static const uint8_t B64_TABLE[64] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static const char *DEFAULT_ANCHOR_1 = "4a78f0eaeb4f13b0";
static const char *DEFAULT_ANCHOR_2 = "e05e579fca169e80";
static const char *DEFAULT_FIXED_SUFFIX_HEX = "41414141414141";

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

static int mutable_nibble_positions(int out[15]) {
    int len = 0;
    int idx;
    for (idx = 0; idx < PREFIX_LEN * 2; ++idx) {
        if (idx == 15) {
            continue;
        }
        out[len++] = idx;
    }
    return len;
}

static uint8_t get_nibble(const uint8_t cand[PREFIX_LEN], int nibble_idx) {
    uint8_t value = cand[nibble_idx / 2];
    if ((nibble_idx & 1) == 0) {
        return (uint8_t)((value >> 4) & 0x0f);
    }
    return (uint8_t)(value & 0x0f);
}

static void set_nibble(uint8_t cand[PREFIX_LEN], int nibble_idx, uint8_t nibble_value) {
    uint8_t *byte_ptr = &cand[nibble_idx / 2];
    uint8_t value = (uint8_t)(nibble_value & 0x0f);
    if ((nibble_idx & 1) == 0) {
        *byte_ptr = (uint8_t)((*byte_ptr & 0x0f) | (value << 4));
    } else {
        *byte_ptr = (uint8_t)((*byte_ptr & 0xf0) | value);
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
        expanded[i * 2] = (uint8_t)(((candidate[i] >> 4) & 0x0f) + 0x78);
        expanded[i * 2 + 1] = (uint8_t)((candidate[i] & 0x0f) + 0x7a);
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
        key[i] = (uint8_t)(((i & 1) == 0) ? b64[i >> 1] : 0);
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
        out[idx] = (uint8_t)(ENC_CONST[idx] ^ ks);
    }
}

static void canonicalize_prefix(const uint8_t raw[PREFIX_BYTES], uint8_t canonical[PREFIX_BYTES]) {
    int idx;
    for (idx = 0; idx < PREFIX_BYTES; idx += 2) {
        canonical[idx] = (uint8_t)lower_ascii(raw[idx]);
        canonical[idx + 1] = raw[idx + 1];
    }
}

static void score_compare_prefix(const uint8_t raw[PREFIX_BYTES], Entry *entry) {
    int wchar_idx;
    int ci_exact_wchars = 0;
    int ci_distance5 = 0;
    int raw_distance10 = 0;
    for (wchar_idx = 0; wchar_idx < PREFIX_BYTES; ++wchar_idx) {
        raw_distance10 += abs((int)raw[wchar_idx] - (int)TARGET[wchar_idx]);
    }
    for (wchar_idx = 0; wchar_idx < TARGET_WCHARS; ++wchar_idx) {
        int raw_low = raw[wchar_idx * 2];
        int raw_high = raw[wchar_idx * 2 + 1];
        int target_low = TARGET[wchar_idx * 2];
        int target_high = TARGET[wchar_idx * 2 + 1];
        int matches = (raw_high == target_high) && (lower_ascii(raw_low) == lower_ascii(target_low));
        ci_distance5 += abs(raw_high - target_high) + abs(lower_ascii(raw_low) - lower_ascii(target_low));
        if (matches && wchar_idx == ci_exact_wchars) {
            ci_exact_wchars += 1;
        }
    }
    entry->ci_exact_wchars = ci_exact_wchars;
    entry->ci_distance5 = ci_distance5;
    entry->raw_distance10 = raw_distance10;
}

static void evaluate_entry(const uint8_t cand[PREFIX_LEN], Entry *entry) {
    memcpy(entry->cand, cand, PREFIX_LEN);
    decrypt_prefix10(cand, entry->lhs);
    canonicalize_prefix(entry->lhs, entry->canonical);
    score_compare_prefix(entry->lhs, entry);
}

static int better_entry(const Entry *a, const Entry *b) {
    if (a->ci_exact_wchars != b->ci_exact_wchars) {
        return a->ci_exact_wchars > b->ci_exact_wchars;
    }
    if (a->ci_distance5 != b->ci_distance5) {
        return a->ci_distance5 < b->ci_distance5;
    }
    if (a->raw_distance10 != b->raw_distance10) {
        return a->raw_distance10 < b->raw_distance10;
    }
    return memcmp(a->cand, b->cand, PREFIX_LEN) < 0;
}

static int same_candidate(const Entry *a, const Entry *b) {
    return memcmp(a->cand, b->cand, PREFIX_LEN) == 0;
}

static int same_compare_prefix(const Entry *a, const Entry *b) {
    return memcmp(a->canonical, b->canonical, PREFIX_BYTES) == 0;
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

static void insert_pool(Pool *pool, const Entry *entry) {
    size_t i;
    for (i = 0; i < pool->len; ++i) {
        if (same_candidate(&pool->entries[i], entry)) {
            if (better_entry(entry, &pool->entries[i])) {
                pool->entries[i] = *entry;
            }
            return;
        }
        if (same_compare_prefix(&pool->entries[i], entry)) {
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
    sort_pool(pool);
    if (better_entry(entry, &pool->entries[pool->len - 1])) {
        pool->entries[pool->len - 1] = *entry;
        sort_pool(pool);
    }
}

static void record_snapshot(
    Snapshot snapshots[MAX_SNAPSHOTS],
    size_t *snapshot_count,
    uint64_t evaluations,
    const Entry *best
) {
    if (*snapshot_count >= MAX_SNAPSHOTS) {
        return;
    }
    snapshots[*snapshot_count].evaluations = evaluations;
    snapshots[*snapshot_count].best = *best;
    *snapshot_count += 1;
}

static void choose_source_candidate(
    uint8_t out[PREFIX_LEN],
    const Pool *pool,
    const uint8_t anchors[MAX_ANCHORS][PREFIX_LEN],
    size_t anchor_count,
    uint64_t *rng
) {
    int roll = rand_range(rng, 100);
    int indices[POOL_CAPACITY];
    int count = 0;
    int pos;
    if (roll < 12) {
        for (pos = 0; pos < PREFIX_LEN; ++pos) {
            out[pos] = (uint8_t)rand_range(rng, 256);
        }
        out[PREFIX_LEN - 1] &= 0xf0;
        out[PREFIX_LEN - 1] |= anchors[0][PREFIX_LEN - 1] & 0x0f;
        return;
    }
    if (pool->len == 0 || (anchor_count > 0 && roll < 35)) {
        memcpy(out, anchors[rand_range(rng, (int)anchor_count)], PREFIX_LEN);
        return;
    }
    if (roll < 58) {
        for (pos = 0; pos < (int)pool->len; ++pos) {
            if (pool->entries[pos].ci_exact_wchars >= 2) {
                indices[count++] = pos;
            }
        }
        if (count > 0) {
            int top_cap = count < 8 ? count : 8;
            memcpy(out, pool->entries[indices[rand_range(rng, top_cap)]].cand, PREFIX_LEN);
            return;
        }
    }
    if (roll < 83) {
        count = 0;
        for (pos = 0; pos < (int)pool->len; ++pos) {
            if (pool->entries[pos].ci_exact_wchars == 1) {
                indices[count++] = pos;
            }
        }
        if (count > 0) {
            int top_cap = count < 12 ? count : 12;
            memcpy(out, pool->entries[indices[rand_range(rng, top_cap)]].cand, PREFIX_LEN);
            return;
        }
    }
    if (roll < 92) {
        size_t top_cap = pool->len < 16 ? pool->len : 16;
        if (top_cap > 0) {
            memcpy(out, pool->entries[rand_range(rng, (int)top_cap)].cand, PREFIX_LEN);
            return;
        }
    }
    memcpy(out, pool->entries[rand_range(rng, (int)pool->len)].cand, PREFIX_LEN);
}

static void random_remote_mutation(uint8_t cand[PREFIX_LEN], uint64_t *rng) {
    int positions[15];
    int positions_len = mutable_nibble_positions(positions);
    int used[15] = {0};
    int changes = 5 + rand_range(rng, 3);
    int idx;
    for (idx = 0; idx < changes; ++idx) {
        int choice = rand_range(rng, positions_len);
        while (used[choice]) {
            choice = rand_range(rng, positions_len);
        }
        used[choice] = 1;
        set_nibble(cand, positions[choice], (uint8_t)rand_range(rng, 16));
    }
}

static void crossover_candidate(
    uint8_t cand[PREFIX_LEN],
    const Pool *pool,
    const uint8_t anchors[MAX_ANCHORS][PREFIX_LEN],
    size_t anchor_count,
    uint64_t *rng
) {
    uint8_t parent_a[PREFIX_LEN];
    uint8_t parent_b[PREFIX_LEN];
    int blocks = 2 + rand_range(rng, 3);
    int block_idx;
    choose_source_candidate(parent_a, pool, anchors, anchor_count, rng);
    choose_source_candidate(parent_b, pool, anchors, anchor_count, rng);
    memcpy(cand, parent_a, PREFIX_LEN);
    for (block_idx = 0; block_idx < blocks; ++block_idx) {
        int start = rand_range(rng, 15);
        int length = 1 + rand_range(rng, 3);
        int offset;
        for (offset = 0; offset < length && (start + offset) < 15; ++offset) {
            set_nibble(cand, start + offset, get_nibble(parent_b, start + offset));
        }
    }
}

static void coordinate_descent_candidate(
    Entry *local_best,
    const Pool *pool,
    const uint8_t anchors[MAX_ANCHORS][PREFIX_LEN],
    size_t anchor_count,
    uint64_t *rng,
    uint64_t *eval_count,
    uint64_t max_evals
) {
    int positions[15];
    int positions_len = mutable_nibble_positions(positions);
    int rounds = 0;
    choose_source_candidate(local_best->cand, pool, anchors, anchor_count, rng);
    evaluate_entry(local_best->cand, local_best);
    while (rounds < 3 && *eval_count < max_evals) {
        Entry round_best = *local_best;
        int improved = 0;
        int pos_idx;
        for (pos_idx = 0; pos_idx < positions_len && *eval_count < max_evals; ++pos_idx) {
            int value_idx;
            int nibble_pos = positions[pos_idx];
            uint8_t original = get_nibble(local_best->cand, nibble_pos);
            for (value_idx = 0; value_idx < 16 && *eval_count < max_evals; ++value_idx) {
                Entry trial;
                uint8_t cand[PREFIX_LEN];
                if ((uint8_t)value_idx == original) {
                    continue;
                }
                memcpy(cand, local_best->cand, PREFIX_LEN);
                set_nibble(cand, nibble_pos, (uint8_t)value_idx);
                evaluate_entry(cand, &trial);
                *eval_count += 1;
                if (better_entry(&trial, &round_best)) {
                    round_best = trial;
                    improved = 1;
                }
            }
        }
        if (!improved) {
            break;
        }
        *local_best = round_best;
        rounds += 1;
    }
}

static void write_entry_json(FILE *fp, const Entry *entry) {
    char cand_hex[PREFIX_LEN * 2 + 1];
    char raw_hex[PREFIX_BYTES * 2 + 1];
    char candidate_hex[PREFIX_LEN * 2 + 14 + 1];
    bytes_to_hex(entry->cand, PREFIX_LEN, cand_hex);
    bytes_to_hex(entry->lhs, PREFIX_BYTES, raw_hex);
    snprintf(candidate_hex, sizeof(candidate_hex), "%s%s", cand_hex, DEFAULT_FIXED_SUFFIX_HEX);
    fprintf(
        fp,
        "{\"cand8_hex\":\"%s\",\"candidate_hex\":\"%s\",\"raw_prefix_hex\":\"%s\",\"ci_exact_wchars\":%d,\"ci_distance5\":%d,\"raw_distance10\":%d}",
        cand_hex,
        candidate_hex,
        raw_hex,
        entry->ci_exact_wchars,
        entry->ci_distance5,
        entry->raw_distance10
    );
}

int main(int argc, char **argv) {
    const char *out_json = NULL;
    uint64_t max_evals = DEFAULT_MAX_EVALS;
    uint64_t seed = DEFAULT_SEED;
    uint64_t rng = DEFAULT_SEED;
    uint64_t snapshot_interval = DEFAULT_SNAPSHOT_INTERVAL;
    uint8_t anchors[MAX_ANCHORS][PREFIX_LEN];
    size_t anchor_count = 0;
    Pool pool = {{{0}}, 0};
    Snapshot snapshots[MAX_SNAPSHOTS];
    size_t snapshot_count = 0;
    Entry best;
    uint64_t eval_count = 0;
    uint64_t next_snapshot = DEFAULT_SNAPSHOT_INTERVAL;
    int i;
    int have_best = 0;

    for (i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--out-json") == 0 && i + 1 < argc) {
            out_json = argv[++i];
        } else if (strcmp(argv[i], "--max-evals") == 0 && i + 1 < argc) {
            max_evals = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) {
            seed = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--snapshot-interval") == 0 && i + 1 < argc) {
            snapshot_interval = strtoull(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--anchor") == 0 && i + 1 < argc) {
            if (anchor_count >= MAX_ANCHORS) {
                fprintf(stderr, "too many anchors\n");
                return 2;
            }
            if (!parse_hex_seed(argv[++i], anchors[anchor_count])) {
                fprintf(stderr, "invalid anchor hex\n");
                return 2;
            }
            anchor_count += 1;
        } else {
            fprintf(
                stderr,
                "usage: %s --out-json PATH [--max-evals N] [--seed N] [--snapshot-interval N] [--anchor HEX16 ...]\n",
                argv[0]
            );
            return 2;
        }
    }

    if (!out_json) {
        fprintf(stderr, "missing --out-json\n");
        return 2;
    }

    if (anchor_count == 0) {
        if (!parse_hex_seed(DEFAULT_ANCHOR_1, anchors[anchor_count])) {
            fprintf(stderr, "invalid default anchor 1\n");
            return 2;
        }
        anchor_count += 1;
        if (!parse_hex_seed(DEFAULT_ANCHOR_2, anchors[anchor_count])) {
            fprintf(stderr, "invalid default anchor 2\n");
            return 2;
        }
        anchor_count += 1;
    }

    rng = seed ? seed : DEFAULT_SEED;
    if (snapshot_interval == 0) {
        snapshot_interval = DEFAULT_SNAPSHOT_INTERVAL;
    }
    next_snapshot = snapshot_interval;

    for (i = 0; i < (int)anchor_count; ++i) {
        Entry anchor_entry;
        evaluate_entry(anchors[i], &anchor_entry);
        insert_pool(&pool, &anchor_entry);
        if (!have_best || better_entry(&anchor_entry, &best)) {
            best = anchor_entry;
            have_best = 1;
        }
    }
    for (i = 0; i < 64; ++i) {
        uint8_t seed_cand[PREFIX_LEN];
        Entry seed_entry;
        choose_source_candidate(seed_cand, &pool, anchors, anchor_count, &rng);
        random_remote_mutation(seed_cand, &rng);
        evaluate_entry(seed_cand, &seed_entry);
        insert_pool(&pool, &seed_entry);
        if (!have_best || better_entry(&seed_entry, &best)) {
            best = seed_entry;
            have_best = 1;
        }
    }
    sort_pool(&pool);

    while (eval_count < max_evals) {
        int op = rand_range(&rng, 100);
        if (op < 45) {
            uint8_t cand[PREFIX_LEN];
            Entry trial;
            choose_source_candidate(cand, &pool, anchors, anchor_count, &rng);
            random_remote_mutation(cand, &rng);
            evaluate_entry(cand, &trial);
            eval_count += 1;
            insert_pool(&pool, &trial);
            if (!have_best || better_entry(&trial, &best)) {
                best = trial;
                have_best = 1;
            }
        } else if (op < 75) {
            uint8_t cand[PREFIX_LEN];
            Entry trial;
            crossover_candidate(cand, &pool, anchors, anchor_count, &rng);
            evaluate_entry(cand, &trial);
            eval_count += 1;
            insert_pool(&pool, &trial);
            if (!have_best || better_entry(&trial, &best)) {
                best = trial;
                have_best = 1;
            }
        } else {
            Entry local_best;
            coordinate_descent_candidate(&local_best, &pool, anchors, anchor_count, &rng, &eval_count, max_evals);
            insert_pool(&pool, &local_best);
            if (!have_best || better_entry(&local_best, &best)) {
                best = local_best;
                have_best = 1;
            }
        }

        if (eval_count >= next_snapshot) {
            sort_pool(&pool);
            if (have_best) {
                record_snapshot(snapshots, &snapshot_count, eval_count, &best);
            }
            next_snapshot += snapshot_interval;
        }
    }

    sort_pool(&pool);
    {
        FILE *fp = fopen(out_json, "wb");
        if (!fp) {
            fprintf(stderr, "failed to open %s for writing\n", out_json);
            return 1;
        }
        fprintf(fp, "{");
        fprintf(fp, "\"anchors\":[");
        for (i = 0; i < (int)anchor_count; ++i) {
            char anchor_hex[PREFIX_LEN * 2 + 1];
            bytes_to_hex(anchors[i], PREFIX_LEN, anchor_hex);
            fprintf(fp, "%s\"%s\"", i ? "," : "", anchor_hex);
        }
        fprintf(fp, "],");
        fprintf(fp, "\"fixed_suffix_hex\":\"%s\",", DEFAULT_FIXED_SUFFIX_HEX);
        fprintf(fp, "\"evaluations\":%llu,", (unsigned long long)eval_count);
        fprintf(fp, "\"snapshot_interval\":%llu,", (unsigned long long)snapshot_interval);
        fprintf(fp, "\"best\":");
        write_entry_json(fp, &best);
        fprintf(fp, ",\"top_entries\":[");
        for (i = 0; i < (int)pool.len && i < MAX_TOP_ENTRIES; ++i) {
            fprintf(fp, "%s", i ? "," : "");
            write_entry_json(fp, &pool.entries[i]);
        }
        fprintf(fp, "],\"snapshots\":[");
        for (i = 0; i < (int)snapshot_count; ++i) {
            fprintf(fp, "%s{\"evaluations\":%llu,\"best\":", i ? "," : "", (unsigned long long)snapshots[i].evaluations);
            write_entry_json(fp, &snapshots[i].best);
            fprintf(fp, "}");
        }
        fprintf(fp, "]}");
        fclose(fp);
    }
    return 0;
}
