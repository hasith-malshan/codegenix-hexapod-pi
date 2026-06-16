#!/usr/bin/env python3
"""
LED Animations for WS2812B strip on Raspberry Pi 4
Hardware: LE0070 - WS2812B 60 LEDs/meter (Non-Waterproof)

Requirements:
    sudo pip3 install rpi_ws281x

Wiring:
    - LED Strip DATA IN  → GPIO 18 (Pin 12) via a 300–500Ω resistor
    - LED Strip 5V       → External 5V power supply (NOT the Pi's 5V)
    - LED Strip GND      → External PSU GND AND Pi GND (shared ground)

Run with sudo:
    sudo python3 led_animations.py
"""

import time
import math
import random
from rpi_ws281x import PixelStrip, Color

# ── Configuration ──────────────────────────────────────────────────────────────
LED_PIN        = 18          # GPIO pin (must support PWM: 18 or 12)
NUM_LEDS       = 60          # LEDs on your strip
LED_FREQ_HZ    = 800_000     # WS2812B signal frequency
LED_DMA        = 10          # DMA channel
LED_BRIGHTNESS = 100         # 0–255
LED_INVERT     = False       # True if using NPN transistor level-shift
LED_CHANNEL    = 0


# ── Helpers ────────────────────────────────────────────────────────────────────

def hsv_to_color(h, s, v):
    """Convert HSV (0-255 each) to rpi_ws281x Color (GRB internally handled)."""
    h_f = h / 255.0
    s_f = s / 255.0
    v_f = v / 255.0
    if s_f == 0:
        r = g = b = int(v_f * 255)
        return Color(r, g, b)
    i = int(h_f * 6)
    f = h_f * 6 - i
    p = v_f * (1 - s_f)
    q = v_f * (1 - f * s_f)
    t = v_f * (1 - (1 - f) * s_f)
    i %= 6
    if i == 0: r, g, b = v_f, t, p
    elif i == 1: r, g, b = q, v_f, p
    elif i == 2: r, g, b = p, v_f, t
    elif i == 3: r, g, b = p, q, v_f
    elif i == 4: r, g, b = t, p, v_f
    else:        r, g, b = v_f, p, q
    return Color(int(r * 255), int(g * 255), int(b * 255))


def fade_to_black(strip, amount):
    """Dim every LED by `amount` (0–255), equivalent to FastLED fadeToBlackBy."""
    factor = (255 - amount) / 255.0
    for i in range(strip.numPixels()):
        c = strip.getPixelColor(i)
        r = int(((c >> 16) & 0xFF) * factor)
        g = int(((c >> 8)  & 0xFF) * factor)
        b = int((c         & 0xFF) * factor)
        strip.setPixelColor(i, Color(r, g, b))


def add_color(strip, pos, color):
    """Add (saturating) a colour to an existing pixel, like FastLED's +=."""
    existing = strip.getPixelColor(pos)
    r = min(255, ((existing >> 16) & 0xFF) + ((color >> 16) & 0xFF))
    g = min(255, ((existing >> 8)  & 0xFF) + ((color >> 8)  & 0xFF))
    b = min(255, (existing         & 0xFF) + (color         & 0xFF))
    strip.setPixelColor(pos, Color(r, g, b))


def or_color(strip, pos, color):
    """Bitwise-OR a colour onto an existing pixel, like FastLED's |=."""
    existing = strip.getPixelColor(pos)
    r = ((existing >> 16) & 0xFF) | ((color >> 16) & 0xFF)
    g = ((existing >> 8)  & 0xFF) | ((color >> 8)  & 0xFF)
    b = (existing         & 0xFF) | (color         & 0xFF)
    strip.setPixelColor(pos, Color(r, g, b))


def beatsin16(bpm, low, high, t=None):
    """Sine wave oscillator between low and high at `bpm` beats per minute."""
    if t is None:
        t = time.time()
    beat = (t * bpm / 60.0) % 1.0          # 0.0 – 1.0
    s = (math.sin(beat * 2 * math.pi) + 1) / 2.0   # 0.0 – 1.0
    return int(low + s * (high - low))


def beatsin8(bpm, low=0, high=255, t=None):
    """Same as beatsin16 but returns 0-255 value."""
    return beatsin16(bpm, low, high, t)


def heat_color(heat):
    """Convert a 0-255 heat value to a flame colour (black→red→yellow→white)."""
    t192 = max(0, heat - 0) * 191 // 255
    heatramp = (t192 & 63) << 2          # ramp up 0-252
    if t192 > 128:
        return Color(255, 255, heatramp)
    elif t192 > 64:
        return Color(255, heatramp, 0)
    else:
        return Color(heatramp, 0, 0)


def party_palette_color(index, brightness):
    """Rough approximation of FastLED's PartyColors palette."""
    index = index % 256
    h = index
    s = 255
    v = max(0, min(255, brightness))
    return hsv_to_color(h, s, v)


# ── Animations ─────────────────────────────────────────────────────────────────

def rainbow(strip, iterations=256):
    """Cycle through the full colour wheel across all LEDs."""
    for j in range(iterations):
        for i in range(NUM_LEDS):
            hue = (i * 7 + j) % 256
            strip.setPixelColor(i, hsv_to_color(hue, 255, 255))
        strip.show()
        time.sleep(0.02)


def confetti(strip, iterations=300):
    """Randomly sparkle coloured pixels while fading the rest."""
    for _ in range(iterations):
        fade_to_black(strip, 10)
        pos = random.randint(0, NUM_LEDS - 1)
        add_color(strip, pos, hsv_to_color(random.randint(0, 255), 255, 255))
        strip.show()
        time.sleep(0.02)


def sinelon(strip, iterations=400):
    """A single coloured dot bouncing back and forth (scanner effect)."""
    for _ in range(iterations):
        fade_to_black(strip, 20)
        t = time.time()
        pos = beatsin16(13, 0, NUM_LEDS - 1, t)
        hue = int(t * 25) % 256
        add_color(strip, pos, hsv_to_color(hue, 255, 255))
        strip.show()
        time.sleep(0.01)


def bpm_effect(strip, iterations=400):
    """Palette-driven animation synced to a beats-per-minute value."""
    beats_per_minute = 62
    for _ in range(iterations):
        t = time.time()
        beat = beatsin8(beats_per_minute, 64, 255, t)
        for j in range(NUM_LEDS):
            palette_index = (j * 2 + int(t * 100)) % 256
            brightness    = max(0, beat - j * 10)
            strip.setPixelColor(j, party_palette_color(palette_index, brightness))
        strip.show()
        time.sleep(0.02)


def juggle(strip, iterations=400):
    """Eight coloured dots chase each other around the strip."""
    for _ in range(iterations):
        fade_to_black(strip, 20)
        t = time.time()
        for j in range(8):
            pos = beatsin16(j + 7, 0, NUM_LEDS - 1, t)
            hue = (j * 32) % 256
            or_color(strip, pos, hsv_to_color(hue, 200, 255))
        strip.show()
        time.sleep(0.02)


def fire_effect(strip, iterations=500):
    """Realistic fire simulation (black → red → yellow → white)."""
    heat = [0] * NUM_LEDS

    for _ in range(iterations):
        # Step 1: cool down every cell a little
        for i in range(NUM_LEDS):
            cool = random.randint(0, 19)
            heat[i] = max(0, heat[i] - cool)

        # Step 2: heat drifts up and diffuses
        for k in range(NUM_LEDS - 1, 1, -1):
            heat[k] = (heat[k - 1] + heat[k - 2] + heat[k - 2]) // 3

        # Step 3: randomly ignite new sparks at the base
        if random.randint(0, 255) < 120:
            y = random.randint(0, 6)
            heat[y] = min(255, heat[y] + random.randint(160, 255))

        # Step 4: map heat to colour
        for j in range(NUM_LEDS):
            strip.setPixelColor(j, heat_color(heat[j]))

        strip.show()
        time.sleep(0.02)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    strip = PixelStrip(
        NUM_LEDS, LED_PIN, LED_FREQ_HZ,
        LED_DMA, LED_INVERT, LED_BRIGHTNESS, LED_CHANNEL
    )
    strip.begin()
    print("WS2812B LED animations running — press Ctrl+C to stop.")

    try:
        while True:
            print("→ Rainbow")
            rainbow(strip)

            print("→ Confetti")
            confetti(strip)

            print("→ Sinelon (scanner)")
            sinelon(strip)

            print("→ BPM")
            bpm_effect(strip)

            print("→ Juggle")
            juggle(strip)

            print("→ Fire")
            fire_effect(strip)

    except KeyboardInterrupt:
        # Turn all LEDs off on exit
        for i in range(strip.numPixels()):
            strip.setPixelColor(i, Color(0, 0, 0))
        strip.show()
        print("\nLEDs cleared. Goodbye!")


if __name__ == "__main__":
    main()