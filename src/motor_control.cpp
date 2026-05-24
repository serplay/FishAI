// =============================================================================
// motor_control.cpp — Mouth Lip-Sync & Head/Body Motor Control
// =============================================================================

#include "motor_control.h"
#include <math.h>

// Singleton
MotorController Motors;

// =============================================================================
// Initialization
// =============================================================================

void MotorController::begin() {
    // --- Mouth Motor: PWM via LEDC peripheral ---
    // We use two channels because the L298N needs complementary signals:
    //   IN1 = HIGH, IN2 = LOW  → Forward (mouth opens)
    //   IN1 = LOW,  IN2 = HIGH → Reverse (mouth closes)
    //   Both LOW = coast (motor free-spinning)
    //   Both HIGH = brake (motor locked) — avoid this, wastes power
    
    ledcSetup(MOUTH_PWM_CHANNEL_A, MOUTH_PWM_FREQ, MOUTH_PWM_RESOLUTION);
    ledcSetup(MOUTH_PWM_CHANNEL_B, MOUTH_PWM_FREQ, MOUTH_PWM_RESOLUTION);
    ledcAttachPin(MOUTH_IN1_PIN, MOUTH_PWM_CHANNEL_A);
    ledcAttachPin(MOUTH_IN2_PIN, MOUTH_PWM_CHANNEL_B);

    // Start with mouth closed (coast)
    ledcWrite(MOUTH_PWM_CHANNEL_A, 0);
    ledcWrite(MOUTH_PWM_CHANNEL_B, 0);

    // --- Head/Body Motor: Simple digital direction control ---
    pinMode(HEAD_IN3_PIN, OUTPUT);
    pinMode(HEAD_IN4_PIN, OUTPUT);
    digitalWrite(HEAD_IN3_PIN, LOW);
    digitalWrite(HEAD_IN4_PIN, LOW);

    _smoothedRMS = 0.0f;

    Serial.println("[MOTOR] Initialized — mouth PWM + head GPIO ready");
}

// =============================================================================
// RMS-based Lip-Sync
// =============================================================================

void MotorController::updateLipSync(const int16_t* samples, size_t count) {
    if (!samples || count == 0) return;

    // --- Compute RMS of the sample buffer ---
    int64_t sumSquares = 0;
    for (size_t i = 0; i < count; i++) {
        int32_t s = samples[i];
        sumSquares += s * s;
    }
    float instantRMS = sqrtf((float)(sumSquares / (int64_t)count));

    // --- Update slow-moving baseline (song energy envelope) ---
    // This tracks the overall loudness of the song so we can detect
    // relative peaks rather than using an absolute threshold.
    // Seed the baseline on first non-silent sample to avoid slow ramp-up.
    if (_baselineRMS < 1.0f && instantRMS > RMS_SILENCE_THRESHOLD) {
        _baselineRMS = instantRMS;
    } else {
        _baselineRMS = (LIPSYNC_BASELINE_ALPHA * instantRMS) +
                       ((1.0f - LIPSYNC_BASELINE_ALPHA) * _baselineRMS);
    }

    // --- Determine target mouth openness ---
    float targetOpenness = 0.0f;

    if (instantRMS < RMS_SILENCE_THRESHOLD) {
        // True silence — mouth fully closed
        targetOpenness = 0.0f;
    } else {
        // Compare instantaneous RMS against the adaptive baseline.
        // Mouth opens proportionally when the signal exceeds the baseline.
        float threshold = _baselineRMS * LIPSYNC_PEAK_RATIO;

        if (instantRMS > threshold) {
            // How far above the threshold are we? Map to 0.0–1.0
            float excess = instantRMS - threshold;
            float range = _baselineRMS * 2.0f;  // Full open at ~3x baseline
            targetOpenness = constrain(excess / range, 0.0f, 1.0f);
        }
    }

    // --- Asymmetric smoothing (fast attack, slow release) ---
    // Makes the mouth snap open on beats/syllables but close smoothly
    float alpha;
    if (targetOpenness > _mouthOpenness) {
        alpha = LIPSYNC_ATTACK_ALPHA;   // Fast: track syllable onsets
    } else {
        alpha = LIPSYNC_RELEASE_ALPHA;  // Slow: smooth close between words
    }
    _mouthOpenness = (alpha * targetOpenness) + ((1.0f - alpha) * _mouthOpenness);

    // Store for diagnostics
    _smoothedRMS = instantRMS;

    // --- Drive the motor ---
    if (_mouthOpenness < 0.05f) {
        // Effectively closed — coast (no power waste)
        ledcWrite(MOUTH_PWM_CHANNEL_A, 0);
        ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
    } else {
        uint8_t pwm = MOUTH_PWM_MIN +
                      (uint8_t)(_mouthOpenness * (float)(MOUTH_PWM_MAX - MOUTH_PWM_MIN));
        ledcWrite(MOUTH_PWM_CHANNEL_A, pwm);
        ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
    }
}

// =============================================================================
// Direct Position Control
// =============================================================================

void MotorController::setMouthPosition(float openness) {
    openness = constrain(openness, 0.0f, 1.0f);
    
    if (openness < 0.01f) {
        closeMouth();
        return;
    }

    uint8_t pwm = MOUTH_PWM_MIN + 
                  (uint8_t)((float)(MOUTH_PWM_MAX - MOUTH_PWM_MIN) * openness);
    ledcWrite(MOUTH_PWM_CHANNEL_A, pwm);
    ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
}

void MotorController::closeMouth() {
    ledcWrite(MOUTH_PWM_CHANNEL_A, 0);
    ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
}

// =============================================================================
// Head/Body Motor
// =============================================================================

void MotorController::raiseHead() {
    // Drive forward — head tilts up
    digitalWrite(HEAD_IN3_PIN, HIGH);
    digitalWrite(HEAD_IN4_PIN, LOW);
    Serial.println("[MOTOR] Head raised");
}

void MotorController::lowerHead() {
    // Drive reverse — head returns to rest
    digitalWrite(HEAD_IN3_PIN, LOW);
    digitalWrite(HEAD_IN4_PIN, HIGH);

    // Brief pulse to return, then coast
    // The mechanical linkage has a physical stop, so we just pulse for ~300ms
    // and let it settle. A proper implementation could use a limit switch.
    vTaskDelay(pdMS_TO_TICKS(300));
    digitalWrite(HEAD_IN3_PIN, LOW);
    digitalWrite(HEAD_IN4_PIN, LOW);
    Serial.println("[MOTOR] Head lowered");
}

void MotorController::setHeadRaw(bool forward) {
    // Non-blocking head direction — used by dance mode
    // Does NOT coast automatically (caller must call coastHead() later)
    if (forward) {
        digitalWrite(HEAD_IN3_PIN, HIGH);
        digitalWrite(HEAD_IN4_PIN, LOW);
    } else {
        digitalWrite(HEAD_IN3_PIN, LOW);
        digitalWrite(HEAD_IN4_PIN, HIGH);
    }
}

void MotorController::coastHead() {
    // Stop driving the head motor (free spin)
    digitalWrite(HEAD_IN3_PIN, LOW);
    digitalWrite(HEAD_IN4_PIN, LOW);
}

// =============================================================================
// Emergency Stop
// =============================================================================

void MotorController::stopAll() {
    ledcWrite(MOUTH_PWM_CHANNEL_A, 0);
    ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
    digitalWrite(HEAD_IN3_PIN, LOW);
    digitalWrite(HEAD_IN4_PIN, LOW);
    _smoothedRMS = 0.0f;
    _baselineRMS = 0.0f;
    _mouthOpenness = 0.0f;
}

// =============================================================================
// Private: RMS → PWM Mapping
// =============================================================================

uint8_t MotorController::rmsToPWM(float rms) {
    // Gate: if below noise floor, return 0 (mouth closed)
    if (rms < RMS_SILENCE_THRESHOLD) {
        return 0;
    }

    // Linear map from [SILENCE_THRESHOLD, MAX_EXPECTED] → [PWM_MIN, PWM_MAX]
    // with clamping at both ends
    float normalized = (rms - RMS_SILENCE_THRESHOLD) / 
                       (float)(RMS_MAX_EXPECTED - RMS_SILENCE_THRESHOLD);
    normalized = constrain(normalized, 0.0f, 1.0f);

    return MOUTH_PWM_MIN + (uint8_t)(normalized * (MOUTH_PWM_MAX - MOUTH_PWM_MIN));
}
