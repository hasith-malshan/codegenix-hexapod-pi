#!/usr/bin/env python3
"""
Beautiful WS2812B LED Animations for Raspberry Pi 4
Hardware: 60-LED WS2812B strip (LE0070)

Wiring:
    DATA IN → GPIO 18 (Pin 12) via 300-470Ω resistor
    5V      → External 5V PSU (NOT Pi 5V pin)
    GND     → External PSU GND + Pi GND Pin 6 (shared ground)

Install: sudo pip3 install rpi_ws281x --break-system-packages
Run:     sudo python3 led_animations.py
"""

import time, math, random, colorsys
from rpi_ws281x import PixelStrip, Color

# ── Config ─────────────────────────────────────────────────────────────────────
LED_PIN        = 18
NUM_LEDS       = 60
BRIGHTNESS     = 200        # 0-255 (higher = more vibrant)
LED_FREQ_HZ    = 800_000
LED_DMA        = 10
LED_INVERT     = False
LED_CHANNEL    = 0

# Animation durations (seconds each before switching)
DURATION = 12

# ── Core helpers ───────────────────────────────────────────────────────────────

def hsv(h, s=1.0, v=1.0):
    """h: 0.0-1.0, s: 0.0-1.0, v: 0.0-1.0 → Color"""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return Color(int(r * 255), int(g * 255), int(b * 255))

def wheel(pos):
    """Smooth colour wheel 0-255 → Color (no dead spots)."""
    pos = pos % 256
    if pos < 85:
        return Color(pos * 3, 255 - pos * 3, 0)
    elif pos < 170:
        pos -= 85
        return Color(255 - pos * 3, 0, pos * 3)
    else:
        pos -= 170
        return Color(0, pos * 3, 255 - pos * 3)

def blend(c1, c2, t):
    """Linear blend between two Colors. t: 0.0-1.0"""
    r1,g1,b1 = (c1>>16)&0xFF,(c1>>8)&0xFF,c1&0xFF
    r2,g2,b2 = (c2>>16)&0xFF,(c2>>8)&0xFF,c2&0xFF
    return Color(int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t))

def dim(c, factor):
    """Scale a Color brightness by factor 0.0-1.0"""
    return Color(int(((c>>16)&0xFF)*factor),
                 int(((c>>8)&0xFF)*factor),
                 int((c&0xFF)*factor))

def add_c(c1, c2):
    """Saturating add two Colors."""
    return Color(min(255,((c1>>16)&0xFF)+((c2>>16)&0xFF)),
                 min(255,((c1>>8)&0xFF)+((c2>>8)&0xFF)),
                 min(255,(c1&0xFF)+(c2&0xFF)))

def set_all(strip, color):
    for i in range(NUM_LEDS):
        strip.setPixelColor(i, color)

def t():
    return time.time()

# ── 1. Smooth Rainbow Wave ─────────────────────────────────────────────────────
def rainbow_wave(strip, duration=DURATION):
    """Buttery smooth flowing rainbow — best overall."""
    start = t()
    offset = 0.0
    while t() - start < duration:
        for i in range(NUM_LEDS):
            hue = (i / NUM_LEDS + offset) % 1.0
            strip.setPixelColor(i, hsv(hue))
        strip.show()
        offset += 0.008
        time.sleep(0.016)   # ~60 fps

# ── 2. Breathing / Pulse ───────────────────────────────────────────────────────
def breathing(strip, duration=DURATION):
    """Whole strip inhales and exhales in colour."""
    start = t()
    hue = 0.0
    while t() - start < duration:
        elapsed = t() - start
        # Sine-based brightness 0.02 → 1.0
        bri = (math.sin(elapsed * 1.2) + 1) / 2 * 0.98 + 0.02
        color = hsv(hue, 1.0, bri)
        set_all(strip, color)
        strip.show()
        hue += 0.001
        time.sleep(0.016)

# ── 3. Meteor Rain ─────────────────────────────────────────────────────────────
def meteor_rain(strip, duration=DURATION):
    """Bright meteors streak down the strip with fading tails."""
    start = t()
    trail_len = 12
    decay = 0.75
    buf = [Color(0,0,0)] * NUM_LEDS
    meteors = []   # each: [pos(float), hue, speed]
    spawn_t = 0.0

    while t() - start < duration:
        now = t() - start
        # Spawn a new meteor every ~0.6s
        if now - spawn_t > 0.6:
            meteors.append([0.0, random.random(), random.uniform(0.4, 0.9)])
            spawn_t = now

        # Fade all pixels
        buf = [dim(c, decay) for c in buf]

        # Advance and draw meteors
        live = []
        for m in meteors:
            m[0] += m[2]
            pos = int(m[0])
            if pos < NUM_LEDS + trail_len:
                for t_off in range(trail_len):
                    idx = pos - t_off
                    if 0 <= idx < NUM_LEDS:
                        bri = (1 - t_off / trail_len) ** 2
                        buf[idx] = add_c(buf[idx], dim(hsv(m[1], 0.6 + 0.4 * (1 - t_off/trail_len)), bri))
                live.append(m)
        meteors = live

        for i, c in enumerate(buf):
            strip.setPixelColor(i, c)
        strip.show()
        time.sleep(0.018)

# ── 4. Fire v2 (ultra smooth) ─────────────────────────────────────────────────
def fire(strip, duration=DURATION):
    """High-resolution fire with smooth heat diffusion."""
    start = t()
    COOLING   = 40
    SPARKING  = 110
    heat = [0.0] * NUM_LEDS

    while t() - start < duration:
        # Cool down
        for i in range(NUM_LEDS):
            heat[i] = max(0.0, heat[i] - random.uniform(0, COOLING / 255))

        # Diffuse upward (smooth float version)
        for k in range(NUM_LEDS - 1, 1, -1):
            heat[k] = (heat[k-1] * 0.5 + heat[k-2] * 0.3 + heat[k] * 0.2)

        # Ignite sparks
        if random.randint(0, 255) < SPARKING:
            y = random.randint(0, 5)
            heat[y] = min(1.0, heat[y] + random.uniform(0.5, 1.0))

        # Map heat → flame colour (black→red→yellow→white)
        for j in range(NUM_LEDS):
            h = heat[j]
            if h < 0.33:
                r, g, b = h * 3, 0, 0
            elif h < 0.66:
                r, g, b = 1.0, (h - 0.33) * 3, 0
            else:
                r, g, b = 1.0, 1.0, (h - 0.66) * 3
            strip.setPixelColor(j, Color(int(r*255), int(g*255), int(b*255)))
        strip.show()
        time.sleep(0.016)

# ── 5. Twinkle Stars ──────────────────────────────────────────────────────────
def twinkle(strip, duration=DURATION):
    """Random stars twinkle in and out smoothly."""
    start = t()
    # state per LED: [brightness, direction, hue, speed]
    state = [[random.random(), random.choice([-1,1]),
              random.random(), random.uniform(0.01,0.04)]
             for _ in range(NUM_LEDS)]

    while t() - start < duration:
        for i in range(NUM_LEDS):
            bri, d, hue, spd = state[i]
            bri += d * spd
            if bri >= 1.0: bri = 1.0; d = -1
            if bri <= 0.0:
                bri = 0.0; d = 1
                hue = random.random()
                spd = random.uniform(0.01, 0.04)
            state[i] = [bri, d, hue, spd]
            strip.setPixelColor(i, hsv(hue, 1.0, bri ** 2))
        strip.show()
        time.sleep(0.016)

# ── 6. Color Wipe / Chase ─────────────────────────────────────────────────────
def color_chase(strip, duration=DURATION):
    """Vivid colour blocks chase each other across the strip."""
    start = t()
    num_colors = 4
    palette = [hsv(i / num_colors) for i in range(num_colors)]
    seg = NUM_LEDS // num_colors
    offset = 0.0

    while t() - start < duration:
        for i in range(NUM_LEDS):
            idx = int((i + offset) % NUM_LEDS)
            color_i = idx // seg % num_colors
            strip.setPixelColor(i, palette[color_i])
        strip.show()
        offset += 0.3
        time.sleep(0.016)

# ── 7. Lightning ──────────────────────────────────────────────────────────────
def lightning(strip, duration=DURATION):
    """Sudden white flashes followed by deep blue calm."""
    start = t()
    calm_color = hsv(0.65, 0.9, 0.04)   # deep dark blue background

    while t() - start < duration:
        set_all(strip, calm_color)
        strip.show()
        time.sleep(random.uniform(0.5, 1.5))

        # Flash sequence
        flashes = random.randint(1, 4)
        for _ in range(flashes):
            bright = Color(255, 255, 255)
            seg_start = random.randint(0, NUM_LEDS - 10)
            seg_len   = random.randint(5, 20)
            for i in range(seg_start, min(seg_start + seg_len, NUM_LEDS)):
                strip.setPixelColor(i, bright)
            strip.show()
            time.sleep(random.uniform(0.02, 0.08))
            set_all(strip, calm_color)
            strip.show()
            time.sleep(random.uniform(0.03, 0.1))

# ── 8. Plasma / Lava ──────────────────────────────────────────────────────────
def plasma(strip, duration=DURATION):
    """Flowing interference pattern — psychedelic but smooth."""
    start = t()
    while t() - start < duration:
        now = t() - start
        for i in range(NUM_LEDS):
            v = (math.sin(i * 0.3 + now * 2.0) +
                 math.sin(i * 0.1 + now * 1.3) +
                 math.sin((i + now * 20) * 0.2)) / 3.0
            hue = (v + 1) / 2
            strip.setPixelColor(i, hsv(hue, 1.0, 0.85))
        strip.show()
        time.sleep(0.016)

# ── 9. Running Dots ───────────────────────────────────────────────────────────
def running_dots(strip, duration=DURATION):
    """Multiple coloured dots fly around the strip."""
    start = t()
    num_dots = 6
    dots = [[i * (NUM_LEDS // num_dots), random.random(),
             random.uniform(0.3, 0.7)] for i in range(num_dots)]

    while t() - start < duration:
        set_all(strip, Color(0, 0, 0))
        for d in dots:
            pos, hue, spd = d
            # Draw dot with gaussian-like spread
            for offset in range(-3, 4):
                idx = int(pos + offset) % NUM_LEDS
                bri = math.exp(-0.5 * (offset / 1.5) ** 2)
                strip.setPixelColor(idx, add_c(
                    strip.getPixelColor(idx),
                    dim(hsv(hue), bri)
                ))
            d[0] = (d[0] + spd) % NUM_LEDS
        strip.show()
        time.sleep(0.016)

# ── 10. Color Ripple ──────────────────────────────────────────────────────────
def ripple(strip, duration=DURATION):
    """Ripples expand outward from random points like water drops."""
    start = t()
    drops = []   # [origin, birth_time, hue]
    last_drop = 0.0

    while t() - start < duration:
        now = t() - start

        if now - last_drop > 0.8:
            drops.append([random.randint(0, NUM_LEDS - 1), now, random.random()])
            last_drop = now

        buf = [Color(0, 0, 0)] * NUM_LEDS
        live = []
        for origin, birth, hue in drops:
            age = now - birth
            radius = age * 25          # pixels/second expansion
            if radius > NUM_LEDS:
                continue
            live.append((origin, birth, hue))
            bri = max(0.0, 1.0 - age * 0.5)
            for i in range(NUM_LEDS):
                dist = min(abs(i - origin), NUM_LEDS - abs(i - origin))
                diff = abs(dist - radius)
                if diff < 3:
                    intensity = bri * math.exp(-0.5 * (diff / 1.2) ** 2)
                    buf[i] = add_c(buf[i], dim(hsv(hue, 1.0, intensity), 1.0))
        drops = live

        for i, c in enumerate(buf):
            strip.setPixelColor(i, c)
        strip.show()
        time.sleep(0.018)

# ── Main loop ──────────────────────────────────────────────────────────────────

ANIMATIONS = [
    ("Smooth Rainbow Wave",  rainbow_wave),
    ("Meteor Rain",          meteor_rain),
    ("Plasma / Lava",        plasma),
    ("Fire v2",              fire),
    ("Twinkle Stars",        twinkle),
    ("Ripple",               ripple),
    ("Running Dots",         running_dots),
    ("Lightning",            lightning),
    ("Color Chase",          color_chase),
    ("Breathing Pulse",      breathing),
]

def fade_out(strip, steps=30):
    """Smoothly fade the strip to black before switching animations."""
    for step in range(steps, -1, -1):
        f = step / steps
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, dim(strip.getPixelColor(i), f))
        strip.show()
        time.sleep(0.016)

def main():
    strip = PixelStrip(NUM_LEDS, LED_PIN, LED_FREQ_HZ,
                       LED_DMA, LED_INVERT, BRIGHTNESS, LED_CHANNEL)
    strip.begin()
    print("✨ LED animations running — Ctrl+C to stop\n")

    try:
        while True:
            for name, fn in ANIMATIONS:
                print(f"→ {name}")
                fn(strip, DURATION)
                fade_out(strip)
    except KeyboardInterrupt:
        fade_out(strip)
        set_all(strip, Color(0, 0, 0))
        strip.show()
        print("\nLEDs off. Goodbye!")

if __name__ == "__main__":
    main()