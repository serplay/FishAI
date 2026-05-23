// =============================================================================
// audio_manager.h — I2S Driver Management for Mic & Amplifier
// =============================================================================
#pragma once

#include <Arduino.h>
#include <driver/i2s.h>
#include "config.h"

// =============================================================================
// AudioManager — Handles I2S port initialization/deinitialization
//
// Key design decision: We use raw ESP-IDF I2S driver calls rather than
// the arduino-audio-tools I2S abstraction for the mic/amp setup because
// we need fine-grained control over DMA buffer sizes to stay within
// the 520KB SRAM budget. The arduino-audio-tools library is used for
// the A2DP sink integration where it shines.
// =============================================================================

class AudioManager {
public:
    /// Initialize the MAX98357A amplifier I2S output (I2S_NUM_0)
    /// @return true on success
    bool beginAmplifier();

    /// Initialize the INMP441 microphone I2S input (I2S_NUM_1)
    /// @return true on success
    bool beginMicrophone();

    /// Deinitialize the amplifier I2S port (frees DMA buffers)
    void stopAmplifier();

    /// Deinitialize the microphone I2S port (frees DMA buffers)
    void stopMicrophone();

    /// Write PCM samples to the amplifier.
    /// @param data    Pointer to PCM data (16-bit signed, mono or stereo)
    /// @param len     Length in bytes
    /// @param written Output: number of bytes actually written
    /// @param timeout Ticks to wait before timeout
    /// @return ESP_OK on success
    esp_err_t writeToAmp(const void* data, size_t len, size_t* written, 
                         TickType_t timeout = portMAX_DELAY);

    /// Read PCM samples from the microphone.
    /// @param data    Buffer to read into
    /// @param len     Maximum bytes to read
    /// @param read    Output: number of bytes actually read
    /// @param timeout Ticks to wait before timeout
    /// @return ESP_OK on success
    esp_err_t readFromMic(void* data, size_t len, size_t* read,
                          TickType_t timeout = portMAX_DELAY);

    /// Check if amplifier is currently initialized
    bool isAmpRunning() const { return _ampRunning; }

    /// Check if microphone is currently initialized
    bool isMicRunning() const { return _micRunning; }

private:
    bool _ampRunning = false;
    bool _micRunning = false;
};

extern AudioManager Audio;
