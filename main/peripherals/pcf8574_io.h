#ifndef PCF8574_IO_H
#define PCF8574_IO_H

#include <stdbool.h>
#include <stdint.h>
#include <pcf8574.h>
#include "esp_err.h"

#ifndef PCF8574_I2C_ADDR
#define PCF8574_I2C_ADDR 0x27
#endif

typedef enum {
    EXT_IO_PIN_0 = 0,
    EXT_IO_PIN_1,
    EXT_IO_PIN_2,
    EXT_IO_PIN_3,
    EXT_IO_PIN_4,
    EXT_IO_PIN_5,
    EXT_IO_PIN_6,
    EXT_IO_PIN_7
} ext_io_pin_t;

extern i2c_dev_t pcf8574_dev;

esp_err_t pcf8574_init(void);
esp_err_t pcf8574_io_init(void);
esp_err_t pcf8574_write_pin(uint8_t pin, bool level);
esp_err_t pcf8574_read_pin(uint8_t pin, bool *level);
esp_err_t pcf8574_write_port(uint8_t value);
esp_err_t pcf8574_read_port(uint8_t *value);
uint8_t pcf8574_get_cached_port(void);

#endif
