from rpi_ws281x import PixelStrip, Color
import time

# LED configuration
LED_COUNT = 60
LED_PIN = 18
LED_FREQ_HZ = 800000
LED_DMA = 10
LED_BRIGHTNESS = 100
LED_INVERT = False
LED_CHANNEL = 0

strip = PixelStrip(
    LED_COUNT,
    LED_PIN,
    LED_FREQ_HZ,
    LED_DMA,
    LED_INVERT,
    LED_BRIGHTNESS,
    LED_CHANNEL
)

strip.begin()

def color_wipe(color, wait_ms=20):
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, color)
        strip.show()
        time.sleep(wait_ms / 1000.0)

def rainbow(wait_ms=20):
    for j in range(256):
        for i in range(strip.numPixels()):
            pos = (i + j) & 255

            if pos < 85:
                color = Color(pos * 3, 255 - pos * 3, 0)
            elif pos < 170:
                pos -= 85
                color = Color(255 - pos * 3, 0, pos * 3)
            else:
                pos -= 170
                color = Color(0, pos * 3, 255 - pos * 3)

            strip.setPixelColor(i, color)

        strip.show()
        time.sleep(wait_ms / 1000.0)

try:
    while True:
        color_wipe(Color(255, 0, 0))   # Red
        time.sleep(1)

        color_wipe(Color(0, 255, 0))   # Green
        time.sleep(1)

        color_wipe(Color(0, 0, 255))   # Blue
        time.sleep(1)

        rainbow()

except KeyboardInterrupt:
    for i in range(strip.numPixels()):
        strip.setPixelColor(i, Color(0, 0, 0))
    strip.show()