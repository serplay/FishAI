// =============================================================================
// button_handler.h — GPIO 33 Button with Short/Long Press Detection
// =============================================================================
#pragma once

#include <Arduino.h>
#include "config.h"

// =============================================================================
// Button press event types
// =============================================================================

enum class ButtonEvent : uint8_t {
    NONE,
    SHORT_PRESS,    // < 500ms  — wake trigger / play-pause
    LONG_PRESS      // > 4000ms — mode override & reboot
};

// =============================================================================
// ButtonHandler — ISR-backed, debounced, non-blocking press detection
//
// Design: The ISR only records timestamps. The polling function (called from
// a FreeRTOS task) evaluates press duration. This avoids doing any heavy
// work inside the ISR context.
// =============================================================================

class ButtonHandler {
public:
    /// Initialize GPIO 33 with internal pull-up and attach ISR
    void begin();

    /// Poll for a completed button event. Non-blocking.
    /// Call this periodically from the button task (~50ms interval).
    /// @return The detected event, or NONE if no event completed
    ButtonEvent poll();

private:
    static void IRAM_ATTR _isrHandler();

    static volatile uint32_t _lastFallTime;   // millis() of last FALLING edge
    static volatile uint32_t _lastRiseTime;   // millis() of last RISING edge
    static volatile bool     _pressed;        // true while button is held
    static volatile bool     _eventReady;     // true when a release is detected

    uint32_t _lastDebounceTime = 0;
};

extern ButtonHandler Button;
