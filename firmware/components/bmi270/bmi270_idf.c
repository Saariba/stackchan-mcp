/*
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * ESP-IDF adapter for the Bosch BMI270 SensorAPI.
 */
#include "bmi270_idf.h"

#include <string.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <driver/i2c_master.h>

#define TAG "bmi270_idf"
#define BMI270_IDF_I2C_TIMEOUT_MS  50
#define BMI270_IDF_READ_WRITE_LEN  32
#define BMI270_IDF_INIT_RETRIES    3

static BMI2_INTF_RETURN_TYPE bmi2_idf_i2c_read(uint8_t reg_addr,
                                                uint8_t *reg_data,
                                                uint32_t len,
                                                void *intf_ptr)
{
    bmi270_idf_intf_t *ctx = (bmi270_idf_intf_t *)intf_ptr;
    esp_err_t err = i2c_master_transmit_receive(ctx->dev_handle,
                                                &reg_addr, 1,
                                                reg_data, len,
                                                BMI270_IDF_I2C_TIMEOUT_MS);
    return (err == ESP_OK) ? BMI2_INTF_RET_SUCCESS : BMI2_INTF_RET_SUCCESS + 1;
}

static BMI2_INTF_RETURN_TYPE bmi2_idf_i2c_write(uint8_t reg_addr,
                                                 const uint8_t *reg_data,
                                                 uint32_t len,
                                                 void *intf_ptr)
{
    bmi270_idf_intf_t *ctx = (bmi270_idf_intf_t *)intf_ptr;
    uint8_t buf[len + 1];
    buf[0] = reg_addr;
    memcpy(buf + 1, reg_data, len);
    esp_err_t err = i2c_master_transmit(ctx->dev_handle,
                                        buf, len + 1,
                                        BMI270_IDF_I2C_TIMEOUT_MS);
    return (err == ESP_OK) ? BMI2_INTF_RET_SUCCESS : BMI2_INTF_RET_SUCCESS + 1;
}

static void bmi2_idf_delay_us(uint32_t period, void *intf_ptr)
{
    (void)intf_ptr;
    int64_t end = esp_timer_get_time() + (int64_t)period;
    if (period >= 1000) {
        vTaskDelay(pdMS_TO_TICKS(period / 1000));
    }
    while (esp_timer_get_time() < end) {
        ;
    }
}

int8_t bmi270_idf_init(struct bmi2_dev *dev,
                       i2c_master_bus_handle_t bus,
                       uint8_t dev_addr)
{
    if (dev == NULL) {
        return BMI2_E_NULL_PTR;
    }

    bmi270_idf_intf_t *ctx = calloc(1, sizeof(bmi270_idf_intf_t));
    if (ctx == NULL) {
        return BMI2_E_NULL_PTR;
    }

    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = dev_addr,
        .scl_speed_hz = 400000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &dev_cfg, &ctx->dev_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "i2c_master_bus_add_device(0x%02X) failed: %s",
                 dev_addr, esp_err_to_name(err));
        free(ctx);
        return BMI2_E_COM_FAIL;
    }
    memset(dev, 0, sizeof(*dev));
    dev->intf = BMI2_I2C_INTF;
    dev->read = bmi2_idf_i2c_read;
    dev->write = bmi2_idf_i2c_write;
    dev->delay_us = bmi2_idf_delay_us;
    dev->intf_ptr = ctx;
    dev->read_write_len = BMI270_IDF_READ_WRITE_LEN;
    dev->config_file_ptr = NULL;

    int8_t rslt = BMI2_E_COM_FAIL;
    for (int attempt = 0; attempt < BMI270_IDF_INIT_RETRIES; attempt++) {
        rslt = bmi270_init(dev);
        if (rslt == BMI2_OK) {
            break;
        }
        ESP_LOGW(TAG, "bmi270_init attempt %d failed: %d", attempt + 1, rslt);
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (rslt != BMI2_OK) {
        ESP_LOGE(TAG, "bmi270_init failed after %d attempts: %d",
                 BMI270_IDF_INIT_RETRIES, rslt);
        i2c_master_bus_rm_device(ctx->dev_handle);
        free(ctx);
        dev->intf_ptr = NULL;
        return rslt;
    }

    ESP_LOGI(TAG, "BMI270 initialised at 0x%02X (chip_id=0x%02X)",
             dev_addr, dev->chip_id);
    return BMI2_OK;
}

void bmi270_idf_deinit(struct bmi2_dev *dev)
{
    if (dev == NULL || dev->intf_ptr == NULL) {
        return;
    }
    bmi270_idf_intf_t *ctx = (bmi270_idf_intf_t *)dev->intf_ptr;
    i2c_master_bus_rm_device(ctx->dev_handle);
    free(ctx);
    dev->intf_ptr = NULL;
}
