#pragma once

// Board wiring and measured alarm criteria must be checked before changing these.
enum {
    MQ2_AO_GPIO = 1,
    MQ2_DO_GPIO = 12,
    MQ2_DO_ENABLED = 0,
    MQ2_DO_ACTIVE_LEVEL = 0,
    MQ2_DO_DEBOUNCE_SAMPLES = 3,
    MQ2_ADC_SAMPLES = 8,
    MQ2_SAMPLE_PERIOD_MS = 1000,
    MQ2_WARMUP_MS = 300000,
    MQ2_MAX_SAMPLE_AGE_MS = 3000,
    MQ2_ALARM_THRESHOLD_RAW = -1, // Unknown until measured; alarm remains null.
    MQ2_ALARM_HYSTERESIS_RAW = 0,
};

// adc_mv is the calibrated voltage at GPIO1, after any external AO divider.
// ESP32-H2 GPIO1 is ADC1_CH0. Attenuation and width configure ADC scaling;
// they do not protect the GPIO from an overvoltage.
#define MQ2_ADC_ATTEN ADC_ATTEN_DB_12
#define MQ2_ADC_BITWIDTH ADC_BITWIDTH_12
