#include <FastLED.h>

#define LED_PIN     23
#define NUM_LEDS    60
#define BRIGHTNESS  120
#define LED_TYPE    WS2812B
#define COLOR_ORDER GRB

CRGB leds[NUM_LEDS];

void setup() {
  FastLED.addLeds<LED_TYPE, LED_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.clear();
}

// ================= MAIN LOOP =================

void loop() {
  cyberGlow();
  runningPulse();
  scannerEye();
  lavaFlow();
}

// ================= CYBER GLOW =================

void cyberGlow() {
  for (int t = 0; t < 600; t++) {
    uint8_t brightness = beatsin8(10, 30, 255);

    fill_solid(leds, NUM_LEDS, CHSV(180, 255, brightness));

    FastLED.show();
    delay(20);
  }
}

// ================= RUNNING PULSE =================

void runningPulse() {
  for (int t = 0; t < 500; t++) {

    fadeToBlackBy(leds, NUM_LEDS, 30);

    int pos = beatsin16(18, 0, NUM_LEDS - 1);

    leds[pos] = CRGB::Blue;

    if (pos > 0) leds[pos - 1] = CRGB(0, 0, 120);
    if (pos > 1) leds[pos - 2] = CRGB(0, 0, 40);

    if (pos < NUM_LEDS - 1) leds[pos + 1] = CRGB(0, 0, 120);
    if (pos < NUM_LEDS - 2) leds[pos + 2] = CRGB(0, 0, 40);

    FastLED.show();
    delay(15);
  }
}

// ================= SCANNER EYE =================

void scannerEye() {

  for (int cycle = 0; cycle < 8; cycle++) {

    for (int pos = 0; pos < NUM_LEDS; pos++) {

      fill_solid(leds, NUM_LEDS, CRGB::Black);

      leds[pos] = CRGB::Red;

      if (pos > 0)
        leds[pos - 1] = CRGB(80, 0, 0);

      if (pos > 1)
        leds[pos - 2] = CRGB(20, 0, 0);

      FastLED.show();
      delay(20);
    }

    for (int pos = NUM_LEDS - 1; pos >= 0; pos--) {

      fill_solid(leds, NUM_LEDS, CRGB::Black);

      leds[pos] = CRGB::Red;

      if (pos < NUM_LEDS - 1)
        leds[pos + 1] = CRGB(80, 0, 0);

      if (pos < NUM_LEDS - 2)
        leds[pos + 2] = CRGB(20, 0, 0);

      FastLED.show();
      delay(20);
    }
  }
}

// ================= LAVA FLOW =================

void lavaFlow() {

  static uint8_t heat[NUM_LEDS];

  for (int loop = 0; loop < 700; loop++) {

    for (int i = 0; i < NUM_LEDS; i++) {
      heat[i] = qsub8(heat[i], random8(0, 15));
    }

    for (int k = NUM_LEDS - 1; k >= 2; k--) {
      heat[k] = (heat[k - 1] + heat[k - 2] + heat[k - 2]) / 3;
    }

    if (random8() < 180) {
      int y = random8(7);
      heat[y] = qadd8(heat[y], random8(180, 255));
    }

    for (int j = 0; j < NUM_LEDS; j++) {
      leds[j] = ColorFromPalette(
        LavaColors_p,
        scale8(heat[j], 240)
      );
    }

    FastLED.show();
    delay(20);
  }
}