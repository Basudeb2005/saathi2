// Saathi push-to-talk button — ESP32 over BLE.
//
// The ESP32 pretends to be a Bluetooth keyboard and sends one keypress.
// The Pi reads it through evdev like any other key, which means the same
// code on the Pi also works with a five-pound shutter remote — the
// hardware is replaceable without touching the software.
//
// Flashing:
//   Arduino IDE -> Boards Manager -> esp32 by Espressif
//   Library Manager -> "ESP32 BLE Keyboard" (T-vK)
//   Board: your ESP32, Upload.
//
// Then on the Pi: pair it once (bluetoothctl), and
//   python -m saathi.button        # prints presses
//   WAKE_MODE=button in .env
//
// Deep sleep is the whole game for a wearable. Awake, an ESP32 drains a
// small LiPo in hours; asleep on ext0 wake it lasts weeks. So it sleeps
// immediately and only exists while a finger is on the button.

#include <BleKeyboard.h>
#include "esp_sleep.h"

// GPIO0 is the BOOT button on most dev boards, so this runs with no
// wiring at all. For a real build, put a tactile switch on any RTC-capable
// pin and change this — GPIO0 also drops the board into flash mode if it's
// held at reset, which is a poor property for a button worn on a wrist.
#define BUTTON_PIN GPIO_NUM_0

// The name the Pi matches on. Keep it distinctive: BUTTON_NAME in .env
// does a substring match against it.
BleKeyboard bleKeyboard("Saathi Button", "Saathi", 100);

// Long enough to outlast contact bounce, short enough to feel instant.
const unsigned long DEBOUNCE_MS = 50;
// If the connection doesn't come back in this long, sleep anyway rather
// than sitting awake draining the battery for a press nobody will hear.
const unsigned long CONNECT_TIMEOUT_MS = 8000;

void sleepNow() {
  // Wake when the button is pulled LOW, i.e. pressed.
  esp_sleep_enable_ext0_wakeup(BUTTON_PIN, 0);
  esp_deep_sleep_start();
}

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  // Woken by anything other than the button — a reset, first power-on —
  // means nobody pressed anything. Go straight back to sleep.
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_EXT0) {
    sleepNow();
  }

  delay(DEBOUNCE_MS);
  if (digitalRead(BUTTON_PIN) != LOW) {
    // A bounce or a knock, not a press.
    sleepNow();
  }

  bleKeyboard.begin();

  unsigned long start = millis();
  while (!bleKeyboard.isConnected()) {
    if (millis() - start > CONNECT_TIMEOUT_MS) {
      sleepNow();
    }
    delay(50);
  }

  // KEY_MEDIA_PLAY_PAUSE because it is what shutter remotes send, so the
  // Pi side needs no special case for either one.
  bleKeyboard.write(KEY_MEDIA_PLAY_PAUSE);
  delay(200);

  // Don't sleep with a finger still down — ext0 would fire again
  // immediately and send a second press.
  while (digitalRead(BUTTON_PIN) == LOW) {
    delay(20);
  }

  bleKeyboard.end();
  sleepNow();
}

void loop() {
  // Never reached: setup() always ends in deep sleep.
}
