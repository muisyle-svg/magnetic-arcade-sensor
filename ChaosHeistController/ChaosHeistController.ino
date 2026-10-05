#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_system.h>
#include <string.h>

// Arduino's prototype generator can place functions that use this packet type
// before its definition; declare it first.
struct GarageRxPacket;

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
// Keep the final LED failure effect long enough to accompany the Windows
// shutdown sequence: fluorescent buzz, fading buzz, dying buzz, then TV off.
const unsigned long POWER_LOSS_LIGHTS_MS = 2347;
const unsigned long POWER_LOSS_BUZZ_FADES_MS = 3968;
const unsigned long POWER_LOSS_BUZZ_DIES_MS = 2005;
const unsigned long POWER_LOSS_TV_OFF_MS = 2051;
const unsigned long POWER_LOSS_BUZZ_FADES_START_MS =
  POWER_LOSS_LIGHTS_MS;
const unsigned long POWER_LOSS_BUZZ_DIES_START_MS =
  POWER_LOSS_LIGHTS_MS + POWER_LOSS_BUZZ_FADES_MS;
const unsigned long POWER_LOSS_TV_OFF_START_MS =
  POWER_LOSS_BUZZ_DIES_START_MS + POWER_LOSS_BUZZ_DIES_MS;
const unsigned long POWER_LOSS_TOTAL_MS =
  POWER_LOSS_TV_OFF_START_MS + POWER_LOSS_TV_OFF_MS;

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
  LED_EFFECT_FINAL_REMOVED,
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
    activeLedEffect = (
      newCount == 0
      ? LED_EFFECT_FINAL_REMOVED
      : LED_EFFECT_REMOVED
    );
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
  } else if (activeLedEffect == LED_EFFECT_FINAL_REMOVED) {
    // The final emerald makes the Master Emerald appear to lose power over
    // the same long arc as the Windows display: unstable red flicker, a
    // fading buzz, a dying glow, then darkness before normal red narration
    // mode resumes.
    if (elapsed < 120) {
      setLed(255, 0);
      return true;
    }
    if (elapsed < 250) {
      setLed(0, 0);
      return true;
    }
    if (elapsed < POWER_LOSS_LIGHTS_MS) {
      unsigned long flicker = (elapsed - 250) / 75;
      if (flicker % 13 == 0 || flicker % 13 == 1) {
        setLed(255, 0);
      } else if (flicker % 13 == 2 || flicker % 13 == 5) {
        setLed(32, 0);
      } else if (flicker % 13 == 3 || flicker % 13 == 4) {
        setLed(0, 0);
      } else {
        setLed(185, 0);
      }
      return true;
    }
    if (elapsed < POWER_LOSS_BUZZ_DIES_START_MS) {
      unsigned long phase = elapsed - POWER_LOSS_BUZZ_FADES_START_MS;
      int baseline = 190 - static_cast<int>(
        100UL * phase / POWER_LOSS_BUZZ_FADES_MS
      );
      unsigned long flicker = phase / 95;
      if (flicker % 17 == 1 || flicker % 17 == 2
          || flicker % 17 == 9) {
        setLed(0, 0);
      } else if (flicker % 17 == 6) {
        setLed(255, 0);
      } else {
        setLed(baseline, 0);
      }
      return true;
    }
    if (elapsed < POWER_LOSS_TV_OFF_START_MS) {
      unsigned long phase = elapsed - POWER_LOSS_BUZZ_DIES_START_MS;
      int baseline = 105 - static_cast<int>(
        90UL * phase / POWER_LOSS_BUZZ_DIES_MS
      );
      unsigned long flicker = phase / 70;
      if (flicker % 11 == 0 || flicker % 11 == 3) {
        setLed(120, 0);
      } else if (flicker % 11 == 1 || flicker % 11 == 2
          || flicker % 11 == 4 || flicker % 11 == 5) {
        setLed(0, 0);
      } else {
        setLed(baseline, 0);
      }
      return true;
    }
    if (elapsed < POWER_LOSS_TOTAL_MS) {
      unsigned long phase = elapsed - POWER_LOSS_TV_OFF_START_MS;
      if (phase < 130) {
        setLed(255, 0);
      } else if (phase < 560) {
        setLed((phase / 80 % 2) ? 0 : 45, 0);
      } else if (phase < 920) {
        setLed((phase / 120 % 2) ? 0 : 12, 0);
      } else {
        setLed(0, 0);
      }
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

void sendProtocolLine(const char *line) {
  size_t length = strlen(line);

  // Never let a disconnected or non-reading PC stall loop(). The LED and
  // sensor state are autonomous; USB reporting is best-effort telemetry.
  if (Serial.availableForWrite() < static_cast<int>(length)) {
    return;
  }

  Serial.write(reinterpret_cast<const uint8_t *>(line), length);
}

void sendReady() {
  sendProtocolLine("MAGNET_LOCK:READY\n");
}

void sendCount(int count) {
  char message[32];
  snprintf(message, sizeof(message), "MAGNET_LOCK:COUNT:%d\n", count);
  sendProtocolLine(message);
}

// --------------------------------------------------------------------------
// Garage Games station (radio only; no wiring changes)
// --------------------------------------------------------------------------
// The shrine joins the Garage Games ESP-NOW network as a magnetic arcade
// station. It only listens unless the Windows Chaos Heist app asks it to send
// one of its two gameplay signals, so the sensors and the LED energy meter
// above behave exactly as before, with or without Garage Games or USB.
//
//   Radio -> USB:  MAGNET_LOCK:GG:STATION:<mac>:<READY|NORADIO>
//                  MAGNET_LOCK:GG:RUN:<token|->:<state>   (state of the current
//                      Garage Games run, or STALE when the master is silent)
//                  MAGNET_LOCK:GG:EVENT:<token>:<PENDING|ACTIVE|COMPLETED>
//                  MAGNET_LOCK:GG:RESULT:<token>:<S|F>:<seq>:<state>
//                  MAGNET_LOCK:GG:NOACK:<token>:<S|F>:<seq>:<reason>
//                  MAGNET_LOCK:GG:IDENT:<SENT|BUSY>
//   USB -> radio:  GG:SEND:<token>:<S|F>:<seq>:<ageMs>   (S = all emeralds placed,
//                      F = ring goal reached; repeated until the app answers)
//                  GG:IDENT   (announce this MAC to Garage Games Setup)
const uint8_t GG_PROTOCOL_VERSION = 3;
const uint8_t GG_ESPNOW_CHANNEL = 1;
const uint8_t GG_RX_MAX_LEN = 63;
const unsigned long GG_STATUS_TIMEOUT_MS = 1800;
const unsigned long GG_SIGNAL_RESEND_MS = 250;
const uint8_t GG_SIGNAL_MAX_SENDS = 24;
const unsigned long GG_RUN_REPORT_MS = 1000;
const unsigned long GG_STATION_REPORT_MS = 5000;
const unsigned long GG_SCAN_CONFIRM_WINDOW_MS = 2500;
const uint8_t GG_SERIAL_BYTES_PER_LOOP = 64;
const uint8_t BROADCAST_MAC[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

struct GarageRxPacket {
  uint8_t source[6];
  uint8_t len;
  unsigned long receivedAtMs;
  char data[GG_RX_MAX_LEN + 1];
};

QueueHandle_t ggRxQueue = nullptr;
bool ggRadioReady = false;
uint8_t ggOwnMac[6] = {};
char ggOwnMacText[13] = "000000000000";

bool ggStatusSeen = false;
unsigned long ggLastStatusMs = 0;
uint8_t ggMasterMac[6] = {};
char ggToken[17] = "-";
char ggState[10] = "NONE";
char ggReportedToken[17] = "";
char ggReportedState[10] = "";
unsigned long ggLastRunReportMs = 0;
unsigned long ggLastStationReportMs = 0;
uint32_t ggEventRevision = 0;

bool ggSignalPending = false;
char ggSignalToken[17] = "";
char ggSignalKind = 'S';
uint32_t ggSignalSequence = 0;
unsigned long ggSignalAtMs = 0;
uint8_t ggSignalSendsLeft = 0;
unsigned long ggNextSignalSendMs = 0;

uint32_t ggScanToken = 0;
unsigned long ggScanSeenAtMs = 0;
bool ggScanReplyDue = false;
bool ggScanReplied = false;
unsigned long ggScanReplyAtMs = 0;
uint32_t ggIdentSequence = 0;

char ggSerialLine[64];
size_t ggSerialLength = 0;
bool ggSerialOverflow = false;

void ggOnSent(const wifi_tx_info_t*, esp_now_send_status_t) {}

void ggOnRecv(const esp_now_recv_info* info, const uint8_t* data, int len) {
  if (!info || !info->src_addr || !data || len <= 0 || !ggRxQueue) return;
  GarageRxPacket packet{};
  memcpy(packet.source, info->src_addr, 6);
  packet.len = static_cast<uint8_t>(min(len, static_cast<int>(GG_RX_MAX_LEN)));
  packet.receivedAtMs = millis();
  memcpy(packet.data, data, packet.len);
  packet.data[packet.len] = '\0';
  xQueueSend(ggRxQueue, &packet, 0);
}

bool ggSend(const char* message) {
  if (!ggRadioReady || !message) return false;
  return esp_now_send(
    BROADCAST_MAC,
    reinterpret_cast<const uint8_t*>(message),
    strlen(message)
  ) == ESP_OK;
}

void ggReport(const char* format, ...) {
  char line[96];
  va_list arguments;
  va_start(arguments, format);
  int length = vsnprintf(line, sizeof(line) - 1, format, arguments);
  va_end(arguments);
  if (length <= 0 || static_cast<size_t>(length) >= sizeof(line) - 1) return;
  line[length] = '\n';
  line[length + 1] = '\0';
  sendProtocolLine(line);
}

bool ggValidStationMac(const uint8_t* mac) {
  bool allZero = true;
  for (uint8_t i = 0; i < 6; ++i) {
    if (mac[i] != 0) allZero = false;
  }
  return !allZero && (mac[0] & 0x01) == 0;
}

bool ggHexToMac(const char* text, uint8_t* mac) {
  if (!text || strlen(text) != 12) return false;
  for (uint8_t i = 0; i < 6; ++i) {
    uint8_t value = 0;
    for (uint8_t digit = 0; digit < 2; ++digit) {
      char c = text[i * 2 + digit];
      uint8_t nibble;
      if (c >= '0' && c <= '9') nibble = c - '0';
      else if (c >= 'A' && c <= 'F') nibble = c - 'A' + 10;
      else return false;
      value = static_cast<uint8_t>((value << 4) | nibble);
    }
    mac[i] = value;
  }
  return true;
}

bool ggIsToken(const char* text) {
  if (!text || strlen(text) != 16) return false;
  for (uint8_t i = 0; i < 16; ++i) {
    char c = text[i];
    if (!((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F'))) return false;
  }
  return true;
}

bool ggParseUint32(const char* text, uint32_t& value) {
  if (!text || *text == '\0') return false;
  uint32_t parsed = 0;
  for (const char* cursor = text; *cursor != '\0'; ++cursor) {
    if (*cursor < '0' || *cursor > '9') return false;
    uint32_t digit = static_cast<uint32_t>(*cursor - '0');
    if (parsed > (UINT32_MAX - digit) / 10) return false;
    parsed = parsed * 10 + digit;
  }
  value = parsed;
  return true;
}

// Splits "a:b:c" in place. Returns the number of fields, or 0 when there are
// more than maxFields or any field is empty.
uint8_t ggSplitFields(char* text, char** fields, uint8_t maxFields) {
  uint8_t count = 0;
  char* cursor = text;
  while (cursor) {
    if (count >= maxFields) return 0;
    fields[count++] = cursor;
    char* separator = strchr(cursor, ':');
    if (separator) {
      *separator = '\0';
      cursor = separator + 1;
    } else {
      cursor = nullptr;
    }
  }
  for (uint8_t i = 0; i < count; ++i) {
    if (fields[i][0] == '\0') return 0;
  }
  return count;
}

bool ggKnownRunState(const char* state) {
  return strcmp(state, "NONE") == 0 || strcmp(state, "ARMED") == 0 ||
    strcmp(state, "COUNTDOWN") == 0 || strcmp(state, "ACTIVE") == 0 ||
    strcmp(state, "PAUSED") == 0 || strcmp(state, "FINISHED") == 0 ||
    strcmp(state, "TIMED_OUT") == 0;
}

bool ggStatusFresh(unsigned long now) {
  return ggStatusSeen && now - ggLastStatusMs <= GG_STATUS_TIMEOUT_MS;
}

bool ggFromMaster(const GarageRxPacket& packet) {
  return ggStatusFresh(packet.receivedAtMs) &&
    memcmp(packet.source, ggMasterMac, 6) == 0;
}

void ggReportRun(bool force) {
  unsigned long now = millis();
  const char* token = ggToken;
  const char* state = ggState;
  if (!ggStatusFresh(now)) {
    token = "-";
    state = "STALE";
  }
  bool changed = strcmp(token, ggReportedToken) != 0 ||
    strcmp(state, ggReportedState) != 0;
  if (!force && !changed) return;
  strncpy(ggReportedToken, token, sizeof(ggReportedToken) - 1);
  ggReportedToken[sizeof(ggReportedToken) - 1] = '\0';
  strncpy(ggReportedState, state, sizeof(ggReportedState) - 1);
  ggReportedState[sizeof(ggReportedState) - 1] = '\0';
  ggLastRunReportMs = now;
  ggReport("MAGNET_LOCK:GG:RUN:%s:%s", token, state);
}

void ggReportStation() {
  ggLastStationReportMs = millis();
  ggReport(
    "MAGNET_LOCK:GG:STATION:%s:%s",
    ggOwnMacText,
    ggRadioReady ? "READY" : "NORADIO"
  );
}

void ggClearSignal() {
  ggSignalPending = false;
  ggSignalSendsLeft = 0;
}

void ggSendSignal() {
  char message[GG_RX_MAX_LEN + 1];
  unsigned long ageMs = millis() - ggSignalAtMs;
  int length = snprintf(
    message,
    sizeof(message),
    "GARC:%u:%s:%lu:%c:%lu",
    GG_PROTOCOL_VERSION,
    ggSignalToken,
    static_cast<unsigned long>(ggSignalSequence),
    ggSignalKind,
    ageMs
  );
  if (length > 0 && static_cast<size_t>(length) < sizeof(message)) {
    ggSend(message);
  }
}

// GARAGE:3:<token|->:<state> from the Garage Games master, about four times a second.
void ggHandleGarage(const GarageRxPacket& packet) {
  char text[GG_RX_MAX_LEN + 1];
  memcpy(text, packet.data, packet.len + 1);
  char* fields[5];
  if (ggSplitFields(text, fields, 5) != 4 || strcmp(fields[0], "GARAGE") != 0 ||
      strcmp(fields[1], "3") != 0 || !ggKnownRunState(fields[3]) ||
      !ggValidStationMac(packet.source) ||
      memcmp(packet.source, ggOwnMac, 6) == 0) return;
  bool noRun = strcmp(fields[2], "-") == 0;
  if (noRun != (strcmp(fields[3], "NONE") == 0)) return;
  if (!noRun && !ggIsToken(fields[2])) return;
  // Follow the master that is already talking; another master is accepted
  // only after the first one has gone quiet.
  if (ggStatusFresh(packet.receivedAtMs) &&
      memcmp(packet.source, ggMasterMac, 6) != 0) return;

  memcpy(ggMasterMac, packet.source, 6);
  if (strcmp(ggToken, fields[2]) != 0) {
    ggEventRevision = 0;
    if (ggSignalPending) {
      ggReport(
        "MAGNET_LOCK:GG:NOACK:%s:%c:%lu:NEWRUN",
        ggSignalToken,
        ggSignalKind,
        static_cast<unsigned long>(ggSignalSequence)
      );
      ggClearSignal();
    }
  }
  strncpy(ggToken, fields[2], sizeof(ggToken) - 1);
  ggToken[sizeof(ggToken) - 1] = '\0';
  strncpy(ggState, fields[3], sizeof(ggState) - 1);
  ggState[sizeof(ggState) - 1] = '\0';
  ggStatusSeen = true;
  ggLastStatusMs = packet.receivedAtMs;
  ggReportRun(false);

  // A Garage Games device scan keeps these status packets flowing; the
  // standalone Speed game stops them. Answer only a scan, never Speed.
  if (ggScanToken != 0 && !ggScanReplied && !ggScanReplyDue &&
      packet.receivedAtMs - ggScanSeenAtMs <= GG_SCAN_CONFIRM_WINDOW_MS) {
    ggScanReplyDue = true;
    ggScanReplyAtMs = packet.receivedAtMs + 20 + esp_random() % 230;
  }
}

// GRESULT:3:<token>:<seq>:<state>:<mac>, the app's answer to our signal.
void ggHandleResult(const GarageRxPacket& packet) {
  char text[GG_RX_MAX_LEN + 1];
  memcpy(text, packet.data, packet.len + 1);
  char* fields[7];
  uint32_t sequence = 0;
  uint8_t mac[6];
  if (ggSplitFields(text, fields, 7) != 6 || strcmp(fields[1], "3") != 0 ||
      !ggIsToken(fields[2]) || !ggParseUint32(fields[3], sequence) ||
      !ggHexToMac(fields[5], mac) || memcmp(mac, ggOwnMac, 6) != 0 ||
      !ggFromMaster(packet) || !ggSignalPending ||
      strcmp(fields[2], ggSignalToken) != 0 || sequence != ggSignalSequence) {
    return;
  }
  ggReport(
    "MAGNET_LOCK:GG:RESULT:%s:%c:%lu:%s",
    ggSignalToken,
    ggSignalKind,
    static_cast<unsigned long>(ggSignalSequence),
    fields[4]
  );
  ggClearSignal();
}

// GSTATE:3:<token>:<revision>:<state>:<mac>, the app's view of each event.
void ggHandleEventState(const GarageRxPacket& packet) {
  char text[GG_RX_MAX_LEN + 1];
  memcpy(text, packet.data, packet.len + 1);
  char* fields[7];
  uint32_t revision = 0;
  uint8_t mac[6];
  if (ggSplitFields(text, fields, 7) != 6 || strcmp(fields[1], "3") != 0 ||
      !ggIsToken(fields[2]) || !ggParseUint32(fields[3], revision) ||
      !ggHexToMac(fields[5], mac) || memcmp(mac, ggOwnMac, 6) != 0 ||
      !ggFromMaster(packet) || strcmp(fields[2], ggToken) != 0 ||
      revision < ggEventRevision) {
    return;
  }
  ggEventRevision = revision;
  ggReport("MAGNET_LOCK:GG:EVENT:%s:%s", fields[2], fields[4]);
}

// DISCOVER:3:<token> is used by both a Garage Games device scan and the
// standalone Speed game. Remember it; ggHandleGarage decides whether to answer.
void ggHandleDiscover(const GarageRxPacket& packet) {
  char text[GG_RX_MAX_LEN + 1];
  memcpy(text, packet.data, packet.len + 1);
  char* fields[4];
  uint32_t token = 0;
  if (ggSplitFields(text, fields, 4) != 3 || strcmp(fields[1], "3") != 0 ||
      !ggParseUint32(fields[2], token) || token == 0) return;
  if (token == ggScanToken) return;
  ggScanToken = token;
  ggScanSeenAtMs = packet.receivedAtMs;
  ggScanReplyDue = false;
  ggScanReplied = false;
}

void ggProcessRadio() {
  GarageRxPacket packet;
  while (ggRxQueue && xQueueReceive(ggRxQueue, &packet, 0) == pdTRUE) {
    if (memchr(packet.data, '\0', packet.len) != nullptr) continue;
    if (strncmp(packet.data, "GARAGE:", 7) == 0) {
      ggHandleGarage(packet);
    } else if (strncmp(packet.data, "GRESULT:", 8) == 0) {
      ggHandleResult(packet);
    } else if (strncmp(packet.data, "GSTATE:", 7) == 0) {
      ggHandleEventState(packet);
    } else if (strncmp(packet.data, "DISCOVER:", 9) == 0) {
      ggHandleDiscover(packet);
    }
  }
}

void ggStartSignal(char* token, char kind, uint32_t sequence, uint32_t ageMs) {
  unsigned long now = millis();
  const char* reason = nullptr;
  if (!ggRadioReady) {
    reason = "NORADIO";
  } else if (!ggStatusFresh(now)) {
    reason = "STALE";
  } else if (strcmp(token, ggToken) != 0) {
    reason = "WRONGRUN";
  }
  if (reason) {
    ggReport(
      "MAGNET_LOCK:GG:NOACK:%s:%c:%lu:%s",
      token,
      kind,
      static_cast<unsigned long>(sequence),
      reason
    );
    return;
  }
  bool sameSignal = ggSignalPending && ggSignalSequence == sequence &&
    ggSignalKind == kind && strcmp(ggSignalToken, token) == 0;
  if (!sameSignal) {
    strncpy(ggSignalToken, token, sizeof(ggSignalToken) - 1);
    ggSignalToken[sizeof(ggSignalToken) - 1] = '\0';
    ggSignalKind = kind;
    ggSignalSequence = sequence;
    ggSignalAtMs = now - min(ageMs, static_cast<uint32_t>(60000));
  }
  ggSignalPending = true;
  ggSignalSendsLeft = GG_SIGNAL_MAX_SENDS;
  ggNextSignalSendMs = now;
}

void ggSendIdentity() {
  unsigned long now = millis();
  bool idle = strcmp(ggState, "NONE") == 0 || strcmp(ggState, "FINISHED") == 0 ||
    strcmp(ggState, "TIMED_OUT") == 0;
  if (!ggRadioReady || !ggStatusFresh(now) || !idle) {
    ggReport("MAGNET_LOCK:GG:IDENT:BUSY");
    return;
  }
  ++ggIdentSequence;
  if (ggIdentSequence == 0) ++ggIdentSequence;
  char message[40];
  int length = snprintf(
    message,
    sizeof(message),
    "GTEST:%u:%s:%lu",
    GG_PROTOCOL_VERSION,
    ggOwnMacText,
    static_cast<unsigned long>(ggIdentSequence)
  );
  if (length > 0 && static_cast<size_t>(length) < sizeof(message) && ggSend(message)) {
    ggReport("MAGNET_LOCK:GG:IDENT:SENT");
  } else {
    ggReport("MAGNET_LOCK:GG:IDENT:BUSY");
  }
}

void ggHandleSerialLine(char* line) {
  if (strcmp(line, "GG:IDENT") == 0) {
    ggSendIdentity();
    return;
  }
  if (strncmp(line, "GG:SEND:", 8) != 0) return;
  char* fields[6];
  uint32_t sequence = 0;
  uint32_t ageMs = 0;
  if (ggSplitFields(line + 8, fields, 6) != 4 || !ggIsToken(fields[0]) ||
      strlen(fields[1]) != 1 || (fields[1][0] != 'S' && fields[1][0] != 'F') ||
      !ggParseUint32(fields[2], sequence) || sequence == 0 ||
      !ggParseUint32(fields[3], ageMs)) return;
  ggStartSignal(fields[0], fields[1][0], sequence, ageMs);
}

void ggReadSerial() {
  uint8_t bytesRead = 0;
  while (Serial.available() > 0 && bytesRead < GG_SERIAL_BYTES_PER_LOOP) {
    int incoming = Serial.read();
    if (incoming < 0) break;
    ++bytesRead;
    if (incoming == '\r' || incoming == '\n') {
      if (!ggSerialOverflow && ggSerialLength > 0) {
        ggSerialLine[ggSerialLength] = '\0';
        ggHandleSerialLine(ggSerialLine);
      }
      ggSerialLength = 0;
      ggSerialOverflow = false;
      continue;
    }
    if (incoming < 0x20 || incoming > 0x7E ||
        ggSerialLength >= sizeof(ggSerialLine) - 1) {
      ggSerialOverflow = true;
      continue;
    }
    if (!ggSerialOverflow) ggSerialLine[ggSerialLength++] = static_cast<char>(incoming);
  }
}

void ggUpdate() {
  // Radio first, so a command from the app is checked against the newest run.
  ggProcessRadio();
  ggReadSerial();
  unsigned long now = millis();

  if (ggSignalPending && static_cast<long>(now - ggNextSignalSendMs) >= 0) {
    if (ggSignalSendsLeft == 0) {
      ggReport(
        "MAGNET_LOCK:GG:NOACK:%s:%c:%lu:TIMEOUT",
        ggSignalToken,
        ggSignalKind,
        static_cast<unsigned long>(ggSignalSequence)
      );
      ggClearSignal();
    } else {
      ggSendSignal();
      --ggSignalSendsLeft;
      ggNextSignalSendMs = now + GG_SIGNAL_RESEND_MS;
    }
  }

  if (ggScanReplyDue && static_cast<long>(now - ggScanReplyAtMs) >= 0) {
    ggScanReplyDue = false;
    ggScanReplied = true;
    char message[32];
    snprintf(
      message,
      sizeof(message),
      "HELLO:%u:%lu",
      GG_PROTOCOL_VERSION,
      static_cast<unsigned long>(ggScanToken)
    );
    ggSend(message);
  }

  ggReportRun(now - ggLastRunReportMs >= GG_RUN_REPORT_MS);
  if (now - ggLastStationReportMs >= GG_STATION_REPORT_MS) {
    ggReportStation();
  }
}

void ggSetup() {
  // A radio failure only disables the Garage Games station. The sensors,
  // LED meter, and USB count telemetry keep running exactly as before.
  ggRxQueue = xQueueCreate(16, sizeof(GarageRxPacket));
  if (!ggRxQueue || !WiFi.mode(WIFI_STA)) return;
  WiFi.disconnect(false, false);
  WiFi.setSleep(false);
  if (esp_wifi_set_ps(WIFI_PS_NONE) != ESP_OK) return;
  if (esp_wifi_set_channel(GG_ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE) != ESP_OK) return;
  WiFi.macAddress(ggOwnMac);
  if (!ggValidStationMac(ggOwnMac)) return;
  snprintf(
    ggOwnMacText,
    sizeof(ggOwnMacText),
    "%02X%02X%02X%02X%02X%02X",
    ggOwnMac[0], ggOwnMac[1], ggOwnMac[2],
    ggOwnMac[3], ggOwnMac[4], ggOwnMac[5]
  );
  if (esp_now_init() != ESP_OK) return;
  if (esp_now_register_send_cb(ggOnSent) != ESP_OK) return;
  if (esp_now_register_recv_cb(ggOnRecv) != ESP_OK) return;
  esp_now_peer_info_t peer{};
  memcpy(peer.peer_addr, BROADCAST_MAC, 6);
  peer.ifidx = WIFI_IF_STA;
  peer.channel = GG_ESPNOW_CHANNEL;
  peer.encrypt = false;
  esp_err_t result = esp_now_add_peer(&peer);
  if (result != ESP_OK && result != ESP_ERR_ESPNOW_EXIST) return;
  ggRadioReady = true;
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

  sendReady();
  ggSetup();
  ggReportStation();

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
  ggUpdate();

  // Keep the Windows display informed that the ESP32 is still connected.
  if (millis() - lastHeartbeat >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeat = millis();
    sendCount(currentCount);
  }

  delay(5);
}
