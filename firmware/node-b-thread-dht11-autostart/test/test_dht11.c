#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "dht11_logic.h"

static size_t frame(const uint8_t bytes[5], dht11_edge_t edges[DHT11_MAX_EDGES])
{
    uint32_t t = 1000;
    size_t n = 0;
    edges[n++] = (dht11_edge_t){t, 1}; // Host releases DATA.
    edges[n++] = (dht11_edge_t){t += 30, 0};
    edges[n++] = (dht11_edge_t){t += 83, 1};
    edges[n++] = (dht11_edge_t){t += 87, 0};
    for (int bit = 0; bit < 40; ++bit) {
        edges[n++] = (dht11_edge_t){t += 54, 1};
        int one = (bytes[bit / 8] >> (7 - bit % 8)) & 1;
        edges[n++] = (dht11_edge_t){t += one ? 71 : 24, 0};
    }
    return n;
}

int main(void)
{
    dht11_edge_t edges[DHT11_MAX_EDGES] = {0};
    int16_t temp = 0;
    uint16_t humidity = 0;

    // The current Aosong manual's example: 52.1% RH, -24.12 C.
    const uint8_t signed_frame[5] = {0x34, 0x01, 0x18, 0x8c, 0xd9};
    size_t n = frame(signed_frame, edges);
    assert(dht11_decode_edges(edges, n, &temp, &humidity) == DHT11_OK);
    assert(temp == -2412 && humidity == 5210);

    const uint8_t normal_frame[5] = {55, 0, 24, 0, 79};
    n = frame(normal_frame, edges);
    assert(dht11_decode_edges(edges, n, &temp, &humidity) == DHT11_OK);
    assert(temp == 2400 && humidity == 5500);
    assert(dht11_decode_edges(edges, 20, &temp, &humidity) == DHT11_TIMEOUT);

    edges[2].at_us = edges[1].at_us + 10;
    assert(dht11_decode_edges(edges, n, &temp, &humidity) == DHT11_TIMING);
    n = frame(normal_frame, edges);
    edges[5].at_us = edges[4].at_us + 50;
    assert(dht11_decode_edges(edges, n, &temp, &humidity) == DHT11_TIMING);

    const uint8_t bad_checksum[5] = {55, 0, 24, 0, 80};
    n = frame(bad_checksum, edges);
    assert(dht11_decode_edges(edges, n, &temp, &humidity) == DHT11_CHECKSUM);
    const uint8_t bad_range[5] = {101, 0, 24, 0, 125};
    n = frame(bad_range, edges);
    assert(dht11_decode_edges(edges, n, &temp, &humidity) == DHT11_RANGE);

    char json[192]; // Same capacity as the CoAP handler's sensor field buffer.
    dht11_sample_t sample = {0};
    assert(dht11_status_fields(json, sizeof(json), &sample, 10000));
    assert(strstr(json, "\"sensor_ok\":false") != NULL);
    assert(strstr(json, "\"temperature_c\":null") != NULL);
    assert(strstr(json, "\"sample_age_ms\":null") != NULL);
    assert(strstr(json, "\"last_attempt_age_ms\":null") != NULL);

    sample = (dht11_sample_t){.has_attempt = true, .ok = true,
                              .temperature_centi = -2412, .humidity_centi = 5210,
                              .attempt_ms = 10000, .error = DHT11_OK};
    assert(dht11_status_fields(json, sizeof(json), &sample, 10500));
    assert(strstr(json, "\"temperature_c\":-24.12") != NULL);
    assert(strstr(json, "\"humidity_pct\":52.10") != NULL);
    assert(strstr(json, "\"sample_age_ms\":500") != NULL);
    assert(strstr(json, "\"last_attempt_age_ms\":500") != NULL);

    sample.ok = false;
    sample.error = DHT11_CHECKSUM;
    assert(dht11_status_fields(json, sizeof(json), &sample, 10600));
    assert(strstr(json, "\"sensor_ok\":false") != NULL);
    assert(strstr(json, "\"temperature_c\":null") != NULL);
    assert(strstr(json, "\"humidity_pct\":null") != NULL);
    assert(strstr(json, "\"sample_age_ms\":null") != NULL);
    assert(strstr(json, "\"last_attempt_age_ms\":600") != NULL);
    assert(strstr(json, "\"sensor_error\":\"checksum\"") != NULL);
    return 0;
}
