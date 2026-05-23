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
    // RMS = sqrt(mean(x²))
    // We use int64_t accumulator to avoid overflow:
    //   max single sample² = 32767² = ~1.07 billion
    //   max sum for 512 samples = ~549 billion — fits in int64_t
    int64_t sumSquares = 0;
    for (size_t i = 0; i < count; i++) {
        int32_t s = samples[i];
        sumSquares += s * s;
    }
    float instantRMS = sqrtf((float)(sumSquares / (int64_t)count));

    // --- Exponential Moving Average for smoothing ---
    // Prevents jittery, seizure-like mouth movements.
    // α = 0.35 gives a nice "punchy but smooth" feel at 16kHz / 256-sample buffers
    // (update rate ~62Hz). Tweak RMS_SMOOTHING_ALPHA in config.h.
    _smoothedRMS = (RMS_SMOOTHING_ALPHA * instantRMS) + 
                   ((1.0f - RMS_SMOOTHING_ALPHA) * _smoothedRMS);

    // --- Map RMS to PWM ---
    uint8_t pwm = rmsToPWM(_smoothedRMS);

    if (pwm == 0) {
        // Below silence threshold — close mouth (coast)
        ledcWrite(MOUTH_PWM_CHANNEL_A, 0);
        ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
    } else {
        // Open mouth proportionally — forward direction only
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

// =============================================================================
// Emergency Stop
// =============================================================================

void MotorController::stopAll() {
    ledcWrite(MOUTH_PWM_CHANNEL_A, 0);
    ledcWrite(MOUTH_PWM_CHANNEL_B, 0);
    digitalWrite(HEAD_IN3_PIN, LOW);
    digitalWrite(HEAD_IN4_PIN, LOW);
    _smoothedRMS = 0.0f;
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
