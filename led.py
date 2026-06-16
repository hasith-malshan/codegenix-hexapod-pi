#!/usr/bin/env python3
"""
LED Animations for WS2812B strip on Raspberry Pi 4
Exact port of FastLED Arduino sketch.

Wiring:
    DATA IN → GPIO 18 (Pin 12) via 300–500Ω resistor
    5V      → External 5V PSU
    GND     → External PSU GND + Pi GND (shared)

Run: sudo python3 led_animations.py
"""

import time
import math
import random
from rpi_ws281x import PixelStrip, Color

# ── Config (match your Arduino defines) ───────────────────────────────────────
LED_PIN        = 18
NUM_LEDS       = 60
BRIGHTNESS     = 100
LED_FREQ_HZ    = 800_000
LED_DMA        = 10
LED_INVERT     = False
LED_CHANNEL    = 0

# ── Internal LED buffer (mirrors CRGB leds[NUM_LEDS]) ─────────────────────────
# Each entry is [r, g, b]
leds = [[0, 0, 0] for _ in range(NUM_LEDS)]

strip = None   # set in main()

# ── FastLED primitives ────────────────────────────────────────────────────────

def hsv_to_rgb(h, s, v):
    """HSV (0-255 each) → (r, g, b) 0-255. Matches FastLED CHSV→CRGB."""
    if s == 0:
        return v, v, v
    h6 = (h / 255.0) * 6.0
    i  = int(h6)
    f  = h6 - i
    v  /= 255.0
    s  /= 255.0
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    v = int(v * 255); p = int(p * 255); q = int(q * 255); t = int(t * 255)
    return [(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)][i % 6]


def chsv(h, s, v):
    """Return [r, g, b] for a CHSV colour."""
    return list(hsv_to_rgb(h, s, v))


def qadd8(a, b):
    """Saturating add, 8-bit (FastLED qadd8)."""
    return min(255, a + b)


def qsub8(a, b):
    """Saturating subtract, 8-bit (FastLED qsub8)."""
    return max(0, a - b)


def fade_to_black_by(amount):
    """Dim every LED by amount/256 — exact FastLED fadeToBlackBy."""
    scale = (256 - amount) / 256.0
    for i in range(NUM_LEDS):
        leds[i][0] = int(leds[i][0] * scale)
        leds[i][1] = int(leds[i][1] * scale)
        leds[i][2] = int(leds[i][2] * scale)


def add_to_led(pos, rgb):
    """leds[pos] += CHSV(...) — saturating per-channel add."""
    leds[pos][0] = qadd8(leds[pos][0], rgb[0])
    leds[pos][1] = qadd8(leds[pos][1], rgb[1])
    leds[pos][2] = qadd8(leds[pos][2], rgb[2])


def or_to_led(pos, rgb):
    """leds[pos] |= CHSV(...) — bitwise OR per channel."""
    leds[pos][0] |= rgb[0]
    leds[pos][1] |= rgb[1]
    leds[pos][2] |= rgb[2]


def millis():
    """Milliseconds since epoch, like Arduino millis()."""
    return int(time.time() * 1000)


def beatsin16(bpm, low, high):
    """
    FastLED beatsin16: sine wave at `bpm` BPM, output range [low, high].
    Uses a shared time base (millis) exactly like the Arduino version.
    """
    t = millis()
    beat = (t * bpm / 60000.0) % 1.0          # 0.0–1.0 within one beat
    s = (math.sin(beat * 2 * math.pi) + 1) / 2.0  # 0.0–1.0
    return int(round(low + s * (high - low)))


def beatsin8(bpm, low=0, high=255):
    """beatsin8 — same as beatsin16 but clamped to uint8 range."""
    return beatsin16(bpm, low, high)


def random8(lo=0, hi=255):
    """random8([lo,] hi) — matches FastLED random8 (hi is inclusive)."""
    return random.randint(lo, hi)


def random16(hi):
    """random16(hi) — 0 to hi-1, matches FastLED random16(n)."""
    return random.randint(0, hi - 1)


def fill_rainbow(start_hue, delta_hue=7):
    """
    FastLED fill_rainbow: fills leds[] with a rainbow starting at start_hue,
    incrementing by delta_hue per LED.
    """
    h = start_hue
    for i in range(NUM_LEDS):
        leds[i] = chsv(h % 256, 255, 255)
        h += delta_hue


def heat_color(heat_val):
    """
    FastLED HeatColor: maps 0-255 heat → black/red/yellow/white flame colour.
    Exact replication of the FastLED algorithm.
    """
    # Scale heat 0-191
    t192 = (heat_val * 191) >> 8

    heatramp = (t192 & 0x3F) << 2   # 0-252, steps of 4

    if t192 > 0x80:          # hottest: yellow → white
        r, g, b = 255, 255, heatramp
    elif t192 > 0x40:        # medium: red → yellow
        r, g, b = 255, heatramp, 0
    else:                    # coolest: black → red
        r, g, b = heatramp, 0, 0
    return [r, g, b]


# PartyColors palette — 16 colours matching FastLED's PartyColors_p
_PARTY_COLORS = [
    (85,  0,  171), (132,  0, 114), (192,  0,  48), (255, 55,   0),
    (255,165,   0), (220,255,   0), (121,255,   0), (  0,255,  45),
    (  0,195, 255), (  0, 41, 255), ( 45,  0, 255), (255,  0, 255),
    (255,  0, 128), (255,  0,  64), (255,  0,   0), (185,  0,  255),
]

def color_from_palette(index, brightness):
    """
    Approximate FastLED ColorFromPalette with PartyColors_p.
    index 0-255, brightness 0-255.
    """
    index = index % 256
    slot  = (index * 16) // 256          # which of the 16 palette entries
    r, g, b = _PARTY_COLORS[slot % 16]
    scale = brightness / 255.0
    return [int(r * scale), int(g * scale), int(b * scale)]


def fast_led_show():
    """Push leds[] buffer to the physical strip."""
    for i in range(NUM_LEDS):
        strip.setPixelColor(i, Color(leds[i][0], leds[i][1], leds[i][2]))
    strip.show()


# ── Animations (exact Arduino logic) ─────────────────────────────────────────

def rainbow():
    """
    void rainbow() {
      for (int j = 0; j < 256; j++) {
        fill_rainbow(leds, NUM_LEDS, j, 7);
        FastLED.show(); delay(20);
      }
    }
    """
    for j in range(256):
        fill_rainbow(j, 7)
        fast_led_show()
        time.sleep(0.020)


def confetti():
    """
    void confetti() {
      for (int i = 0; i < 300; i++) {
        fadeToBlackBy(leds, NUM_LEDS, 10);
        int pos = random16(NUM_LEDS);
        leds[pos] += CHSV(random8(), 255, 255);
        FastLED.show(); delay(20);
      }
    }
    """
    for _ in range(300):
        fade_to_black_by(10)
        pos = random16(NUM_LEDS)
        add_to_led(pos, chsv(random8(0, 255), 255, 255))
        fast_led_show()
        time.sleep(0.020)


def sinelon():
    """
    void sinelon() {
      for (int i = 0; i < 400; i++) {
        fadeToBlackBy(leds, NUM_LEDS, 20);
        int pos = beatsin16(13, 0, NUM_LEDS - 1);
        leds[pos] += CHSV(millis() / 10, 255, 255);
        FastLED.show(); delay(10);
      }
    }
    """
    for _ in range(400):
        fade_to_black_by(20)
        pos = beatsin16(13, 0, NUM_LEDS - 1)
        hue = (millis() // 10) % 256
        add_to_led(pos, chsv(hue, 255, 255))
        fast_led_show()
        time.sleep(0.010)


def bpm():
    """
    void bpm() {
      uint8_t BeatsPerMinute = 62;
      CRGBPalette16 palette = PartyColors_p;
      for (int i = 0; i < 400; i++) {
        uint8_t beat = beatsin8(BeatsPerMinute, 64, 255);
        for (int j = 0; j < NUM_LEDS; j++) {
          leds[j] = ColorFromPalette(palette, (j*2)+millis()/10, beat-(j*10));
        }
        FastLED.show(); delay(20);
      }
    }
    """
    beats_per_minute = 62
    for _ in range(400):
        beat = beatsin8(beats_per_minute, 64, 255)
        t = millis() // 10
        for j in range(NUM_LEDS):
            index      = (j * 2 + t) % 256
            brightness = max(0, beat - j * 10)
            leds[j]    = color_from_palette(index, brightness)
        fast_led_show()
        time.sleep(0.020)


def juggle():
    """
    void juggle() {
      for (int i = 0; i < 400; i++) {
        fadeToBlackBy(leds, NUM_LEDS, 20);
        byte dothue = 0;
        for (int j = 0; j < 8; j++) {
          leds[beatsin16(j+7, 0, NUM_LEDS-1)] |= CHSV(dothue, 200, 255);
          dothue += 32;
        }
        FastLED.show(); delay(20);
      }
    }
    """
    for _ in range(400):
        fade_to_black_by(20)
        dothue = 0
        for j in range(8):
            pos = beatsin16(j + 7, 0, NUM_LEDS - 1)
            or_to_led(pos, chsv(dothue % 256, 200, 255))
            dothue = (dothue + 32) & 0xFF
        fast_led_show()
        time.sleep(0.020)


def fire_effect():
    """
    void fireEffect() {
      static byte heat[NUM_LEDS];
      for (int loop = 0; loop < 500; loop++) {
        for (int i = 0; i < NUM_LEDS; i++)
          heat[i] = qsub8(heat[i], random8(0, 20));
        for (int k = NUM_LEDS-1; k >= 2; k--)
          heat[k] = (heat[k-1] + heat[k-2] + heat[k-2]) / 3;
        if (random8() < 120) {
          int y = random8(7);
          heat[y] = qadd8(heat[y], random8(160, 255));
        }
        for (int j = 0; j < NUM_LEDS; j++)
          leds[j] = HeatColor(heat[j]);
        FastLED.show(); delay(20);
      }
    }
    """
    heat = [0] * NUM_LEDS   # static byte heat[NUM_LEDS]

    for _ in range(500):
        # Step 1: cool down
        for i in range(NUM_LEDS):
            heat[i] = qsub8(heat[i], random8(0, 20))

        # Step 2: drift heat upward
        for k in range(NUM_LEDS - 1, 1, -1):
            heat[k] = (heat[k-1] + heat[k-2] + heat[k-2]) // 3

        # Step 3: ignite sparks at base
        if random8(0, 255) < 120:
            y = random8(0, 6)          # random8(7) → 0..6
            heat[y] = qadd8(heat[y], random8(160, 255))

        # Step 4: render
        for j in range(NUM_LEDS):
            leds[j] = heat_color(heat[j])

        fast_led_show()
        time.sleep(0.020)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global strip
    strip = PixelStrip(
        NUM_LEDS, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, BRIGHTNESS, LED_CHANNEL
    )
    strip.begin()
    print("Running — Ctrl+C to stop.")

    try:
        while True:
            print("→ Rainbow");    rainbow()
            print("→ Confetti");   confetti()
            print("→ Sinelon");    sinelon()
            print("→ BPM");        bpm()
            print("→ Juggle");     juggle()
            print("→ Fire");       fire_effect()
    except KeyboardInterrupt:
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()
        print("\nLEDs off. Bye!")


if __name__ == "__main__":
    main()#!/usr/bin/env python3
"""
LED Animations for WS2812B strip on Raspberry Pi 4
Exact port of FastLED Arduino sketch.

Wiring:
    DATA IN → GPIO 18 (Pin 12) via 300–500Ω resistor
    5V      → External 5V PSU
    GND     → External PSU GND + Pi GND (shared)

Run: sudo python3 led_animations.py
"""

import time
import math
import random
from rpi_ws281x import PixelStrip, Color

# ── Config (match your Arduino defines) ───────────────────────────────────────
LED_PIN        = 18
NUM_LEDS       = 60
BRIGHTNESS     = 100
LED_FREQ_HZ    = 800_000
LED_DMA        = 10
LED_INVERT     = False
LED_CHANNEL    = 0

# ── Internal LED buffer (mirrors CRGB leds[NUM_LEDS]) ─────────────────────────
# Each entry is [r, g, b]
leds = [[0, 0, 0] for _ in range(NUM_LEDS)]

strip = None   # set in main()

# ── FastLED primitives ────────────────────────────────────────────────────────

def hsv_to_rgb(h, s, v):
    """HSV (0-255 each) → (r, g, b) 0-255. Matches FastLED CHSV→CRGB."""
    if s == 0:
        return v, v, v
    h6 = (h / 255.0) * 6.0
    i  = int(h6)
    f  = h6 - i
    v  /= 255.0
    s  /= 255.0
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    v = int(v * 255); p = int(p * 255); q = int(q * 255); t = int(t * 255)
    return [(v,t,p),(q,v,p),(p,v,t),(p,q,v),(t,p,v),(v,p,q)][i % 6]


def chsv(h, s, v):
    """Return [r, g, b] for a CHSV colour."""
    return list(hsv_to_rgb(h, s, v))


def qadd8(a, b):
    """Saturating add, 8-bit (FastLED qadd8)."""
    return min(255, a + b)


def qsub8(a, b):
    """Saturating subtract, 8-bit (FastLED qsub8)."""
    return max(0, a - b)


def fade_to_black_by(amount):
    """Dim every LED by amount/256 — exact FastLED fadeToBlackBy."""
    scale = (256 - amount) / 256.0
    for i in range(NUM_LEDS):
        leds[i][0] = int(leds[i][0] * scale)
        leds[i][1] = int(leds[i][1] * scale)
        leds[i][2] = int(leds[i][2] * scale)


def add_to_led(pos, rgb):
    """leds[pos] += CHSV(...) — saturating per-channel add."""
    leds[pos][0] = qadd8(leds[pos][0], rgb[0])
    leds[pos][1] = qadd8(leds[pos][1], rgb[1])
    leds[pos][2] = qadd8(leds[pos][2], rgb[2])


def or_to_led(pos, rgb):
    """leds[pos] |= CHSV(...) — bitwise OR per channel."""
    leds[pos][0] |= rgb[0]
    leds[pos][1] |= rgb[1]
    leds[pos][2] |= rgb[2]


def millis():
    """Milliseconds since epoch, like Arduino millis()."""
    return int(time.time() * 1000)


def beatsin16(bpm, low, high):
    """
    FastLED beatsin16: sine wave at `bpm` BPM, output range [low, high].
    Uses a shared time base (millis) exactly like the Arduino version.
    """
    t = millis()
    beat = (t * bpm / 60000.0) % 1.0          # 0.0–1.0 within one beat
    s = (math.sin(beat * 2 * math.pi) + 1) / 2.0  # 0.0–1.0
    return int(round(low + s * (high - low)))


def beatsin8(bpm, low=0, high=255):
    """beatsin8 — same as beatsin16 but clamped to uint8 range."""
    return beatsin16(bpm, low, high)


def random8(lo=0, hi=255):
    """random8([lo,] hi) — matches FastLED random8 (hi is inclusive)."""
    return random.randint(lo, hi)


def random16(hi):
    """random16(hi) — 0 to hi-1, matches FastLED random16(n)."""
    return random.randint(0, hi - 1)


def fill_rainbow(start_hue, delta_hue=7):
    """
    FastLED fill_rainbow: fills leds[] with a rainbow starting at start_hue,
    incrementing by delta_hue per LED.
    """
    h = start_hue
    for i in range(NUM_LEDS):
        leds[i] = chsv(h % 256, 255, 255)
        h += delta_hue


def heat_color(heat_val):
    """
    FastLED HeatColor: maps 0-255 heat → black/red/yellow/white flame colour.
    Exact replication of the FastLED algorithm.
    """
    # Scale heat 0-191
    t192 = (heat_val * 191) >> 8

    heatramp = (t192 & 0x3F) << 2   # 0-252, steps of 4

    if t192 > 0x80:          # hottest: yellow → white
        r, g, b = 255, 255, heatramp
    elif t192 > 0x40:        # medium: red → yellow
        r, g, b = 255, heatramp, 0
    else:                    # coolest: black → red
        r, g, b = heatramp, 0, 0
    return [r, g, b]


# PartyColors palette — 16 colours matching FastLED's PartyColors_p
_PARTY_COLORS = [
    (85,  0,  171), (132,  0, 114), (192,  0,  48), (255, 55,   0),
    (255,165,   0), (220,255,   0), (121,255,   0), (  0,255,  45),
    (  0,195, 255), (  0, 41, 255), ( 45,  0, 255), (255,  0, 255),
    (255,  0, 128), (255,  0,  64), (255,  0,   0), (185,  0,  255),
]

def color_from_palette(index, brightness):
    """
    Approximate FastLED ColorFromPalette with PartyColors_p.
    index 0-255, brightness 0-255.
    """
    index = index % 256
    slot  = (index * 16) // 256          # which of the 16 palette entries
    r, g, b = _PARTY_COLORS[slot % 16]
    scale = brightness / 255.0
    return [int(r * scale), int(g * scale), int(b * scale)]


def fast_led_show():
    """Push leds[] buffer to the physical strip."""
    for i in range(NUM_LEDS):
        strip.setPixelColor(i, Color(leds[i][0], leds[i][1], leds[i][2]))
    strip.show()


# ── Animations (exact Arduino logic) ─────────────────────────────────────────

def rainbow():
    """
    void rainbow() {
      for (int j = 0; j < 256; j++) {
        fill_rainbow(leds, NUM_LEDS, j, 7);
        FastLED.show(); delay(20);
      }
    }
    """
    for j in range(256):
        fill_rainbow(j, 7)
        fast_led_show()
        time.sleep(0.020)


def confetti():
    """
    void confetti() {
      for (int i = 0; i < 300; i++) {
        fadeToBlackBy(leds, NUM_LEDS, 10);
        int pos = random16(NUM_LEDS);
        leds[pos] += CHSV(random8(), 255, 255);
        FastLED.show(); delay(20);
      }
    }
    """
    for _ in range(300):
        fade_to_black_by(10)
        pos = random16(NUM_LEDS)
        add_to_led(pos, chsv(random8(0, 255), 255, 255))
        fast_led_show()
        time.sleep(0.020)


def sinelon():
    """
    void sinelon() {
      for (int i = 0; i < 400; i++) {
        fadeToBlackBy(leds, NUM_LEDS, 20);
        int pos = beatsin16(13, 0, NUM_LEDS - 1);
        leds[pos] += CHSV(millis() / 10, 255, 255);
        FastLED.show(); delay(10);
      }
    }
    """
    for _ in range(400):
        fade_to_black_by(20)
        pos = beatsin16(13, 0, NUM_LEDS - 1)
        hue = (millis() // 10) % 256
        add_to_led(pos, chsv(hue, 255, 255))
        fast_led_show()
        time.sleep(0.010)


def bpm():
    """
    void bpm() {
      uint8_t BeatsPerMinute = 62;
      CRGBPalette16 palette = PartyColors_p;
      for (int i = 0; i < 400; i++) {
        uint8_t beat = beatsin8(BeatsPerMinute, 64, 255);
        for (int j = 0; j < NUM_LEDS; j++) {
          leds[j] = ColorFromPalette(palette, (j*2)+millis()/10, beat-(j*10));
        }
        FastLED.show(); delay(20);
      }
    }
    """
    beats_per_minute = 62
    for _ in range(400):
        beat = beatsin8(beats_per_minute, 64, 255)
        t = millis() // 10
        for j in range(NUM_LEDS):
            index      = (j * 2 + t) % 256
            brightness = max(0, beat - j * 10)
            leds[j]    = color_from_palette(index, brightness)
        fast_led_show()
        time.sleep(0.020)


def juggle():
    """
    void juggle() {
      for (int i = 0; i < 400; i++) {
        fadeToBlackBy(leds, NUM_LEDS, 20);
        byte dothue = 0;
        for (int j = 0; j < 8; j++) {
          leds[beatsin16(j+7, 0, NUM_LEDS-1)] |= CHSV(dothue, 200, 255);
          dothue += 32;
        }
        FastLED.show(); delay(20);
      }
    }
    """
    for _ in range(400):
        fade_to_black_by(20)
        dothue = 0
        for j in range(8):
            pos = beatsin16(j + 7, 0, NUM_LEDS - 1)
            or_to_led(pos, chsv(dothue % 256, 200, 255))
            dothue = (dothue + 32) & 0xFF
        fast_led_show()
        time.sleep(0.020)


def fire_effect():
    """
    void fireEffect() {
      static byte heat[NUM_LEDS];
      for (int loop = 0; loop < 500; loop++) {
        for (int i = 0; i < NUM_LEDS; i++)
          heat[i] = qsub8(heat[i], random8(0, 20));
        for (int k = NUM_LEDS-1; k >= 2; k--)
          heat[k] = (heat[k-1] + heat[k-2] + heat[k-2]) / 3;
        if (random8() < 120) {
          int y = random8(7);
          heat[y] = qadd8(heat[y], random8(160, 255));
        }
        for (int j = 0; j < NUM_LEDS; j++)
          leds[j] = HeatColor(heat[j]);
        FastLED.show(); delay(20);
      }
    }
    """
    heat = [0] * NUM_LEDS   # static byte heat[NUM_LEDS]

    for _ in range(500):
        # Step 1: cool down
        for i in range(NUM_LEDS):
            heat[i] = qsub8(heat[i], random8(0, 20))

        # Step 2: drift heat upward
        for k in range(NUM_LEDS - 1, 1, -1):
            heat[k] = (heat[k-1] + heat[k-2] + heat[k-2]) // 3

        # Step 3: ignite sparks at base
        if random8(0, 255) < 120:
            y = random8(0, 6)          # random8(7) → 0..6
            heat[y] = qadd8(heat[y], random8(160, 255))

        # Step 4: render
        for j in range(NUM_LEDS):
            leds[j] = heat_color(heat[j])

        fast_led_show()
        time.sleep(0.020)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global strip
    strip = PixelStrip(
        NUM_LEDS, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, BRIGHTNESS, LED_CHANNEL
    )
    strip.begin()
    print("Running — Ctrl+C to stop.")

    try:
        while True:
            print("→ Rainbow");    rainbow()
            print("→ Confetti");   confetti()
            print("→ Sinelon");    sinelon()
            print("→ BPM");        bpm()
            print("→ Juggle");     juggle()
            print("→ Fire");       fire_effect()
    except KeyboardInterrupt:
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()
        print("\nLEDs off. Bye!")


if __name__ == "__main__":
    main()