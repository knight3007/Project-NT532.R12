// Logic báo động: xem alarm.h.

#include "alarm.h"

#include <math.h>

void alarm_default_config(alarm_config_t *cfg)
{
    cfg->temp_c = 45.0f;
    cfg->gas = 600.0f;
    cfg->n_on = 3;
    cfg->n_off = 5;
    cfg->warmup_ms = 120000u;
}

void alarm_init(alarm_t *al, const alarm_config_t *cfg)
{
    al->cfg = *cfg;
    al->active = false;
    al->over = 0;
    al->under = 0;
}

alarm_event_t alarm_feed(alarm_t *al, uint32_t up_ms, float temp_c, float gas)
{
    // Mẫu hỏng (NaN/inf) không tính là mẫu nào: không tăng, không reset bộ đếm.
    if (!isfinite(temp_c) || !isfinite(gas)) {
        return ALARM_NONE;
    }
    bool warm = up_ms < al->cfg.warmup_ms;
    bool over = temp_c >= al->cfg.temp_c || (!warm && gas >= al->cfg.gas);

    if (over) {
        al->under = 0;
        if (al->over < 0xFFFFu) {
            al->over++;
        }
        if (!al->active && al->over >= al->cfg.n_on) {
            al->active = true;
            return ALARM_RAISE;
        }
    } else {
        al->over = 0;
        if (al->under < 0xFFFFu) {
            al->under++;
        }
        if (al->active && al->under >= al->cfg.n_off) {
            al->active = false;
            return ALARM_CLEAR;
        }
    }
    return ALARM_NONE;
}
