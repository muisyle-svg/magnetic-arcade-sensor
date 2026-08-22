#include <Arduino.h>

// Seven KY-003 digital Hall-effect sensor modules on the XIAO ESP32-C3.
const int SENSOR_PINS[] = {D1, D2, D3, D4, D5, D6, D7};
const int SENSOR_COUNT = sizeof(SENSOR_PINS) / sizeof(SENSOR_PINS[0]);

// IMPORTANT: In Arduino IDE, set Tools > USB CDC On Boot > Enabled.
// The XIAO also labels D6/GPIO21 as UART TX. With USB CDC disabled, the
// Serial heartbeat can appear as activity on D6 and make that KY-003 LED
// flicker. USB CDC keeps the PC serial connection on the native USB pins.

// Four-leg RGB LED. Only the red and green channels are needed.
// Use one 220-330 ohm resistor on EACH connected color leg.
// D8/GPIO8 is used only as an LED output. Unlike a Hall input, this does not
// let a detected magnet force a boot-strapping pin low during reset. D0/GPIO2
// and the boot-button pin D9/GPIO9 are deliberately left unconnected.
const int RED_LED_PIN = D8;
const int GREEN_LED_PIN = D10;

// false: common cathode goes to GND.
// true: common anode goes to 3V3 and the PWM output is inverted.
const bool RGB_COMMON_ANODE = true;

// Most KY-003 modules pull their signal LOW when the correct
// magnet pole is detected. INPUT_PULLUP also keeps the signal
// in a known state if the module is disconnected.
const bool SENSOR_ACTIVE_LOW = true;

const unsigned long HEARTBEAT_INTERVAL_MS = 1000;
const unsigned long SENSOR_DEBOUNCE_MS = 30;
const unsigned long RETURN_ENERGY_BOOST_MS = 1600;
const unsigned long RETURN_BOOST_PULSE_PERIOD_MS = 700;

// Resting energy color for 0 through 7 detected emeralds. The LED has only
// red and green connected, so these values move through red, orange, amber,
// yellow-green, and finally pure green as the shrine's energy is restored.
const byte ENERGY_RED[SENSOR_COUNT + 1] = {
  255, 255, 255, 255, 235, 190, 95, 0
};
const byte ENERGY_GREEN[SENSOR_COUNT + 1] = {
  0, 12, 30, 58, 95, 145, 220, 255
};

enum LedEffect {
  LED_EFFECT_NONE,
  LED_EFFECT_REMOVED,
  LED_EFFECT_RETURNED,
  LED_EFFECT_ALL_RETURNED,
};

int previousCount = -1;
unsigned long lastHeartbeat = 0;
bool rawSensorStates[SENSOR_COUNT];
bool stableSensorStates[SENSOR_COUNT];
unsigned long rawStateChangedAt[SENSOR_COUNT];
LedEffect activeLedEffect = LED_EFFECT_NONE;
unsigned long ledEffectStartedAt = 0;
bool returnEnergyBoostActive = false;
unsigned long returnEnergyBoostStartedAt = 0;

bool magnetDetected(int pin) {
  int reading = digitalRead(pin);

  if (SENSOR_ACTIVE_LOW) {
    return reading == LOW;
  }

  return reading == HIGH;
}

void initializeSensorStates() {
  unsigned long now = millis();

  for (int i = 0; i < SENSOR_COUNT; i++) {
    bool detected = magnetDetected(SENSOR_PINS[i]);
    rawSensorStates[i] = detected;
    stableSensorStates[i] = detected;
    rawStateChangedAt[i] = now;
  }
}

void updateSensorStates() {
  unsigned long now = millis();

  for (int i = 0; i < SENSOR_COUNT; i++) {
    bool detected = magnetDetected(SENSOR_PINS[i]);

    if (detected != rawSensorStates[i]) {
      rawSensorStates[i] = detected;
      rawStateChangedAt[i] = now;
    }

    if (
      stableSensorStates[i] != rawSensorStates[i]
      && now - rawStateChangedAt[i] >= SENSOR_DEBOUNCE_MS
    ) {
      stableSensorStates[i] = rawSensorStates[i];
    }
  }
}

int countMagnets() {
  int count = 0;

  for (int i = 0; i < SENSOR_COUNT; i++) {
    if (stableSensorStates[i]) {
      count++;
    }
  }

  return count;
}

void writeLedChannel(int pin, int brightness) {
  brightness = constrain(brightness, 0, 255);
  analogWrite(pin, RGB_COMMON_ANODE ? 255 - brightness : brightness);
}

void setLed(int red, int green) {
  writeLedChannel(RED_LED_PIN, red);
  writeLedChannel(GREEN_LED_PIN, green);
}

void startLedEffect(int oldCount, int newCount) {
  if (oldCount < 0 || newCount == oldCount) {
    return;
  }

  if (newCount < oldCount) {
    activeLedEffect = LED_EFFECT_REMOVED;
    returnEnergyBoostActive = false;
  } else if (newCount == SENSOR_COUNT) {
    activeLedEffect = LED_EFFECT_ALL_RETURNED;
    returnEnergyBoostActive = true;
    returnEnergyBoostStartedAt = millis();
  } else {
    activeLedEffect = LED_EFFECT_RETURNED;
    returnEnergyBoostActive = true;
    returnEnergyBoostStartedAt = millis();
  }

  ledEffectStartedAt = millis();
}

bool renderLedEffect() {
  unsigned long elapsed = millis() - ledEffectStartedAt;

  if (activeLedEffect == LED_EFFECT_REMOVED) {
    // Two sharp red alarm flashes.
    if (elapsed < 70 || (elapsed >= 110 && elapsed < 180)) {
      setLed(255, 0);
      return true;
    }
    if (elapsed < 230) {
      setLed(0, 0);
      return true;
    }
  } else if (activeLedEffect == LED_EFFECT_RETURNED) {
    // A short green absorption flash before returning to red warning mode.
    if (elapsed < 120) {
      setLed(0, 255);
      return true;
    }
    if (elapsed < 170) {
      setLed(0, 0);
      return true;
    }
  } else if (activeLedEffect == LED_EFFECT_ALL_RETURNED) {
    // Three increasingly bright green charge pulses.
    if (elapsed < 1080) {
      int pulseIndex = elapsed / 360;
      unsigned long withinPulse = elapsed % 360;

      if (withinPulse < 190) {
        int targetBrightness = 130 + pulseIndex * 60;
        int brightness = 50 + static_cast<int>(
          (targetBrightness - 50) * withinPulse / 190
        );
        setLed(0, brightness);
      } else {
        setLed(0, 0);
      }
      return true;
    }
  }

  activeLedEffect = LED_EFFECT_NONE;
  return false;
}

void updateLed(int currentCount) {
  if (activeLedEffect != LED_EFFECT_NONE && renderLedEffect()) {
    return;
  }

  currentCount = constrain(currentCount, 0, SENSOR_COUNT);
  unsigned long now = millis();
  if (
    returnEnergyBoostActive
    && now - returnEnergyBoostStartedAt >= RETURN_ENERGY_BOOST_MS
  ) {
    returnEnergyBoostActive = false;
  }

  // More recovered emeralds make the shrine pulse more quickly. Returning an
  // emerald temporarily speeds it up further so the placement feels charged.
  unsigned long pulsePeriod = 2600UL - (
    static_cast<unsigned long>(currentCount) * 150UL
  );
  if (returnEnergyBoostActive) {
    pulsePeriod = RETURN_BOOST_PULSE_PERIOD_MS;
  }

  // Integer-only triangle wave. The ESP32-C3 has no hardware floating-point
  // unit, so avoid float/cosf here; some libm builds can emit illegal FP
  // instructions on this chip.
  const int minimumBrightness = 62;
  const int brightnessRange = 255 - minimumBrightness;
  const unsigned long halfPeriod = pulsePeriod / 2;
  unsigned long phase = now % pulsePeriod;
  int brightness;

  if (phase <= halfPeriod) {
    brightness = minimumBrightness + static_cast<int>(
      brightnessRange * phase / halfPeriod
    );
  } else {
    brightness = minimumBrightness + static_cast<int>(
      brightnessRange * (pulsePeriod - phase) / halfPeriod
    );
  }

  int red = static_cast<int>(ENERGY_RED[currentCount]) * brightness / 255;
  int green = static_cast<int>(ENERGY_GREEN[currentCount]) * brightness / 255;
  setLed(red, green);
}

void sendCount(int count) {
  Serial.print("MAGNET_LOCK:COUNT:");
  Serial.println(count);
}

void setup() {
  Serial.begin(115200);

  delay(1000);

  for (int i = 0; i < SENSOR_COUNT; i++) {
    pinMode(SENSOR_PINS[i], INPUT_PULLUP);
  }

  initializeSensorStates();

  pinMode(RED_LED_PIN, OUTPUT);
  pinMode(GREEN_LED_PIN, OUTPUT);
  setLed(0, 0);

  Serial.println("MAGNET_LOCK:READY");

  // Send the initial state immediately.
  previousCount = countMagnets();
  sendCount(previousCount);
  lastHeartbeat = millis();
}

void loop() {
  updateSensorStates();
  int currentCount = countMagnets();

  // Report immediately whenever the number of detected magnets changes.
  if (currentCount != previousCount) {
    startLedEffect(previousCount, currentCount);
    previousCount = currentCount;
    sendCount(currentCount);
  }

  updateLed(currentCount);

  // Keep the Windows display informed that the ESP32 is still connected.
  if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeat = millis();
    sendCount(currentCount);
  }

  delay(5);
}
