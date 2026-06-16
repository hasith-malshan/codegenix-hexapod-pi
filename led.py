#!/usr/bin/env python3
"""
🎆 ULTRA FAST & BEAUTIFUL WS2812B LED Animations for Raspberry Pi 4
Hardware: 60-LED WS2812B strip (LE0070)

Wiring:
    DATA IN → GPIO 18 (Pin 12) via 300-470Ω resistor
    5V      → External 5V PSU (NOT Pi 5V pin)
    GND     → External PSU GND + Pi GND Pin 6 (shared ground)

Install: sudo pip3 install rpi_ws281x --break-system-packages
Run:     sudo python3 led_animations_enhanced.py
"""

import time, math, random, colorsys
from rpi_ws281x import PixelStrip, Color

# ── Config ─────────────────────────────────────────────────────────────────────
LED_PIN        = 18
NUM_LEDS       = 60
BRIGHTNESS     = 255        # Max brightness for eye-catching effects
LED_FREQ_HZ    = 800_000
LED_DMA        = 10
LED_INVERT     = False
LED_CHANNEL    = 0

# Animation durations (seconds each)
DURATION = 10

# ── Core Helpers ───────────────────────────────────────────────────────────────

def hsv(h, s=1.0, v=1.0):
    """h: 0.0-1.0 → Color"""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return Color(int(r * 255), int(g * 255), int(b * 255))

def wheel(pos):
    """Smooth colour wheel 0-255"""
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
    """Linear blend between colors"""
    r1,g1,b1 = (c1>>16)&0xFF,(c1>>8)&0xFF,c1&0xFF
    r2,g2,b2 = (c2>>16)&0xFF,(c2>>8)&0xFF,c2&0xFF
    return Color(int(r1+(r2-r1)*t), int(g1+(g2-g1)*t), int(b1+(b2-b1)*t))

def dim(c, factor):
    """Scale brightness"""
    return Color(int(((c>>16)&0xFF)*factor),
                 int(((c>>8)&0xFF)*factor),
                 int((c&0xFF)*factor))

def add_c(c1, c2):
    """Saturating add colors"""
    return Color(min(255,((c1>>16)&0xFF)+((c2>>16)&0xFF)),
                 min(255,((c1>>8)&0xFF)+((c2>>8)&0xFF)),
                 min(255,(c1&0xFF)+(c2&0xFF)))

def set_all(strip, color):
    for i in range(NUM_LEDS):
        strip.setPixelColor(i, color)

def t():
    return time.time()

# ══════════════════════════════════════════════════════════════════════════════
# 🌈 ANIMATION 1: HYPNOTIC DUAL RAINBOW WAVE
# ══════════════════════════════════════════════════════════════════════════════
def hypnotic_rainbow(strip, duration=DURATION):
    """Two rainbow waves flowing in opposite directions - MESMERIZING"""
    start = t()
    while t() - start < duration:
        elapsed = t() - start
        for i in range(NUM_LEDS):
            # Forward wave
            h1 = (i / NUM_LEDS + elapsed * 0.3) % 1.0
            # Backward wave
            h2 = ((NUM_LEDS - i) / NUM_LEDS - elapsed * 0.3) % 1.0
            # Blend them
            c1 = hsv(h1, 1.0, 1.0)
            c2 = hsv(h2, 1.0, 0.7)
            blended = blend(c1, c2, 0.5)
            strip.setPixelColor(i, blended)
        strip.show()
        time.sleep(0.008)  # ULTRA FAST

# ══════════════════════════════════════════════════════════════════════════════
# 🔥 ANIMATION 2: HYPER FIRE WITH SPARKS
# ══════════════════════════════════════════════════════════════════════════════
def hyper_fire(strip, duration=DURATION):
    """Intense, rapidly moving fire with explosive sparks"""
    start = t()
    COOLING = 25
    SPARKING = 200
    heat = [0.0] * NUM_LEDS
    
    while t() - start < duration:
        # Cool down rapidly
        for i in range(NUM_LEDS):
            heat[i] = max(0.0, heat[i] - random.uniform(0, COOLING / 255))
        
        # Aggressive diffusion
        for k in range(NUM_LEDS - 1, 1, -1):
            heat[k] = heat[k-1] * 0.6 + heat[k-2] * 0.2 + heat[k] * 0.2
        
        # Explosive sparking
        for _ in range(random.randint(2, 5)):
            if random.randint(0, 255) < SPARKING:
                y = random.randint(0, 7)
                heat[y] = min(1.0, heat[y] + random.uniform(0.7, 1.0))
        
        # Flame colors (enhanced)
        for j in range(NUM_LEDS):
            h = heat[j]
            if h < 0.25:
                r, g, b = h * 4, 0, 0
            elif h < 0.5:
                r, g, b = 1.0, (h - 0.25) * 4, 0
            elif h < 0.75:
                r, g, b = 1.0, 1.0, (h - 0.5) * 2
            else:
                r, g, b = 1.0, 1.0, (h - 0.25)
            
            strip.setPixelColor(j, Color(int(r*255), int(g*255), int(b*255)))
        
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════
# ⚡ ANIMATION 3: ELECTRIC PULSE
# ══════════════════════════════════════════════════════════════════════════════
def electric_pulse(strip, duration=DURATION):
    """Lightning-fast pulses of electric cyan racing across the strip"""
    start = t()
    pulse_speed = 2.0
    pulse_width = 8
    
    while t() - start < duration:
        elapsed = t() - start
        pos = (elapsed * pulse_speed * NUM_LEDS) % (NUM_LEDS + pulse_width)
        
        set_all(strip, Color(0, 5, 10))  # Dark cyan background
        
        # Draw 3 pulses
        for pulse_num in range(3):
            pulse_pos = (pos - pulse_num * 20) % NUM_LEDS
            
            for i in range(NUM_LEDS):
                dist = abs((i - pulse_pos) % NUM_LEDS)
                if dist > NUM_LEDS / 2:
                    dist = NUM_LEDS - dist
                
                if dist < pulse_width:
                    intensity = (1.0 - dist / pulse_width) ** 1.5
                    brightness = int(255 * intensity)
                    strip.setPixelColor(i, add_c(
                        strip.getPixelColor(i),
                        Color(0, brightness, brightness // 2)
                    ))
        
        strip.show()
        time.sleep(0.006)

# ══════════════════════════════════════════════════════════════════════════════
# 💎 ANIMATION 4: CRYSTALLINE SHIMMER
# ══════════════════════════════════════════════════════════════════════════════
def crystalline_shimmer(strip, duration=DURATION):
    """Rapid, synchronized twinkling with geometric patterns"""
    start = t()
    state = [[random.random(), random.random(), random.uniform(0.02, 0.08)]
             for _ in range(NUM_LEDS)]
    
    while t() - start < duration:
        elapsed = t() - start
        
        for i in range(NUM_LEDS):
            bri, hue, spd = state[i]
            
            # Oscillate brightness rapidly
            bri = abs(math.sin(elapsed * 3 + i * 0.2))
            
            # Slowly shift hue
            hue = (hue + spd * 0.0001) % 1.0
            
            state[i][1] = hue
            
            # Create a base color that varies by position
            base_hue = (i / NUM_LEDS + elapsed * 0.1) % 1.0
            
            strip.setPixelColor(i, hsv(base_hue, 0.5, bri ** 0.5))
        
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════
# 🌊 ANIMATION 5: LIQUID NEON
# ══════════════════════════════════════════════════════════════════════════════
def liquid_neon(strip, duration=DURATION):
    """Smooth, flowing interference pattern with vibrant colors"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        
        for i in range(NUM_LEDS):
            # Multi-layered sine waves for fluid motion
            v = (math.sin(i * 0.4 + elapsed * 3.0) * 0.5 +
                 math.sin(i * 0.15 + elapsed * 1.5) * 0.3 +
                 math.sin((i - elapsed * 30) * 0.08) * 0.2)
            
            hue = (v + 1) / 2
            brightness = abs(math.sin(elapsed * 2 + i * 0.1)) * 0.7 + 0.3
            
            strip.setPixelColor(i, hsv(hue, 0.9, brightness))
        
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════
# 🎯 ANIMATION 6: CONVERGING BEAMS
# ══════════════════════════════════════════════════════════════════════════════
def converging_beams(strip, duration=DURATION):
    """Brilliant beams converging from both ends to the center"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        
        set_all(strip, Color(0, 0, 0))
        
        # Right-to-left beam
        pos_r = (elapsed * 25) % (NUM_LEDS + 10)
        for i in range(NUM_LEDS):
            dist = abs(i - pos_r)
            if dist < 8:
                bri = (1.0 - dist / 8) ** 1.2
                strip.setPixelColor(i, add_c(
                    strip.getPixelColor(i),
                    Color(int(255 * bri), int(150 * bri), 0)
                ))
        
        # Left-to-right beam
        pos_l = NUM_LEDS - (elapsed * 25) % (NUM_LEDS + 10)
        for i in range(NUM_LEDS):
            dist = abs(i - pos_l)
            if dist < 8:
                bri = (1.0 - dist / 8) ** 1.2
                strip.setPixelColor(i, add_c(
                    strip.getPixelColor(i),
                    Color(0, int(200 * bri), int(255 * bri))
                ))
        
        strip.show()
        time.sleep(0.006)

# ══════════════════════════════════════════════════════════════════════════════
# 🌀 ANIMATION 7: SPIRAL VORTEX
# ══════════════════════════════════════════════════════════════════════════════
def spiral_vortex(strip, duration=DURATION):
    """Color spiral that expands and contracts - hypnotic"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        
        set_all(strip, Color(0, 0, 0))
        
        for i in range(NUM_LEDS):
            # Create spiral effect
            angle = (i / NUM_LEDS) * math.pi * 4 + elapsed * 2
            radius_factor = (math.sin(elapsed * 2) + 1) / 2
            
            # Map to LED position
            hue = (angle / (2 * math.pi) + elapsed * 0.2) % 1.0
            brightness = abs(math.sin(angle + elapsed * 3)) * 0.8 + 0.2
            
            strip.setPixelColor(i, hsv(hue, 1.0, brightness * radius_factor))
        
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════
# 💥 ANIMATION 8: EXPLOSION BURST
# ══════════════════════════════════════════════════════════════════════════════
def explosion_burst(strip, duration=DURATION):
    """Explosive bursts of light radiating outward"""
    start = t()
    bursts = []
    last_burst = 0
    
    while t() - start < duration:
        elapsed = t() - start
        
        # Spawn bursts
        if elapsed - last_burst > 0.4:
            center = random.randint(10, NUM_LEDS - 10)
            hue = random.random()
            bursts.append([center, elapsed, hue])
            last_burst = elapsed
        
        set_all(strip, Color(2, 2, 5))
        
        # Draw bursts
        live = []
        for center, birth, hue in bursts:
            age = elapsed - birth
            if age < 0.8:
                live.append([center, birth, hue])
                radius = age * 80
                fade = 1.0 - age / 0.8
                
                for i in range(NUM_LEDS):
                    dist = abs(i - center)
                    if dist < radius + 5:
                        intensity = max(0.0, 1.0 - (dist - radius) / 8) * fade
                        if intensity > 0:
                            strip.setPixelColor(i, add_c(
                                strip.getPixelColor(i),
                                dim(hsv(hue, 1.0, 1.0), intensity)
                            ))
        
        bursts = live
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════
# 🎪 ANIMATION 9: STROBE RAINBOW
# ══════════════════════════════════════════════════════════════════════════════
def strobe_rainbow(strip, duration=DURATION):
    """Fast strobing rainbow blocks - INTENSE"""
    start = t()
    colors = [hsv(i / 6, 1.0, 1.0) for i in range(6)]
    seg_size = NUM_LEDS // 6
    
    while t() - start < duration:
        elapsed = t() - start
        offset = int(elapsed * 12) % 6
        
        for i in range(NUM_LEDS):
            color_idx = (i // seg_size + offset) % 6
            
            # Strobe effect
            if int(elapsed * 20) % 2 == 0:
                strip.setPixelColor(i, colors[color_idx])
            else:
                strip.setPixelColor(i, dim(colors[color_idx], 0.3))
        
        strip.show()
        time.sleep(0.010)

# ══════════════════════════════════════════════════════════════════════════════
# 🔮 ANIMATION 10: BINARY CASCADES
# ══════════════════════════════════════════════════════════════════════════════
def binary_cascades(strip, duration=DURATION):
    """Digital cascades of light falling downward"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        
        for i in range(NUM_LEDS):
            # Digital cascade effect
            pos = (elapsed * 40 + i) % NUM_LEDS
            
            # Primary color based on position
            hue = (i / NUM_LEDS + elapsed * 0.15) % 1.0
            
            # Intensity based on distance from cascade
            dist = abs((i - pos + NUM_LEDS) % NUM_LEDS - NUM_LEDS / 2)
            if dist < NUM_LEDS / 2:
                intensity = max(0, 1.0 - dist / (NUM_LEDS / 3))
            else:
                intensity = 0
            
            strip.setPixelColor(i, hsv(hue, 1.0, intensity ** 0.8))
        
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════
# ⭐ ANIMATION 11: AURORA BOREALIS
# ══════════════════════════════════════════════════════════════════════════════
def aurora_borealis(strip, duration=DURATION):
    """Smooth, magical aurora-like color waves"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        
        for i in range(NUM_LEDS):
            # Multi-layered perlin-like effect
            v1 = math.sin(i * 0.2 + elapsed * 0.8)
            v2 = math.sin(i * 0.3 + elapsed * 1.2 + 2)
            v3 = math.sin((i - elapsed * 20) * 0.1)
            
            hue = ((v1 + v2 + v3) / 3 + 1) / 2
            brightness = abs(math.sin(elapsed * 1.5 + i * 0.15)) * 0.6 + 0.4
            
            strip.setPixelColor(i, hsv(hue % 1.0, 0.7, brightness))
        
        strip.show()
        time.sleep(0.010)

# ══════════════════════════════════════════════════════════════════════════════
# 💫 ANIMATION 12: QUANTUM TUNNEL
# ══════════════════════════════════════════════════════════════════════════════
def quantum_tunnel(strip, duration=DURATION):
    """Hypnotic tunnel of expanding rings"""
    start = t()
    
    while t() - start < duration:
        elapsed = t() - start
        
        set_all(strip, Color(0, 0, 0))
        
        for ring_n in range(4):
            ring_pos = (elapsed * 50 + ring_n * 15) % NUM_LEDS
            width = 6
            
            for i in range(NUM_LEDS):
                dist = abs((i - ring_pos + NUM_LEDS) % NUM_LEDS - NUM_LEDS / 2)
                if dist < NUM_LEDS / 2:
                    rel_dist = abs(dist - width)
                    if rel_dist < width:
                        hue = (ring_n / 4 + elapsed * 0.2) % 1.0
                        bri = (1.0 - rel_dist / width) ** 1.3
                        strip.setPixelColor(i, add_c(
                            strip.getPixelColor(i),
                            dim(hsv(hue, 1.0, 1.0), bri)
                        ))
        
        strip.show()
        time.sleep(0.008)

# ══════════════════════════════════════════════════════════════════════════════

ANIMATIONS = [
    ("🌈 Hypnotic Rainbow",    hypnotic_rainbow),
    ("🔥 Hyper Fire",          hyper_fire),
    ("⚡ Electric Pulse",      electric_pulse),
    ("💎 Crystalline Shimmer", crystalline_shimmer),
    ("🌊 Liquid Neon",         liquid_neon),
    ("🎯 Converging Beams",    converging_beams),
    ("🌀 Spiral Vortex",       spiral_vortex),
    ("💥 Explosion Burst",     explosion_burst),
    ("🎪 Strobe Rainbow",      strobe_rainbow),
    ("🔮 Binary Cascades",     binary_cascades),
    ("⭐ Aurora Borealis",     aurora_borealis),
    ("💫 Quantum Tunnel",      quantum_tunnel),
]

def fade_out(strip, steps=20):
    """Ultra-fast fade"""
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
    print("✨✨✨ ULTRA FAST & BEAUTIFUL LED ANIMATIONS ✨✨✨")
    print("All 60 LEDs included | Fast animation speeds\n")

    try:
        while True:
            for name, fn in ANIMATIONS:
                print(f"▶ {name}")
                fn(strip, DURATION)
                fade_out(strip)
    except KeyboardInterrupt:
        fade_out(strip)
        set_all(strip, Color(0, 0, 0))
        strip.show()
        print("\n✨ LEDs off. Goodbye!")

if __name__ == "__main__":
    main()