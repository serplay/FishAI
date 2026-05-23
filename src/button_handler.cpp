// =============================================================================
// button_handler.cpp — GPIO 33 Short/Long Press Detection
// =============================================================================

#include "button_handler.h"

ButtonHandler Button;

volatile uint32_t ButtonHandler::_lastFallTime = 0;
volatile uint32_t ButtonHandler::_lastRiseTime = 0;
volatile bool     ButtonHandler::_pressed      = false;
volatile bool     ButtonHandler::_eventReady   = false;

void ButtonHandler::begin() {
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), _isrHandler, CHANGE);
    Serial.println("[BUTTON] GPIO 33 initialized");
}

void IRAM_ATTR ButtonHandler::_isrHandler() {
    uint32_t now = millis();
    if (digitalRead(BUTTON_PIN) == LOW) {
        _lastFallTime = now;
        _pressed = true;
    } else {
        _lastRiseTime = now;
        _pressed = false;
        _eventReady = true;
    }
}

ButtonEvent ButtonHandler::poll() {
    if (_pressed) {
        uint32_t held = millis() - _lastFallTime;
        if (held >= BUTTON_LONG_PRESS_MS) {
            _pressed = false;
            _eventReady = false;
            return ButtonEvent::LONG_PRESS;
        }
        return ButtonEvent::NONE;
    }
    if (_eventReady) {
        _eventReady = false;
        uint32_t duration = _lastRiseTime - _lastFallTime;
        if (duration < BUTTON_DEBOUNCE_MS) return ButtonEvent::NONE;
        if (duration <= BUTTON_SHORT_PRESS_MAX) return ButtonEvent::SHORT_PRESS;
        return ButtonEvent::NONE;
    }
    return ButtonEvent::NONE;
}
