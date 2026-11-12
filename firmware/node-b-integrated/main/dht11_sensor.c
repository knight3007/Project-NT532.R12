#include "dht11_sensor.h"

#include <string.h>

#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

enum { DHT11_PIN = GPIO_NUM_11, DHT11_INTERVAL_MS = 2000 };

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static dht11_edge_t s_edges[DHT11_MAX_EDGES];
static size_t s_edge_count;
static bool s_capturing;
static dht11_sample_t s_sample;

// GPIO ISR service and esp_timer_get_time() are supported by ESP-IDF v5.4.
// https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/peripherals/gpio.html
// https://docs.espressif.com/projects/esp-idf/en/v5.4/esp32h2/api-reference/system/esp_timer.html
static void on_edge(void *unused)
{
    (void)unused;
    dht11_edge_t edge = {
        .at_us = (uint32_t)esp_timer_get_time(),
        .level = (uint8_t)gpio_get_level(DHT11_PIN),
    };
    portENTER_CRITICAL_ISR(&s_lock);
    if (s_capturing && s_edge_count < DHT11_MAX_EDGES) {
        s_edges[s_edge_count++] = edge;
    }
    portEXIT_CRITICAL_ISR(&s_lock);
}

static dht11_error_t read_once(int16_t *temperature, uint16_t *humidity)
{
    dht11_edge_t edges[DHT11_MAX_EDGES];
    size_t count;

    if (gpio_set_level(DHT11_PIN, 0) != ESP_OK) return DHT11_INIT_ERROR;
    // The Aosong DHT11 manual specifies 18–30 ms host low. Sleeping yields to Thread.
    vTaskDelay(pdMS_TO_TICKS(20) + 1);
    portENTER_CRITICAL(&s_lock);
    s_edge_count = 0;
    s_capturing = true;
    portEXIT_CRITICAL(&s_lock);
    if (gpio_set_level(DHT11_PIN, 1) != ESP_OK) {
        portENTER_CRITICAL(&s_lock);
        s_capturing = false;
        portEXIT_CRITICAL(&s_lock);
        return DHT11_INIT_ERROR;
    }
    // A full response is about 4 ms; this bounded wait also covers a missing sensor.
    vTaskDelay(pdMS_TO_TICKS(10) + 1);
    portENTER_CRITICAL(&s_lock);
    s_capturing = false;
    count = s_edge_count;
    memcpy(edges, s_edges, count * sizeof(edges[0]));
    portEXIT_CRITICAL(&s_lock);
    return dht11_decode_edges(edges, count, temperature, humidity);
}

static void sensor_task(void *unused)
{
    (void)unused;
    for (;;) {
        // First read is after power-up stabilization. Each later read starts
        // at least 2 s after the previous read finished, even if it timed out.
        vTaskDelay(pdMS_TO_TICKS(DHT11_INTERVAL_MS));
        int16_t temperature = 0;
        uint16_t humidity = 0;
        dht11_error_t error = read_once(&temperature, &humidity);
        dht11_sample_t sample = {
            .has_attempt = true,
            .ok = error == DHT11_OK,
            .temperature_centi = error == DHT11_OK ? temperature : 0,
            .humidity_centi = error == DHT11_OK ? humidity : 0,
            .attempt_ms = (uint64_t)esp_timer_get_time() / 1000,
            .error = error,
        };
        portENTER_CRITICAL(&s_lock);
        s_sample = sample;
        portEXIT_CRITICAL(&s_lock);
    }
}

esp_err_t dht11_sensor_start(void)
{
    // Open-drain release cannot drive DATA above the external 3.3 V pull-up.
    esp_err_t error = gpio_set_level(DHT11_PIN, 1);
    if (error == ESP_OK) {
        gpio_config_t config = {
            .pin_bit_mask = 1ULL << DHT11_PIN,
            .mode = GPIO_MODE_INPUT_OUTPUT_OD,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_ANYEDGE,
        };
        error = gpio_config(&config);
    }
    if (error == ESP_OK) error = gpio_set_level(DHT11_PIN, 1);
    if (error == ESP_OK) {
        error = gpio_install_isr_service(0);
        if (error == ESP_ERR_INVALID_STATE) error = ESP_OK; // Already installed.
    }
    if (error == ESP_OK) error = gpio_isr_handler_add(DHT11_PIN, on_edge, NULL);
    if (error == ESP_OK &&
        xTaskCreate(sensor_task, "dht11", 4096, NULL, 3, NULL) != pdPASS) {
        error = ESP_ERR_NO_MEM;
    }
    if (error != ESP_OK) {
        portENTER_CRITICAL(&s_lock);
        s_sample = (dht11_sample_t){.has_attempt = true, .error = DHT11_INIT_ERROR,
                                    .attempt_ms = (uint64_t)esp_timer_get_time() / 1000};
        portEXIT_CRITICAL(&s_lock);
    }
    return error;
}

dht11_sample_t dht11_sensor_snapshot(void)
{
    portENTER_CRITICAL(&s_lock);
    dht11_sample_t sample = s_sample;
    portEXIT_CRITICAL(&s_lock);
    return sample;
}
