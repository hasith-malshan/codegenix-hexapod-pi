#!/usr/bin/env python3
"""
🎯 Left-to-Right Moving Lights for Raspberry Pi 4
All lights moving from left (GPIO side) to right continuously

Install: sudo pip3 install rpi_ws281x --break-system-packages
Run:     sudo python3 led_left_right_motion.py
"""

import time, math, random, colorsys
from rpi_ws281x import PixelStrip, Color

# ── Config ─────────────────────────────────────────────────────────────────────
LED_PIN        = 18
NUM_LEDS       = 60
BRIGHTNESS     = 255
LED_FREQ_HZ    = 800_000
LED_DMA        = 10
LED_INVERT     = False
LED_CHANNEL    = 0
DURATION       = 15

# ── Helpers ────────────────────────────────────────────────────────────────────
def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return Color(int(r * 255), int(g * 255), int(b * 255))

def dim(c, factor):
    return Color(int(((c>>16)&0xFF)*factor),
                 int(((c>>8)&0xFF)*factor),
                 int((c&0xFF)*factor))

def add_c(c1, c2):
    return Color(min(255,((c1>>16)&0xFF)+((c2>>16)&0xFF)),
                 min(255,((c1>>8)&0xFF)+((c2>>8)&0xFF)),
                 min(255,(c1&0xFF)+(c2&0xFF)))

def set_all(strip, color):
    for i in range(NUM_LEDS):
        strip.setPixelColor(i, color)

def t():
    return time.time()

# ════════════════════════════════════════════════════════════════════════════════
# 1️⃣ SIMPLE LEFT-TO-RIGHT MOVING LIGHT
# ════════════════════════════════════════════════════════════════════════════════
def simple_ltr(strip, duration=DURATION):
    """Single bright dot moving left to right smoothly"""
    start = t()
    speed = NUM_LEDS / 3  # Complete traverse in 3 seconds
    
    while t() - start < duration:
        elapsed = t() - start
        pos = (elapsed * speed) % NUM_LEDS
        
        set_all(strip, Color(0, 0, 0))
        
        # Draw moving dot with glow
        center_idx = int(pos)
        for i in range(NUM_LEDS):
            dist = abs(i - pos)
            if dist < 5:
                brightness = max(0, 1.0 - dist / 5)
                strip.setPixelColor(i, dim(Color(255, 100, 0), brightness ** 0.8))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 2️⃣ RAINBOW WAVE LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def rainbow_ltr(strip, duration=DURATION):
    """Colorful rainbow continuously flowing left to right"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        offset = elapsed * 20  # Pixels per second
        
        for i in range(NUM_LEDS):
            # Rainbow based on position + time offset
            hue = ((i + offset) % NUM_LEDS) / NUM_LEDS
            strip.setPixelColor(i, hsv(hue, 1.0, 1.0))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 3️⃣ COMET TRAIL LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def comet_trail(strip, duration=DURATION):
    """Bright head with fading tail moving left to right"""
    start = t()
    speed = NUM_LEDS / 2
    tail_length = 15
    
    while t() - start < duration:
        elapsed = t() - start
        pos = (elapsed * speed) % (NUM_LEDS + tail_length)
        
        set_all(strip, Color(0, 0, 0))
        
        # Draw tail
        for tail_idx in range(tail_length):
            idx = int(pos) - tail_idx
            if 0 <= idx < NUM_LEDS:
                fade = 1.0 - (tail_idx / tail_length)
                brightness = fade ** 1.5
                color = dim(Color(0, 150, 255), brightness)
                strip.setPixelColor(idx, color)
        
        # Draw bright head
        head_idx = int(pos)
        if 0 <= head_idx < NUM_LEDS:
            strip.setPixelColor(head_idx, Color(0, 255, 255))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 4️⃣ MULTIPLE DOTS RACING LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def racing_dots(strip, duration=DURATION):
    """Multiple colored dots racing left to right"""
    start = t()
    num_dots = 6
    colors = [hsv(i / num_dots, 1.0, 1.0) for i in range(num_dots)]
    
    while t() - start < duration:
        elapsed = t() - start
        speed = NUM_LEDS / 2
        
        set_all(strip, Color(0, 0, 0))
        
        for dot_idx in range(num_dots):
            # Each dot starts at different position
            start_pos = dot_idx * (NUM_LEDS / num_dots)
            pos = (elapsed * speed + start_pos) % NUM_LEDS
            idx = int(pos)
            
            # Draw dot with glow
            for i in range(NUM_LEDS):
                dist = abs((i - pos) % NUM_LEDS)
                if dist > NUM_LEDS / 2:
                    dist = NUM_LEDS - dist
                
                if dist < 4:
                    bri = (1.0 - dist / 4) ** 1.3
                    strip.setPixelColor(i, add_c(
                        strip.getPixelColor(i),
                        dim(colors[dot_idx], bri)
                    ))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 5️⃣ PULSE WAVE LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def pulse_wave_ltr(strip, duration=DURATION):
    """Wave of expanding pulses moving left to right"""
    start = t()
    speed = NUM_LEDS / 2.5
    
    while t() - start < duration:
        elapsed = t() - start
        pulse_pos = (elapsed * speed) % NUM_LEDS
        
        set_all(strip, Color(0, 0, 5))
        
        for i in range(NUM_LEDS):
            # Distance from pulse center
            dist = abs(i - pulse_pos)
            if dist < 15:
                # Create expanding ring effect
                ring_intensity = abs(math.sin(dist - elapsed * 15)) * 0.8
                hue = (pulse_pos / NUM_LEDS + elapsed * 0.1) % 1.0
                
                strip.setPixelColor(i, add_c(
                    strip.getPixelColor(i),
                    dim(hsv(hue, 1.0, 1.0), ring_intensity)
                ))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 6️⃣ FIRE FLOW LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def fire_flow_ltr(strip, duration=DURATION):
    """Fire colors flowing continuously left to right"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        offset = elapsed * 25  # Pixels/sec
        
        for i in range(NUM_LEDS):
            # Fire color gradient
            pos_in_cycle = ((i + offset) % NUM_LEDS) / NUM_LEDS
            
            if pos_in_cycle < 0.25:
                # Black to red
                r = int(255 * (pos_in_cycle / 0.25))
                g, b = 0, 0
            elif pos_in_cycle < 0.5:
                # Red to orange
                r = 255
                g = int(255 * ((pos_in_cycle - 0.25) / 0.25))
                b = 0
            elif pos_in_cycle < 0.75:
                # Orange to yellow
                r = 255
                g = 255
                b = 0
            else:
                # Yellow to white
                r = 255
                g = 255
                b = int(255 * ((pos_in_cycle - 0.75) / 0.25))
            
            strip.setPixelColor(i, Color(r, g, b))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 7️⃣ SCANNER BEAM LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def scanner_beam(strip, duration=DURATION):
    """Thin bright beam scanning left to right repeatedly"""
    start = t()
    speed = NUM_LEDS / 1.5
    beam_width = 3
    
    while t() - start < duration:
        elapsed = t() - start
        pos = (elapsed * speed) % NUM_LEDS
        
        set_all(strip, Color(0, 0, 0))
        
        # Draw sharp beam
        for i in range(NUM_LEDS):
            dist = abs(i - pos)
            if dist < beam_width:
                brightness = (1.0 - dist / beam_width) ** 2
                strip.setPixelColor(i, dim(Color(0, 255, 100), brightness))
        
        strip.show()
        time.sleep(0.005)

# ════════════════════════════════════════════════════════════════════════════════
# 8️⃣ NEON FLOW LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def neon_flow_ltr(strip, duration=DURATION):
    """Neon-style glowing colors flowing left to right"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        offset = elapsed * 30
        
        for i in range(NUM_LEDS):
            # Smooth neon color transition
            cycle_pos = ((i + offset) % NUM_LEDS) / NUM_LEDS
            
            # Create neon glow effect
            glow = abs(math.sin(cycle_pos * math.pi * 4 + elapsed * 5))
            
            # Color transitions: cyan → magenta → yellow → cyan
            if cycle_pos < 0.33:
                hue = cycle_pos / 0.33 * 0.25  # Cyan to magenta range
            elif cycle_pos < 0.66:
                hue = 0.25 + (cycle_pos - 0.33) / 0.33 * 0.25  # Magenta to yellow
            else:
                hue = 0.5 + (cycle_pos - 0.66) / 0.34 * 0.5  # Yellow to cyan
            
            brightness = glow * 0.8 + 0.2
            strip.setPixelColor(i, hsv(hue, 0.9, brightness))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 9️⃣ SPARKLE TRAIL LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def sparkle_trail(strip, duration=DURATION):
    """Sparkling trail moving left to right"""
    start = t()
    speed = NUM_LEDS / 2
    trail_len = 20
    
    while t() - start < duration:
        elapsed = t() - start
        pos = (elapsed * speed) % (NUM_LEDS + trail_len)
        
        set_all(strip, Color(0, 0, 0))
        
        # Random sparkles in the trail
        for trail_idx in range(trail_len):
            idx = int(pos) - trail_idx
            if 0 <= idx < NUM_LEDS:
                # Random brightness for sparkle effect
                random.seed(idx + int(elapsed * 100))
                if random.random() > 0.3:
                    fade = 1.0 - (trail_idx / trail_len)
                    brightness = fade ** 1.2
                    
                    # Color varies along trail
                    hue = trail_idx / trail_len
                    strip.setPixelColor(idx, dim(hsv(hue, 0.7, 1.0), brightness))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════
# 🔟 DUAL BEAM LEFT-TO-RIGHT
# ════════════════════════════════════════════════════════════════════════════════
def dual_beam_ltr(strip, duration=DURATION):
    """Two beams moving left to right in sync"""
    start = t()
    speed = NUM_LEDS / 2
    
    while t() - start < duration:
        elapsed = t() - start
        pos1 = (elapsed * speed) % NUM_LEDS
        pos2 = (elapsed * speed + NUM_LEDS/2) % NUM_LEDS
        
        set_all(strip, Color(0, 0, 0))
        
        # Beam 1 - Cyan
        for i in range(NUM_LEDS):
            dist = abs(i - pos1)
            if dist < 6:
                bri = (1.0 - dist / 6) ** 1.5
                strip.setPixelColor(i, add_c(
                    strip.getPixelColor(i),
                    dim(Color(0, 255, 255), bri)
                ))
        
        # Beam 2 - Magenta (offset)
        for i in range(NUM_LEDS):
            dist = abs(i - pos2)
            if dist < 6:
                bri = (1.0 - dist / 6) ** 1.5
                strip.setPixelColor(i, add_c(
                    strip.getPixelColor(i),
                    dim(Color(255, 0, 255), bri)
                ))
        
        strip.show()
        time.sleep(0.008)

# ════════════════════════════════════════════════════════════════════════════════

ANIMATIONS = [
    ("▶ Simple Left-Right",      simple_ltr),
    ("🌈 Rainbow Flow L→R",      rainbow_ltr),
    ("☄️  Comet Trail L→R",      comet_trail),
    ("🏎️  Racing Dots L→R",      racing_dots),
    ("〰️  Pulse Wave L→R",       pulse_wave_ltr),
    ("🔥 Fire Flow L→R",         fire_flow_ltr),
    ("✨ Scanner Beam L→R",      scanner_beam),
    ("💫 Neon Flow L→R",         neon_flow_ltr),
    ("✦ Sparkle Trail L→R",     sparkle_trail),
    ("⚡ Dual Beam L→R",        dual_beam_ltr),
]

def fade_out(strip, steps=20):
    for step in range(steps, -1, -1):
        f = step / steps
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, dim(strip.getPixelColor(i), f))
        strip.show()
        time.sleep(0.005)

def main():
    strip = PixelStrip(NUM_LEDS, LED_PIN, LED_FREQ_HZ,
                       LED_DMA, LED_INVERT, BRIGHTNESS, LED_CHANNEL)
    strip.begin()
    print("▶▶▶ LEFT-TO-RIGHT MOVING LIGHT ANIMATIONS ▶▶▶")
    print("All lights moving continuously left → right\n")

    try:
        while True:
            for name, fn in ANIMATIONS:
                print(f"{name}")
                fn(strip, DURATION)
                fade_out(strip)
    except KeyboardInterrupt:
        fade_out(strip)
        set_all(strip, Color(0, 0, 0))
        strip.show()
        print("\n✨ LEDs off. Goodbye!")

if __name__ == "__main__":
    main()