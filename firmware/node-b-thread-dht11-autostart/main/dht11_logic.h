#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { DHT11_MAX_EDGES = 90 };

typedef struct {
    uint32_t at_us;
    uint8_t level;
} dht11_edge_t;

typedef enum {
    DHT11_OK,
    DHT11_TIMEOUT,
    DHT11_TIMING,
    DHT11_CHECKSUM,
    DHT11_RANGE,
    DHT11_INIT_ERROR,
} dht11_error_t;

typedef struct {
    bool has_attempt;
    bool ok;
    int16_t temperature_centi;
    uint16_t humidity_centi;
    uint64_t attempt_ms;
    dht11_error_t error;
} dht11_sample_t;

dht11_error_t dht11_decode_edges(const dht11_edge_t *edges, size_t count,
                                  int16_t *temperature_centi, uint16_t *humidity_centi);
bool dht11_status_fields(char *out, size_t capacity, const dht11_sample_t *sample,
                         uint64_t now_ms);
