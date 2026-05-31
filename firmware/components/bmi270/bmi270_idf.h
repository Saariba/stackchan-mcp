/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * ESP-IDF adapter for the Bosch BMI270 SensorAPI.
 * Provides I2C read/write/delay callbacks that bridge the platform-agnostic
 * Bosch driver to the ESP-IDF i2c_master driver.
 */
#pragma once

#include <driver/i2c_master.h>
#include "bmi2.h"
#include "bmi270.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    i2c_master_dev_handle_t dev_handle;
} bmi270_idf_intf_t;

/*
 * Initialise a bmi2_dev for I2C communication on an existing ESP-IDF master
 * bus.  Fills in the read/write/delay callbacks, allocates an I2C device
 * handle at |dev_addr| (typically 0x69 on CoreS3), and uploads the BMI270
 * standard firmware.
 *
 * On success the caller owns |*dev| and the heap-allocated intf_ptr inside
 * it; call bmi270_idf_deinit() to release both.
 */
int8_t bmi270_idf_init(struct bmi2_dev *dev,
                       i2c_master_bus_handle_t bus,
                       uint8_t dev_addr);

void bmi270_idf_deinit(struct bmi2_dev *dev);

#ifdef __cplusplus
}
#endif
