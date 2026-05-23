// =============================================================================
// audio_manager.cpp — I2S Driver Management for Mic & Amplifier
// =============================================================================

#include "audio_manager.h"

// Singleton
AudioManager Audio;

// =============================================================================
// MAX98357A Amplifier — I2S_NUM_0 (TX only)
// =============================================================================

bool AudioManager::beginAmplifier() {
    if (_ampRunning) return true;

    i2s_config_t config = {};
    config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX);
    config.sample_rate = AUDIO_SAMPLE_RATE;
    config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;  // MAX98357A mono
    config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    config.dma_buf_count = I2S_DMA_BUF_COUNT;
    config.dma_buf_len = I2S_DMA_BUF_LEN;
    config.use_apll = false;    // APB clock is fine for 16kHz
    config.tx_desc_auto_clear = true;  // Zero-fill on underrun (prevents pops)

    i2s_pin_config_t pins = {};
    pins.bck_io_num = AMP_BCLK_PIN;
    pins.ws_io_num = AMP_LRC_PIN;
    pins.data_out_num = AMP_DIN_PIN;
    pins.data_in_num = I2S_PIN_NO_CHANGE;

    esp_err_t err = i2s_driver_install(AMP_I2S_PORT, &config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("[AUDIO] Amp I2S install failed: %s\n", esp_err_to_name(err));
        return false;
    }

    err = i2s_set_pin(AMP_I2S_PORT, &pins);
    if (err != ESP_OK) {
        Serial.printf("[AUDIO] Amp pin config failed: %s\n", esp_err_to_name(err));
        i2s_driver_uninstall(AMP_I2S_PORT);
        return false;
    }

    i2s_zero_dma_buffer(AMP_I2S_PORT);
    _ampRunning = true;
    Serial.println("[AUDIO] Amplifier I2S initialized (I2S_NUM_0)");
    return true;
}

// =============================================================================
// INMP441 Microphone — I2S_NUM_1 (RX only)
// =============================================================================

bool AudioManager::beginMicrophone() {
    if (_micRunning) return true;

    i2s_config_t config = {};
    config.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
    config.sample_rate = AUDIO_SAMPLE_RATE;
    // INMP441 outputs 32-bit frames. We read as 32-bit and truncate
    // the upper 16 bits in software — the lower 16 bits are noise/padding.
    config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    config.dma_buf_count = I2S_DMA_BUF_COUNT;
    config.dma_buf_len = I2S_DMA_BUF_LEN;
    config.use_apll = false;

    i2s_pin_config_t pins = {};
    pins.bck_io_num = MIC_SCK_PIN;
    pins.ws_io_num = MIC_WS_PIN;
    pins.data_out_num = I2S_PIN_NO_CHANGE;
    pins.data_in_num = MIC_SD_PIN;

    esp_err_t err = i2s_driver_install(MIC_I2S_PORT, &config, 0, NULL);
    if (err != ESP_OK) {
        Serial.printf("[AUDIO] Mic I2S install failed: %s\n", esp_err_to_name(err));
        return false;
    }

    err = i2s_set_pin(MIC_I2S_PORT, &pins);
    if (err != ESP_OK) {
        Serial.printf("[AUDIO] Mic pin config failed: %s\n", esp_err_to_name(err));
        i2s_driver_uninstall(MIC_I2S_PORT);
        return false;
    }

    _micRunning = true;
    Serial.println("[AUDIO] Microphone I2S initialized (I2S_NUM_1)");
    return true;
}

// =============================================================================
// Shutdown
// =============================================================================

void AudioManager::stopAmplifier() {
    if (!_ampRunning) return;
    i2s_zero_dma_buffer(AMP_I2S_PORT);
    i2s_driver_uninstall(AMP_I2S_PORT);
    _ampRunning = false;
    Serial.println("[AUDIO] Amplifier I2S stopped");
}

void AudioManager::stopMicrophone() {
    if (!_micRunning) return;
    i2s_driver_uninstall(MIC_I2S_PORT);
    _micRunning = false;
    Serial.println("[AUDIO] Microphone I2S stopped");
}

// =============================================================================
// I/O Wrappers
// =============================================================================

esp_err_t AudioManager::writeToAmp(const void* data, size_t len, size_t* written,
                                    TickType_t timeout) {
    if (!_ampRunning) return ESP_ERR_INVALID_STATE;
    return i2s_write(AMP_I2S_PORT, data, len, written, timeout);
}

esp_err_t AudioManager::readFromMic(void* data, size_t len, size_t* read,
                                     TickType_t timeout) {
    if (!_micRunning) return ESP_ERR_INVALID_STATE;
    return i2s_read(MIC_I2S_PORT, data, len, read, timeout);
}
