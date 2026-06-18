import argparse
import colorsys
import math
import random
import sys
import time


LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
BRIGHTNESS = 100
DEFAULT_TRANSITION_SECONDS = 2.0

pixels = None


class NeoPixelStrip:
    def __init__(self):
        from rpi_ws281x import Color, PixelStrip, ws

        self._color = Color
        self._strip = PixelStrip(
            NUM_LEDS,
            LED_PIN,
            freq_hz=800000,
            dma=10,
            invert=False,
            brightness=BRIGHTNESS,
            channel=LED_CHANNEL,
            strip_type=ws.WS2811_STRIP_GRB,
        )
        self._strip.begin()

    def __getitem__(self, index):
        color = self._strip.getPixelColor(index)
        return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF

    def __setitem__(self, index, color):
        red, green, blue = color
        self._strip.setPixelColor(index, self._color(red, green, blue))

    def fill(self, color):
        for index in range(NUM_LEDS):
            self[index] = color

    def show(self):
        self._strip.show()


def initialize_strip():
    global pixels
    if pixels is None:
        print("Configuring GPIO13 on PWM channel 1...", flush=True)
        pixels = NeoPixelStrip()
        print("LED strip ready.", flush=True)


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def delay(milliseconds):
    time.sleep(milliseconds / 1000)


def millis():
    return int(time.monotonic() * 1000)


def hsv(hue, saturation=255, value=255):
    red, green, blue = colorsys.hsv_to_rgb(
        (hue % 256) / 256,
        saturation / 255,
        value / 255,
    )
    return int(red * 255), int(green * 255), int(blue * 255)


def scale_color(color, scale):
    return tuple(int(channel * scale) for channel in color)


def blend(color_a, color_b, amount):
    return tuple(
        int(color_a[channel] + (color_b[channel] - color_a[channel]) * amount)
        for channel in range(3)
    )


def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255
    for index in range(NUM_LEDS):
        pixels[index] = scale_color(pixels[index], scale)


def add_color(index, color):
    current = pixels[index]
    pixels[index] = tuple(min(255, current[channel] + color[channel]) for channel in range(3))


def beatsin(bpm, low, high, phase=0):
    angle = time.monotonic() * bpm * 2 * math.pi / 60 + phase
    position = (math.sin(angle) + 1) / 2
    return int(low + position * (high - low))


PARTY_PALETTE = [
    (85, 0, 171),
    (132, 0, 124),
    (181, 0, 75),
    (229, 0, 27),
    (232, 23, 0),
    (184, 71, 0),
    (171, 119, 0),
    (171, 171, 0),
    (171, 85, 0),
    (221, 34, 0),
    (242, 0, 13),
    (194, 0, 62),
    (143, 0, 112),
    (95, 0, 160),
    (47, 0, 208),
    (0, 7, 249),
]


def color_from_palette(palette, index, brightness=255):
    scaled = (index % 256) / 16
    base = int(scaled)
    fraction = scaled - base
    color = blend(palette[base % len(palette)], palette[(base + 1) % len(palette)], fraction)
    return tuple(int(channel * brightness / 255) for channel in color)


def heat_color(temperature):
    heat_ramp = (temperature & 0x3F) << 2
    if temperature > 0x80:
        return 255, 255, heat_ramp
    if temperature > 0x40:
        return 255, heat_ramp, 0
    return heat_ramp, 0, 0


def clear_strip():
    if pixels is not None:
        pixels.fill((0, 0, 0))
        pixels.show()


def transition_off(seconds):
    clear_strip()
    if seconds > 0:
        log(f"Transition: LEDs off for {seconds:g} seconds")
        time.sleep(seconds)


def rainbow():
    for start_hue in range(160):
        for index in range(NUM_LEDS):
            pixels[index] = hsv(start_hue + index * 18)
        pixels.show()
        delay(25)


def confetti():
    for _ in range(180):
        fade_to_black_by(25)
        add_color(random.randrange(NUM_LEDS), hsv(random.randrange(256)))
        pixels.show()
        delay(25)


def sinelon():
    for _ in range(180):
        fade_to_black_by(35)
        position = beatsin(18, 0, NUM_LEDS - 1)
        add_color(position, hsv(millis() // 8))
        pixels.show()
        delay(25)


def bpm():
    beats_per_minute = 90
    for _ in range(180):
        beat = beatsin(beats_per_minute, 80, 255)
        for index in range(NUM_LEDS):
            pixels[index] = color_from_palette(
                PARTY_PALETTE,
                index * 24 + millis() // 8,
                max(20, beat - index * 12),
            )
        pixels.show()
        delay(25)


def juggle():
    for _ in range(180):
        fade_to_black_by(40)
        for dot in range(4):
            position = beatsin(dot + 8, 0, NUM_LEDS - 1, dot * 0.6)
            add_color(position, hsv(dot * 64, 220, 255))
        pixels.show()
        delay(25)


def fire():
    heat = [0] * NUM_LEDS
    for _ in range(200):
        for index in range(NUM_LEDS):
            heat[index] = max(0, heat[index] - random.randrange(10, 35))
        for index in range(NUM_LEDS - 1, 1, -1):
            heat[index] = (heat[index - 1] + heat[index - 2] * 2) // 3
        if random.randrange(256) < 130:
            spark = random.randrange(min(2, NUM_LEDS))
            heat[spark] = min(255, heat[spark] + random.randrange(160, 256))
        for index in range(NUM_LEDS):
            pixels[index] = heat_color(heat[index])
        pixels.show()
        delay(25)


def color_wipe():
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 100, 0)]
    for color in colors:
        for index in range(NUM_LEDS):
            pixels[index] = color
            pixels.show()
            delay(100)
        delay(250)


def theater_chase():
    for frame in range(60):
        hue = frame * 5
        for index in range(NUM_LEDS):
            pixels[index] = hsv(hue + index * 20) if (index + frame) % 3 == 0 else (0, 0, 0)
        pixels.show()
        delay(70)


def comet():
    trail = 4
    for frame in range((NUM_LEDS + trail) * 8):
        pixels.fill((0, 0, 0))
        position = frame % (NUM_LEDS * 2 - 2)
        if position >= NUM_LEDS:
            position = NUM_LEDS * 2 - 2 - position
        for offset in range(trail):
            led = position - offset
            if 0 <= led < NUM_LEDS:
                pixels[led] = scale_color(hsv(frame * 5), (trail - offset) / trail)
        pixels.show()
        delay(55)


def dual_scanner():
    for frame in range(90):
        fade_to_black_by(65)
        position = frame % (NUM_LEDS * 2 - 2)
        if position >= NUM_LEDS:
            position = NUM_LEDS * 2 - 2 - position
        pixels[position] = (255, 20, 0)
        pixels[NUM_LEDS - 1 - position] = (0, 60, 255)
        pixels.show()
        delay(45)


def breathing():
    color = (30, 120, 255)
    for frame in range(180):
        level = (math.sin(frame * 2 * math.pi / 60 - math.pi / 2) + 1) / 2
        pixels.fill(scale_color(color, 0.03 + level * 0.97))
        pixels.show()
        delay(25)


def sparkle_burst():
    for burst in range(24):
        pixels.fill((0, 0, 0))
        hue = random.randrange(256)
        count = random.randrange(2, NUM_LEDS + 1)
        for index in random.sample(range(NUM_LEDS), count):
            pixels[index] = hsv(hue + random.randrange(-20, 21), 180, 255)
        pixels.show()
        delay(80)
        for _ in range(4):
            fade_to_black_by(70)
            pixels.show()
            delay(35)


def strobe():
    for flash in range(28):
        pixels.fill(hsv(flash * 11, 100, 255))
        pixels.show()
        delay(45)
        pixels.fill((0, 0, 0))
        pixels.show()
        delay(85)


def wave():
    for frame in range(160):
        for index in range(NUM_LEDS):
            level = (math.sin(frame * 0.18 - index * 0.9) + 1) / 2
            pixels[index] = hsv(frame * 2 + index * 16, 230, int(25 + level * 230))
        pixels.show()
        delay(28)


def alternating():
    colors = [(255, 0, 80), (0, 180, 255)]
    for frame in range(55):
        for index in range(NUM_LEDS):
            pixels[index] = colors[(index + frame) % 2]
        pixels.show()
        delay(80)


def random_palette():
    palette = [hsv(random.randrange(256), 220, 255) for _ in range(4)]
    for frame in range(150):
        for index in range(NUM_LEDS):
            pixels[index] = color_from_palette(palette, frame * 3 + index * 32)
        pixels.show()
        delay(28)


EFFECTS = {
    "rainbow": ("Rainbow", "Moving color gradient", rainbow),
    "confetti": ("Confetti", "Random sparkles and trails", confetti),
    "sinelon": ("Sinelon", "Single sweeping scanner", sinelon),
    "bpm": ("BPM", "Tempo-style palette pulse", bpm),
    "juggle": ("Juggle", "Four weaving colored dots", juggle),
    "fire": ("Fire", "Rising flame simulation", fire),
    "color-wipe": ("Color Wipe", "Sequential color fill", color_wipe),
    "theater-chase": ("Theater Chase", "Moving dotted pattern", theater_chase),
    "comet": ("Comet", "Moving light with a tail", comet),
    "dual-scanner": ("Dual Scanner", "Opposing red and blue scanners", dual_scanner),
    "breathing": ("Breathing", "Smooth whole-strip pulse", breathing),
    "sparkle-burst": ("Sparkle Burst", "Short groups of bright sparkles", sparkle_burst),
    "strobe": ("Strobe", "Fast full-strip flashes", strobe),
    "wave": ("Wave", "Traveling brightness wave", wave),
    "alternating": ("Alternating", "Switching odd and even LEDs", alternating),
    "random-palette": ("Random Palette", "Generated coordinated colors", random_palette),
}


def print_effects():
    print("\nAvailable effects:")
    for number, (key, effect) in enumerate(EFFECTS.items(), start=1):
        name, description, _ = effect
        print(f"  {number:2}. {name:<16} {key:<16} {description}")
    print()


def resolve_effect(selection):
    normalized = selection.strip().lower().replace("_", "-")
    if normalized.isdigit():
        number = int(normalized)
        keys = list(EFFECTS)
        if 1 <= number <= len(keys):
            return keys[number - 1]
    if normalized in EFFECTS:
        return normalized
    for key, (name, _, _) in EFFECTS.items():
        if normalized == name.lower().replace(" ", "-"):
            return key
    return None


def run_effect(key, transition_seconds):
    name, description, effect = EFFECTS[key]
    log(f"Starting {name}: {description}")
    effect()
    log(f"Finished {name}")
    transition_off(transition_seconds)


def run_all(transition_seconds, repeat=False):
    while True:
        for key in EFFECTS:
            run_effect(key, transition_seconds)
        if not repeat:
            return


def interactive_menu(transition_seconds):
    print_effects()
    print("Commands: number/name = run effect, all = run all, list = show list, off = clear, q = quit")
    while True:
        selection = input("\nChoose effect: ").strip()
        command = selection.lower()
        if command in {"q", "quit", "exit"}:
            return
        if command in {"list", "ls", "help", "?"}:
            print_effects()
            continue
        if command == "all":
            run_all(transition_seconds)
            continue
        if command == "off":
            clear_strip()
            log("LED strip cleared")
            continue
        key = resolve_effect(selection)
        if key is None:
            print("Unknown effect. Enter 'list' to see valid choices.")
            continue
        run_effect(key, transition_seconds)


def parse_args():
    parser = argparse.ArgumentParser(description="WS2812B effect demonstrator for seven LEDs")
    parser.add_argument("--list", action="store_true", help="list effects without accessing GPIO")
    parser.add_argument("--effect", help="run an effect by number or name")
    parser.add_argument("--all", action="store_true", help="run every effect")
    parser.add_argument("--loop", action="store_true", help="repeat --effect or --all continuously")
    parser.add_argument(
        "--transition",
        type=float,
        default=DEFAULT_TRANSITION_SECONDS,
        help="seconds to keep LEDs off after an effect (default: 2)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.transition < 0:
        print("--transition cannot be negative", file=sys.stderr)
        return 2
    if args.list:
        print_effects()
        return 0

    selected = None
    if args.effect:
        selected = resolve_effect(args.effect)
        if selected is None:
            print(f"Unknown effect: {args.effect}", file=sys.stderr)
            print_effects()
            return 2

    initialize_strip()
    log(
        f"GPIO{LED_PIN}, channel={LED_CHANNEL}, LEDs={NUM_LEDS}, "
        f"brightness={BRIGHTNESS}, transition={args.transition:g}s"
    )

    try:
        if selected:
            while True:
                run_effect(selected, args.transition)
                if not args.loop:
                    break
        elif args.all:
            run_all(args.transition, args.loop)
        else:
            interactive_menu(args.transition)
    except KeyboardInterrupt:
        print()
        log("Stopped by user")
    finally:
        clear_strip()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
