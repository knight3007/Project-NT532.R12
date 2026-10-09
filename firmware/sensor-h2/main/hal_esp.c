#include "hal_esp.h"

#include <math.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/ledc.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "hal";

#define SERVO_FREQ_HZ 50
#define SERVO_RES LEDC_TIMER_14_BIT
#define SERVO_PERIOD_US (1000000 / SERVO_FREQ_HZ)
#define SERVO_MAX_DUTY ((1u << 14) - 1)

static node_cfg_t s_cfg;
static adc_oneshot_unit_handle_t s_adc;
static adc_channel_t s_adc_ch;
static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_sht;

static void out_low(int pin)
{
    gpio_reset_pin(pin);
    gpio_set_level(pin, 0);                  // đặt mức trước khi thành output để không có xung nhọn
    gpio_set_direction(pin, GPIO_MODE_OUTPUT);
    gpio_pulldown_en(pin);
}

void hal_safe_gpio_init(void)
{
    out_low(CONFIG_NT532_GPIO_PUMP);
    out_low(CONFIG_NT532_GPIO_LASER);
    out_low(CONFIG_NT532_GPIO_BUZZER);
}

// --- servo --------------------------------------------------------------------------------

static float clampf(float v, float lo, float hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

static uint32_t angle_to_duty(float deg, float offset, bool inv)
{
    float a = clampf((inv ? -deg : deg) + offset, -90.0f, 90.0f);
    float us = s_cfg.pulse_min_us + (a + 90.0f) / 180.0f * (s_cfg.pulse_max_us - s_cfg.pulse_min_us);
    return (uint32_t)lroundf(us / SERVO_PERIOD_US * SERVO_MAX_DUTY);
}

static void servo_init(void)
{
    ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,  // H2 chỉ có low-speed
        .duty_resolution = SERVO_RES,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = SERVO_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    const int pins[2] = {CONFIG_NT532_GPIO_SERVO_PAN, CONFIG_NT532_GPIO_SERVO_TILT};
    for (int i = 0; i < 2; i++) {
        ledc_channel_config_t ch = {
            .gpio_num = pins[i],
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = (ledc_channel_t)(LEDC_CHANNEL_0 + i),
            .timer_sel = LEDC_TIMER_0,
            .intr_type = LEDC_INTR_DISABLE,
            .duty = 0,
            .hpoint = 0,
        };
        ESP_ERROR_CHECK(ledc_channel_config(&ch));
    }
    hal_servo_set(0, 0);
}

void hal_servo_set(float pan, float tilt)
{
    pan = clampf(pan, s_cfg.pan_min, s_cfg.pan_max);
    tilt = clampf(tilt, s_cfg.tilt_min, s_cfg.tilt_max);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, angle_to_duty(pan, s_cfg.pan_off, s_cfg.pan_inv));
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, angle_to_duty(tilt, s_cfg.tilt_off, s_cfg.tilt_inv));
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1);
}

// --- bơm, laser, còi --------------------------------------------------------------------------

void hal_dev_set(act_dev_t dev, bool on)
{
    gpio_set_level(dev == ACT_DEV_PUMP ? CONFIG_NT532_GPIO_PUMP : CONFIG_NT532_GPIO_LASER, on ? 1 : 0);
}

void hal_buzzer_set(bool on)
{
    gpio_set_level(CONFIG_NT532_GPIO_BUZZER, on ? 1 : 0);
}

// --- MQ-2 -----------------------------------------------------------------------------------

static void adc_init(void)
{
    adc_unit_t unit;
    ESP_ERROR_CHECK(adc_oneshot_io_to_channel(CONFIG_NT532_GPIO_MQ2, &unit, &s_adc_ch));
    if (unit != ADC_UNIT_1) {
        ESP_LOGE(TAG, "GPIO%d không thuộc ADC1", CONFIG_NT532_GPIO_MQ2);
        abort();
    }
    adc_oneshot_unit_init_cfg_t uc = {.unit_id = ADC_UNIT_1};
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&uc, &s_adc));
    adc_oneshot_chan_cfg_t cc = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, s_adc_ch, &cc));
}

float hal_gas_read(void)
{
    int sum = 0, n = 0;
    for (int i = 0; i < 8; i++) {
        int raw;
        if (adc_oneshot_read(s_adc, s_adc_ch, &raw) == ESP_OK) {
            sum += raw;
            n++;
        }
    }
    return n ? (float)sum / n : 0.0f;
}

// --- SHT31 ----------------------------------------------------------------------------------

static void i2c_init(void)
{
    i2c_master_bus_config_t bc = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = CONFIG_NT532_GPIO_I2C_SDA,
        .scl_io_num = CONFIG_NT532_GPIO_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,  // thêm điện trở kéo lên ngoài nếu dây dài
    };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bc, &s_bus));
    i2c_device_config_t dc = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = 0x44,
        .scl_speed_hz = 100000,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(s_bus, &dc, &s_sht));
}

static uint8_t crc8(const uint8_t *d, int n)
{
    uint8_t crc = 0xFF;
    for (int i = 0; i < n; i++) {
        crc ^= d[i];
        for (int b = 0; b < 8; b++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}

bool hal_sht31_read(float *t_c, float *h_pct)
{
    static int fails;
    const uint8_t cmd[2] = {0x24, 0x00};  // single shot, high repeatability, không clock stretching
    uint8_t rx[6];
    esp_err_t e = i2c_master_transmit(s_sht, cmd, sizeof(cmd), 100);
    if (e == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(20));  // đo tối đa 15 ms
        e = i2c_master_receive(s_sht, rx, sizeof(rx), 100);
    }
    if (e == ESP_OK && (crc8(rx, 2) != rx[2] || crc8(rx + 3, 2) != rx[5])) {
        e = ESP_ERR_INVALID_CRC;
    }
    if (e != ESP_OK) {
        if (++fails % 5 == 0) {
            i2c_master_bus_reset(s_bus);  // CHƯA KIỂM: có gỡ được bus bị treo hay không
        }
        ESP_LOGW(TAG, "SHT31 lỗi %s (liên tiếp %d), giữ số cũ", esp_err_to_name(e), fails);
        return false;
    }
    fails = 0;
    uint16_t rt = (uint16_t)((rx[0] << 8) | rx[1]);
    uint16_t rh = (uint16_t)((rx[3] << 8) | rx[4]);
    *t_c = -45.0f + 175.0f * rt / 65535.0f;
    *h_pct = 100.0f * rh / 65535.0f;
    return true;
}

void hal_init(const node_cfg_t *cfg)
{
    s_cfg = *cfg;
    servo_init();
    adc_init();
    i2c_init();
}
