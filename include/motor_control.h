// =============================================================================
// motor_control.h — Mouth Lip-Sync & Head/Body Motor Control
// =============================================================================
#pragma once

#include <Arduino.h>
#include "config.h"

// =============================================================================
// Motor Controller — manages PWM lip-sync and head/body movement
// =============================================================================

class MotorController {
public:
    /// Initialize PWM channels and set motors to safe idle state
    void begin();

    /// Calculate RMS from a buffer of 16-bit PCM samples and drive mouth motor.
    /// Call this from the audio processing task every time a buffer is decoded.
    /// @param samples  Pointer to signed 16-bit PCM sample buffer
    /// @param count    Number of samples in the buffer
    void updateLipSync(const int16_t* samples, size_t count);

    /// Directly set mouth openness (0.0 = closed, 1.0 = fully open).
    /// Bypasses RMS calculation — used for testing or manual override.
    void setMouthPosition(float openness);

    /// Close the mouth (coast, no active braking)
    void closeMouth();

    /// Raise the head/body (e.g., when wake word detected)
    void raiseHead();

    /// Lower the head/body back to resting position (BLOCKING — 300ms pulse)
    void lowerHead();

    /// Set head motor direction without blocking (for dance mode).
    /// @param forward  true = head up, false = head down
    void setHeadRaw(bool forward);

    /// Coast the head motor (stop driving, free spin)
    void coastHead();

    /// Emergency stop — all motors off
    void stopAll();

    /// Get the last computed smoothed RMS value (for diagnostics / WebSocket telemetry)
    float getSmoothedRMS() const { return _smoothedRMS; }

private:
    float _smoothedRMS = 0.0f;
    float _baselineRMS = 0.0f;    // Slow-moving energy envelope of the audio
    float _mouthOpenness = 0.0f;  // Smoothed mouth position (0.0–1.0)

    /// Map a smoothed RMS value to a PWM duty cycle
    uint8_t rmsToPWM(float rms);
};

// Singleton instance — accessed from audio task & button handler
extern MotorController Motors;
