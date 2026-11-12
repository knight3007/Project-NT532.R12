#include "dht11_logic.h"

#include <stdio.h>

static bool within(uint32_t duration, uint32_t min, uint32_t max)
{
    return duration >= min && duration <= max;
}

static uint16_t centi_fraction(uint8_t fraction)
{
    return fraction < 10 ? (uint16_t)fraction * 10 : fraction;
}

dht11_error_t dht11_decode_edges(const dht11_edge_t *edges, size_t count,
                                  int16_t *temperature_centi, uint16_t *humidity_centi)
{
    if (count < 83) return DHT11_TIMEOUT;

    // Aosong DHT11 manual, sections 5.2–5.3: 83/87 us response, then 40 data bits.
    // https://www.aosong.com/userfiles/files/media/DHT11温湿度传感器说明书（中）%20A0-1208.pdf
    for (size_t start = 0; start < 5 && start + 82 < count; ++start) {
        if (edges[start].level != 0 || edges[start + 1].level != 1 ||
            edges[start + 2].level != 0 ||
            !within(edges[start + 1].at_us - edges[start].at_us, 60, 110) ||
            !within(edges[start + 2].at_us - edges[start + 1].at_us, 60, 110)) {
            continue;
        }

        uint8_t bytes[5] = {0};
        size_t base = start + 2;
        for (size_t bit = 0; bit < 40; ++bit) {
            const dht11_edge_t *low_start = &edges[base + 2 * bit];
            const dht11_edge_t *rise = &edges[base + 2 * bit + 1];
            const dht11_edge_t *fall = &edges[base + 2 * bit + 2];
            uint32_t low_us = rise->at_us - low_start->at_us;
            uint32_t high_us = fall->at_us - rise->at_us;
            if (rise->level != 1 || fall->level != 0 || !within(low_us, 35, 80)) {
                return DHT11_TIMING;
            }
            if (!within(high_us, 15, 45) && !within(high_us, 55, 95)) {
                return DHT11_TIMING;
            }
            bytes[bit / 8] = (uint8_t)((bytes[bit / 8] << 1) | (high_us >= 55));
        }

        uint8_t sum = (uint8_t)(bytes[0] + bytes[1] + bytes[2] + bytes[3]);
        if (sum != bytes[4]) return DHT11_CHECKSUM;
        uint8_t temp_fraction = bytes[3] & 0x7f;
        if (bytes[1] > 99 || temp_fraction > 99) return DHT11_RANGE;

        uint16_t humidity = (uint16_t)(bytes[0] * 100 + centi_fraction(bytes[1]));
        int16_t temperature = (int16_t)(bytes[2] * 100 + centi_fraction(temp_fraction));
        if (bytes[3] & 0x80) temperature = -temperature;
        if (humidity > 10000 || temperature < -4000 || temperature > 8000) return DHT11_RANGE;

        *temperature_centi = temperature;
        *humidity_centi = humidity;
        return DHT11_OK;
    }
    return DHT11_TIMING;
}

static const char *error_name(dht11_error_t error)
{
    switch (error) {
    case DHT11_TIMEOUT: return "timeout";
    case DHT11_TIMING: return "timing";
    case DHT11_CHECKSUM: return "checksum";
    case DHT11_RANGE: return "range";
    case DHT11_INIT_ERROR: return "init";
    default: return "not_ready";
    }
}

bool dht11_status_fields(char *out, size_t capacity, const dht11_sample_t *sample,
                         uint64_t now_ms)
{
    char age[24] = "null";
    if (sample->has_attempt && now_ms >= sample->attempt_ms) {
        snprintf(age, sizeof(age), "%llu", (unsigned long long)(now_ms - sample->attempt_ms));
    }
    int written;
    if (sample->has_attempt && sample->ok) {
        int temp = sample->temperature_centi;
        if (temp < 0) temp = -temp;
        written = snprintf(out, capacity,
                           ",\"sensor_ok\":true,\"temperature_c\":%s%d.%02d,"
                           "\"humidity_pct\":%u.%02u,\"sample_age_ms\":%s,"
                           "\"last_attempt_age_ms\":%s,\"sensor_error\":null",
                           sample->temperature_centi < 0 ? "-" : "", temp / 100, temp % 100,
                           sample->humidity_centi / 100, sample->humidity_centi % 100, age, age);
    } else {
        written = snprintf(out, capacity,
                           ",\"sensor_ok\":false,\"temperature_c\":null,"
                           "\"humidity_pct\":null,\"sample_age_ms\":null,"
                           "\"last_attempt_age_ms\":%s,\"sensor_error\":\"%s\"",
                           age, sample->has_attempt ? error_name(sample->error) : "not_ready");
    }
    return written >= 0 && (size_t)written < capacity;
}
