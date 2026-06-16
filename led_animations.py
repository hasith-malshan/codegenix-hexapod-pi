import colorsys
import math
import random
import time

import board
import neopixel


LED_PIN = board.D18
NUM_LEDS = 60
BRIGHTNESS = 100 / 255
COLOR_ORDER = neopixel.GRB

pixels = neopixel.NeoPixel(
    LED_PIN,
    NUM_LEDS,
    brightness=BRIGHTNESS,
    auto_write=False,
    pixel_order=COLOR_ORDER,
)


def millis():
    return int(time.monotonic() * 1000)


def delay(ms):
    time.sleep(ms / 1000)


def hsv(hue, saturation=255, value=255):
    red, green, blue = colorsys.hsv_to_rgb(
        (hue % 256) / 256,
        saturation / 255,
        value / 255,
    )
    return int(red * 255), int(green * 255), int(blue * 255)


def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255
    for index in range(NUM_LEDS):
        red, green, blue = pixels[index]
        pixels[index] = int(red * scale), int(green * scale), int(blue * scale)


def add_color(index, color):
    old_red, old_green, old_blue = pixels[index]
    red, green, blue = color
    pixels[index] = (
        min(255, old_red + red),
        min(255, old_green + green),
        min(255, old_blue + blue),
    )


def blend(color_a, color_b, amount):
    return tuple(
        int(color_a[channel] + (color_b[channel] - color_a[channel]) * amount)
        for channel in range(3)
    )


def beatsin(bpm, low, high):
    seconds = time.monotonic()
    beat = math.sin(seconds * bpm * 2 * math.pi / 60)
    position = (beat + 1) / 2
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
    temp = int(temperature)
    heat_ramp = (temp & 0x3F) << 2

    if temp > 0x80:
        return 255, 255, heat_ramp
    if temp > 0x40:
        return 255, heat_ramp, 0
    return heat_ramp, 0, 0


def rainbow():
    for start_hue in range(256):
        for index in range(NUM_LEDS):
            pixels[index] = hsv(start_hue + index * 7)
        pixels.show()
        delay(20)


def confetti():
    for _ in range(300):
        fade_to_black_by(10)
        pos = random.randrange(NUM_LEDS)
        add_color(pos, hsv(random.randrange(256), 255, 255))
        pixels.show()
        delay(20)


def sinelon():
    for _ in range(400):
        fade_to_black_by(20)
        pos = beatsin(13, 0, NUM_LEDS - 1)
        add_color(pos, hsv(millis() // 10, 255, 255))
        pixels.show()
        delay(10)


def bpm():
    beats_per_minute = 62

    for _ in range(400):
        beat = beatsin(beats_per_minute, 64, 255)

        for index in range(NUM_LEDS):
            pixels[index] = color_from_palette(
                PARTY_PALETTE,
                (index * 2) + millis() // 10,
                max(0, beat - (index * 10)),
            )

        pixels.show()
        delay(20)


def juggle():
    for _ in range(400):
        fade_to_black_by(20)

        dot_hue = 0
        for index in range(8):
            pos = beatsin(index + 7, 0, NUM_LEDS - 1)
            add_color(pos, hsv(dot_hue, 200, 255))
            dot_hue += 32

        pixels.show()
        delay(20)


def fire_effect():
    heat = [0] * NUM_LEDS

    for _ in range(500):
        for index in range(NUM_LEDS):
            heat[index] = max(0, heat[index] - random.randrange(20))

        for index in range(NUM_LEDS - 1, 1, -1):
            heat[index] = (heat[index - 1] + heat[index - 2] + heat[index - 2]) // 3

        if random.randrange(256) < 120:
            index = random.randrange(min(7, NUM_LEDS))
            heat[index] = min(255, heat[index] + random.randrange(160, 256))

        for index in range(NUM_LEDS):
            pixels[index] = heat_color(heat[index])

        pixels.show()
        delay(20)


def clear_strip():
    pixels.fill((0, 0, 0))
    pixels.show()


def main():
    try:
        while True:
            rainbow()
            confetti()
            sinelon()
            bpm()
            juggle()
            fire_effect()
    except KeyboardInterrupt:
        clear_strip()


if __name__ == "__main__":
    main()
