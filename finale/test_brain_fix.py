import sys
import importlib.util
import os
import random
import collections
import threading
import time
import csv
import math
import colorsys


# --- The "Smart" Python 3.13 Hack ---
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None


sys.modules['imp'] = FakeImp()
# ------------------------------------

import serial
import soundcard as sc
import numpy as np
import aubio
import tensorflow as tf
import tensorflow_hub as hub
import speech_recognition as sr
import pyttsx3
from scipy.signal import butter, lfilter

# Display Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341

# LED Libraries
from rpi_ws281x import PixelStrip, Color, ws

# ==========================================
# 1. HARDWARE CONFIGURATION
# ==========================================
# Serial (ESP32)
SERIAL_PORT = '/dev/serial0'
BAUD_RATE = 115200

# Display (SPI)
DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25

# LEDs (WS2812B)
LED_PIN = 13  # GPIO13
LED_CHANNEL = 1  # PWM Channel 1 for GPIO13
NUM_LEDS = 7  # Adjust based on your strip
LED_BRIGHTNESS = 100

# Audio
RATE = 16000
CHUNK = 512


# ==========================================
# 2. GLOBAL STATE
# ==========================================
class RobotState:
    def __init__(self):
        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"
        self.voice_active = False
        self.command_detected_time = 0.0
        self.body_roll = 0.0  # IMU Tilt

        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0
        self.lock = threading.Lock()


state = RobotState()

# ==========================================
# 3. SERIAL CONNECTION & COMMS
# ==========================================
print(f"🔌 Connecting to ESP32 over {SERIAL_PORT}...")
try:
    esp32_serial = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    print("✅ Connected to ESP32")
except Exception as e:
    print(f"❌ Failed to connect: {e}")
    esp32_serial = None

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
                        with state.lock:
                            state.body_roll = float(line.split(":")[1])
                    except ValueError:
                        pass
                elif line == "READY":
                    _esp32_ready.set()
                elif line:
                    print(f"🤖 [ESP32] {line}")
            except Exception:
                time.sleep(0.1)
        else:
            time.sleep(0.1)


def send_to_esp32(command):
    if not (esp32_serial and esp32_serial.is_open):
        print(f" [Sim] Would send: {command}")
        return
    with _send_lock:
        if not _esp32_ready.wait(timeout=5.0):
            print(f"⚠️ ESP32 timeout — forcing command: {command}")
        _esp32_ready.clear()
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
        except Exception as e:
            print(f"Send failed: {e}")
            _esp32_ready.set()


# ==========================================
# 4. LED SYNC ENGINE
# ==========================================
def led_loop():
    strip = PixelStrip(NUM_LEDS, LED_PIN, 800000, 10, False, LED_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB)
    strip.begin()

    def fill(color):
        for i in range(NUM_LEDS): strip.setPixelColor(i, color)

    while True:
        with state.lock:
            dt = time.time() - state.command_detected_time
            va = state.voice_active
            speed = state.music_speed

        now_ms = int(time.time() * 1000)

        # STATE 1: Success Flash (White)
        if dt < 0.25:
            fill(Color(255, 255, 255))

        # STATE 2: Success Glow (Blue)
        elif dt < 1.0:
            fill(Color(0, 50, 255))

        # STATE 3: Listening (Green Breathing)
        elif va:
            lvl = (math.sin(now_ms * 0.005) + 1) / 2
            fill(Color(0, int(50 + lvl * 200), 0))

        # STATE 4: Fast Music (Rainbow/Party)
        elif speed == "FAST":
            for i in range(NUM_LEDS):
                hue = ((i * 20 + now_ms // 5) % 256) / 256.0
                r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                strip.setPixelColor(i, Color(int(r * 255), int(g * 255), int(b * 255)))

        # STATE 5: Medium/Slow Music (Purple/Pink Wave)
        elif speed in ["MEDIUM", "SLOW"]:
            for i in range(NUM_LEDS):
                lvl = (math.sin((now_ms * 0.003) - (i * 0.5)) + 1) / 2
                strip.setPixelColor(i, Color(int(150 * lvl), 0, int(255 * lvl)))

        # STATE 6: IDLE (Cyan Breathing)
        else:
            lvl = (math.sin(now_ms * 0.002) + 1) / 2
            c_val = int(20 + lvl * 100)
            fill(Color(0, c_val, c_val))

        strip.show()
        time.sleep(0.03)


# ==========================================
# 5. AUDIO AI & VAD ENGINE
# ==========================================
# Bandpass filter for isolating vocals
def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    b, a = butter(order, [lowcut / nyq, highcut / nyq], btype='band')
    return b, a


_BP_B, _BP_A = butter_bandpass(300, 3400, RATE, order=4)


def bandpass(data):
    return np.ascontiguousarray(lfilter(_BP_B, _BP_A, data), dtype=np.float32)


yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
YAMNET_CLASSES = []
class_map_path = yamnet_model.class_map_path().numpy().decode('utf-8')
with tf.io.gfile.GFile(class_map_path) as f:
    for row in csv.DictReader(f): YAMNET_CLASSES.append(row['display_name'])

audio_buffer = np.zeros(RATE * 3, dtype=np.float32)
recognizer = sr.Recognizer()

COMMANDS = [
    (["forward", "advance"], "WALK_FORWARD", "walking forward"),
    (["backward", "back"], "WALK_BACKWARD", "walking backward"),
    (["left"], "TURN_LEFT", "turning left"),
    (["right"], "TURN_RIGHT", "turning right"),
    (["stop", "stand"], "STAND", "stopping"),
    (["dance", "party"], "DANCE_CIRCLE", "lets party"),
    (["slow"], "DANCE_ROLL_SLOW", "slow mode"),
    (["fast"], "DANCE_ROLL_FAST", "high speed"),
    (["twist"], "DANCE_TWIST", "doing the twist"),
]


def say_phrase_offline(text):
    def _speak():
        try:
            e = pyttsx3.init()
            e.setProperty('rate', 145)
            e.say(text)
            e.runAndWait()
        except:
            pass

    threading.Thread(target=_speak, daemon=True).start()


def run_yamnet_periodically():
    while True:
        time.sleep(4)
        snap = np.copy(audio_buffer)
        scores, _, _ = yamnet_model(snap)
        top = int(np.argmax(np.mean(scores, axis=0)))
        with state.lock:
            if "CMD" not in state.genre: state.genre = YAMNET_CLASSES[top]


def process_voice_command(audio_bytes):
    try:
        text = recognizer.recognize_google(sr.AudioData(audio_bytes, RATE, 2), language='en-US').lower()
        print(f"🎤 [VOICE] Recognized: '{text}'")
        matched = False
        for keywords, cmd, phrase in COMMANDS:
            if any(kw in text for kw in keywords):
                send_to_esp32(cmd)
                say_phrase_offline(phrase)
                with state.lock:
                    state.command_detected_time = time.time()
                    state.voice_override_until = time.time() + 15.0
                matched = True
                break
    except Exception as e:
        print(f"🎤 [VOICE] Unclear or Error")

    with state.lock:
        state.voice_active = False


def audio_listener():
    global audio_buffer
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)

    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.3)

    mic = sc.default_microphone()
    syllables = []
    beat_history = []

    with mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            chunk = recorder.record(numframes=CHUNK).flatten().astype(np.float32)
            now = time.time()

            # Feed buffers
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = chunk

            # VAD (Syllable Counter)
            vocal_audio = bandpass(chunk)
            if aubio_syllable(vocal_audio)[0]:
                syllables.append(now)
            syllables = [t for t in syllables if now - t <= 3.0]

            with state.lock:
                va = state.voice_active
                override = now < state.voice_override_until

            # Trigger Voice Recognition
            if len(syllables) > 8 and not va and not override:
                with state.lock:
                    state.voice_active = True
                audio_bytes = (np.concatenate([audio_buffer[-RATE * 4:]]) * 32767).astype(np.int16).tobytes()
                threading.Thread(target=process_voice_command, args=(audio_bytes,), daemon=True).start()
                syllables.clear()

            # Beat Tracking & Dance Orchestration
            if aubio_tempo(chunk)[0]:
                bpm = aubio_tempo.get_bpm()
                if 40 < bpm < 90: bpm *= 2
                if 50 < bpm < 200: beat_history.append(bpm)
                with state.lock:
                    state.beat_hit = True
                    state.bpm = np.median(beat_history) if beat_history else bpm

            # 3-Second Automated Dance Window
            with state.lock:
                if (now - state.last_dance_command_time) >= 3.0 and not override and not va and len(beat_history) >= 3:
                    avg_bpm = np.median(beat_history)
                    if any(s in state.genre for s in ["Acoustic", "Vocal", "Speech", "Choir"]) or avg_bpm < 100:
                        state.music_speed, move = "SLOW", random.choice(["DANCE_ROLL_SLOW", "DANCE_CRAWL"])
                    elif avg_bpm < 130:
                        state.music_speed, move = "MEDIUM", random.choice(["DANCE_TWIST", "DANCE_SALSA"])
                    else:
                        state.music_speed, move = "FAST", random.choice(
                            ["DANCE_ROLL_FAST", "DANCE_STROBE", "DANCE_PULSE"])

                    send_to_esp32(move)
                    state.last_dance_command_time = now
                    beat_history.clear()


# ==========================================
# 6. DISPLAY ENGINE (EYES)
# ==========================================
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(spi, cs=digitalio.DigitalInOut(DISPLAY_CS_PIN), dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
                           rst=digitalio.DigitalInOut(DISPLAY_RST_PIN), rotation=90, baudrate=24000000)


def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    r = min(corner_radius, (x1 - x0) // 2, (y1 - y0) // 2)
    if r <= 0: draw.rectangle([x0, y0, x1, y1], fill=fill); return
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.pieslice([x0, y0, x0 + r * 2, y0 + r * 2], 180, 270, fill=fill)
    draw.pieslice([x1 - r * 2, y1 - r * 2, x1, y1], 0, 90, fill=fill)
    draw.pieslice([x0, y1 - r * 2, x0 + r * 2, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - r * 2, y0, x1, y0 + r * 2], 270, 360, fill=fill)


def display_loop():
    os.system("amixer set Master 100% > /dev/null 2>&1")
    disp = init_display()

    eye_w, eye_h = 70, 120
    lx, rx, cy = 90, 230, 120
    blink_timer = time.time()
    is_blinking = False

    while True:
        with state.lock:
            speed, beat_active, va, cmd_t, bpm, roll = state.music_speed, state.beat_hit, state.voice_active, state.command_detected_time, state.bpm, state.body_roll
            state.beat_hit = False

        dt = time.time() - cmd_t
        bg = (255, 255, 255) if dt < 0.25 else (30, 30, 80) if dt < 1.0 else (10, 35, 15) if va else (0, 0, 0)
        img = Image.new("RGB", (320, 240), color=bg)
        draw = ImageDraw.Draw(img)

        h, col, cy_r = eye_h, (0, 255, 255), cy
        if dt < 0.25:
            col, h, cy_r = (0, 0, 0), int(eye_h * 0.4), cy - 10
        elif dt < 1.0:
            col, h, cy_r = (0, 191, 255), int(eye_h * 0.4), cy - 10
        elif va:
            col, h = (0, 255, 100), int(eye_h * 0.75)
        elif speed == "FAST":
            col, h = (255, 50, 50), eye_h + 20
        elif speed == "MEDIUM":
            col, h = (255, 150, 50), eye_h + 10
        elif speed == "SLOW":
            col, h = (150, 50, 255), int(eye_h * 0.6)

        ew = eye_w + 10 if (beat_active and not va and dt > 1.0) else eye_w

        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True;
            blink_timer = time.time()
        if is_blinking and not va and dt > 1.0:
            h = 10
            if time.time() - blink_timer > 0.15: is_blinking = False

        # IMU Tilt Math
        roll_offset = int(roll * 1.5)
        cy_left, cy_right = cy_r + roll_offset, cy_r - roll_offset

        draw_rounded_rect(draw, [lx - ew // 2, cy_left - h // 2, lx + ew // 2, cy_left + h // 2], corner_radius=20,
                          fill=col)
        draw_rounded_rect(draw, [rx - ew // 2, cy_right - h // 2, rx + ew // 2, cy_right + h // 2], corner_radius=20,
                          fill=col)

        disp.image(img)
        time.sleep(0.03)


# ==========================================
# 7. BOOT SEQUENCE
# ==========================================
if __name__ == "__main__":
    print("\n🚀 BOOTING HEXAPOD AI ENGINE...\n")
    threading.Thread(target=esp32_reader_thread, daemon=True).start()
    threading.Thread(target=run_yamnet_periodically, daemon=True).start()
    threading.Thread(target=audio_listener, daemon=True).start()
    threading.Thread(target=led_loop, daemon=True).start()  # <-- Starts LED Sync
    display_loop()  # Blocks main thread