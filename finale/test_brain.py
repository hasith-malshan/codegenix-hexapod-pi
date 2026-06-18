import sys
import collections

# Ensure sudo can find your packages
sys.path.append("/home/codegenix/.local/lib/python3.13/site-packages")

import importlib.util
import os

# --- REVISED FIX: DIRECT PIPEWIRE/PULSE AUDIO COOKIE BRIDGE ---
os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
cookie_paths = [
    "/home/codegenix/.config/pulse/cookie",
    "/home/codegenix/.pulse-cookie",
    "/home/codegenix/.config/pulse-cookie"
]
for path in cookie_paths:
    if os.path.exists(path):
        os.environ["PULSE_COOKIE"] = path
        break
os.environ.pop("XDG_RUNTIME_DIR", None)
os.environ["TFHUB_CACHE_DIR"] = "./ai_model_cache"

# --- The "Smart" Python 3.13 Hack ---
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None
sys.modules['imp'] = FakeImp()
# ------------------------------------

# Standard Python Libraries
import serial
import soundcard as sc
import numpy as np
import aubio
import tensorflow as tf
import tensorflow_hub as hub
import threading
import time
import csv
import colorsys
import speech_recognition as sr
import pyttsx3
import math
import random
from scipy.signal import butter, lfilter

# Display & Graphics Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341

# LED Libraries
from rpi_ws281x import PixelStrip, Color, ws

# ==========================================
# 1. AUDIO CONFIGURATION
# ==========================================
RATE = 16000
CHUNK = 256

def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    b, a = butter(order, [lowcut / nyq, highcut / nyq], btype='band')
    return b, a

def butter_bandpass_filter(data, lowcut=300, highcut=3000, fs=RATE, order=4):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    return np.ascontiguousarray(y, dtype=np.float32)

# ==========================================
# 2. HARDWARE WIRING
# ==========================================
DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25

LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
LED_BRIGHTNESS = 100

# ==========================================
# 3. GLOBAL STATE
# ==========================================
class RobotState:
    def __init__(self):
        self.operating_mode = "AUTO"
        self.audio_source = "MIC"
        self.show_audio_logs = False

        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"
        self.voice_active = False
        self.command_detected_time = 0.0
        self.body_roll = 0.0

        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0
        self.bpm_history = collections.deque(maxlen=20)
        self.lock = threading.Lock()

state = RobotState()

# ==========================================
# 4. SETUP USB SERIAL (ESP32)
# ==========================================
def connect_to_esp32():
    print("\n🔌 Searching for ESP32 via USB...")
    for port in ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/serial0']:
        try:
            s = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Successfully connected to ESP32 on {port}")
            return s
        except Exception: continue
    print("❌ Failed to find ESP32 on USB. Manual mode will simulate commands.")
    return None

esp32_serial = connect_to_esp32()
_esp32_ready = threading.Event()
_esp32_ready.set()
_send_lock = threading.Lock()

def esp32_reader_thread():
    while True:
        if esp32_serial and esp32_serial.is_open:
            try:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith("TILT:"):
                    try:
                        with state.lock: state.body_roll = float(line.split(":")[1])
                    except ValueError: pass
                elif line == "READY":
                    _esp32_ready.set()
            except Exception: time.sleep(0.1)
        else: time.sleep(0.1)

def send_to_esp32(command):
    if not (esp32_serial and esp32_serial.is_open):
        print(f" [Simulated] -> {command}")
        return
    with _send_lock:
        if not _esp32_ready.wait(timeout=2.0): pass
        _esp32_ready.clear()
        try: esp32_serial.write((command + "\n").encode('utf-8'))
        except Exception: _esp32_ready.set()

# ==========================================
# 5. LED STRIP MATH & ANIMATION THREAD
# ==========================================
strip = PixelStrip(NUM_LEDS, LED_PIN, 800000, 10, False, LED_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB)
strip.begin()

def hsv(hue, sat=255, val=255):
    r, g, b = colorsys.hsv_to_rgb((hue % 256) / 256.0, sat / 255.0, val / 255.0)
    return Color(int(r * 255), int(g * 255), int(b * 255))

def beatsin(bpm, low, high, phase=0):
    angle = time.monotonic() * bpm * 2 * math.pi / 60 + phase
    position = (math.sin(angle) + 1) / 2
    return int(low + position * (high - low))

def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255.0
    for i in range(NUM_LEDS):
        c = strip.getPixelColor(i)
        r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
        strip.setPixelColor(i, Color(int(r * scale), int(g * scale), int(b * scale)))

def led_thread():
    frame = 0
    heat = [0] * NUM_LEDS
    while True:
        with state.lock:
            speed, va, cmd_t = state.music_speed, state.voice_active, state.command_detected_time
        dt = time.time() - cmd_t
        frame += 1

        if dt < 0.25:
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(255, 255, 255))
            strip.show(); time.sleep(0.02); continue
        elif dt < 1.0:
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, 50, 255))
            strip.show(); time.sleep(0.02); continue

        if va:
            strip.setPixelColor(0, Color(0, 0, 0))
            fade_to_black_by(60)
            pos = frame % (NUM_LEDS * 2 - 2)
            if pos >= NUM_LEDS: pos = NUM_LEDS * 2 - 2 - pos
            strip.setPixelColor(pos, Color(0, 255, 50))
            strip.show(); time.sleep(0.05); continue

        if speed == "FAST":
            for i in range(NUM_LEDS): heat[i] = max(0, heat[i] - random.randrange(10, 35))
            for i in range(NUM_LEDS - 1, 1, -1): heat[i] = (heat[i - 1] + heat[i - 2] * 2) // 3
            if random.randrange(256) < 130:
                s = random.randrange(min(2, NUM_LEDS))
                heat[s] = min(255, heat[s] + random.randrange(160, 256))
            for i in range(NUM_LEDS):
                t = heat[i]
                ramp = (t & 0x3F) << 2
                if t > 0x80: c = Color(255, 255, ramp)
                elif t > 0x40: c = Color(255, ramp, 0)
                else: c = Color(ramp, 0, 0)
                strip.setPixelColor(i, c)
            strip.show(); time.sleep(0.03)

        elif speed == "MEDIUM":
            fade_to_black_by(35)
            pos = beatsin(30, 0, NUM_LEDS - 1)
            strip.setPixelColor(pos, hsv(int(time.monotonic() * 50) % 256))
            strip.show(); time.sleep(0.02)

        elif speed == "SLOW":
            for i in range(NUM_LEDS):
                lvl = (math.sin(frame * 0.10 - i * 0.5) + 1) / 2
                strip.setPixelColor(i, hsv(frame + i * 10, 230, int(25 + lvl * 230)))
            strip.show(); time.sleep(0.04)

        else:
            lvl = (math.sin(frame * 0.05) + 1) / 2
            c_val = int(10 + lvl * 80)
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, c_val, c_val))
            strip.show(); time.sleep(0.03)

# ==========================================
# 6. LCD DISPLAY ENGINE
# ==========================================
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(spi, cs=digitalio.DigitalInOut(DISPLAY_CS_PIN), dc=digitalio.DigitalInOut(DISPLAY_DC_PIN), rst=digitalio.DigitalInOut(DISPLAY_RST_PIN), rotation=90, baudrate=24000000)

def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    r = min(corner_radius, (x1 - x0) // 2, (y1 - y0) // 2)
    if r <= 0: draw.rectangle([x0, y0, x1, y1], fill=fill); return
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.pieslice([x0, y0, x0 + r * 2, y0 + r * 2], 180, 270, fill=fill)
    draw.pieslice([x1 - r * 2, y1 - r * 2, x1, y1], 0, 90, fill=fill)
    draw.pieslice([x0, y1 - r * 2, x0 + r * 2, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - r * 2, y0, x1, y0 + r * 2], 270, 360, fill=fill