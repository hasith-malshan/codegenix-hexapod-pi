import sys
import collections

sys.path.append("/home/codegenix/.local/lib/python3.13/site-packages")
import importlib.util
import os

os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
cookie_paths = ["/home/codegenix/.config/pulse/cookie", "/home/codegenix/.pulse-cookie",
                "/home/codegenix/.config/pulse-cookie"]
for path in cookie_paths:
    if os.path.exists(path):
        os.environ["PULSE_COOKIE"] = path
        break
os.environ.pop("XDG_RUNTIME_DIR", None)
os.environ["TFHUB_CACHE_DIR"] = "./ai_model_cache"


class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None: raise ImportError(f"No module named {name}")
        return None


sys.modules['imp'] = FakeImp()

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

import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341
from rpi_ws281x import PixelStrip, Color, ws

RATE = 16000
CHUNK = 256


def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    low, high = lowcut / nyq, highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a


def butter_bandpass_filter(data, lowcut=300, highcut=3000, fs=RATE, order=4):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    return np.ascontiguousarray(lfilter(b, a, data), dtype=np.float32)


DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25
LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
LED_BRIGHTNESS = 100


class RobotState:
    def __init__(self):
        self.operating_mode, self.audio_source = "AUTO", "MIC"
        self.show_audio_logs = False
        self.bpm, self.syllable_count, self.genre, self.last_beat_time = 0.0, 0, "Listening...", 0.0
        self.mood, self.voice_active, self.command_detected_time = "IDLE", False, 0.0
        self.body_roll, self.manual_led_pattern = 0.0, None
        self.last_dance_command_time, self.voice_override_until = time.time(), 0.0
        self.bpm_history = collections.deque(maxlen=20)
        self.lock = threading.Lock()


state = RobotState()


def connect_to_esp32():
    print("\n🔌 Searching for ESP32 via USB...")
    for port in ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/serial0']:
        try:
            s = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Successfully connected to ESP32 on {port}")
            return s
        except Exception:
            continue
    print("❌ Failed to find ESP32 on USB. Manual mode will simulate commands.")
    return None


esp32_serial = connect_to_esp32()


def esp32_reader_thread():
    while True:
        if esp32_serial and esp32_serial.is_open:
            try:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                if not line:
                    continue

                if line.startswith("TILT:"):
                    try:
                        roll_val = float(line.split(":")[1])
                        if not math.isnan(roll_val) and not math.isinf(roll_val):
                            with state.lock: state.body_roll = roll_val
                    except ValueError:
                        pass
                else:
                    # FIX: Safely print any other message from the ESP32 to SSH for debugging
                    print(f"🤖 [ESP32 COMMS]: {line}")
            except Exception:
                time.sleep(0.1)
        else:
            time.sleep(0.1)


def send_to_esp32(command):
    if not (esp32_serial and esp32_serial.is_open): return
    try:
        esp32_serial.write((command + "\n").encode('utf-8'))
        esp32_serial.flush()
        print(f"📡 Sent to ESP32: {command}")
    except Exception as e:
        print(f"❌ Serial write error: {e}")


strip = PixelStrip(NUM_LEDS, LED_PIN, 800000, 10, False, LED_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB)
strip.begin()


def hsv(hue, sat=255, val=255):
    r, g, b = colorsys.hsv_to_rgb((hue % 256) / 256.0, sat / 255.0, val / 255.0)
    return Color(int(r * 255), int(g * 255), int(b * 255))


def beatsin(bpm, low, high, phase=0):
    return int(low + ((math.sin(time.monotonic() * bpm * 2 * math.pi / 60 + phase) + 1) / 2) * (high - low))


def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255.0
    for i in range(NUM_LEDS):
        c = strip.getPixelColor(i)
        strip.setPixelColor(i, Color(int(((c >> 16) & 0xFF) * scale), int(((c >> 8) & 0xFF) * scale),
                                     int((c & 0xFF) * scale)))


def led_thread():
    frame = 0;
    heat = [0] * NUM_LEDS
    while True:
        try:
            with state.lock:
                mood, va, cmd_t, manual_led, bpm, beat_active = state.mood, state.voice_active, state.command_detected_time, state.manual_led_pattern, state.bpm, (
                                                                                                                                                                              time.time() - state.last_beat_time) < 0.15
            dt = time.time() - cmd_t;
            frame += 1

            if dt < 0.25:
                for i in range(NUM_LEDS): strip.setPixelColor(i, Color(255, 255, 255))
            elif dt < 1.0:
                for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, 50, 255))
            elif va:
                strip.setPixelColor(0, Color(0, 0, 0));
                fade_to_black_by(60)
                pos = frame % (NUM_LEDS * 2 - 2);
                pos = NUM_LEDS * 2 - 2 - pos if pos >= NUM_LEDS else pos
                strip.setPixelColor(pos, Color(0, 255, 50))
            elif manual_led:
                if manual_led == "rainbow":
                    for i in range(NUM_LEDS): strip.setPixelColor(i, hsv((frame * 5 + i * 18) % 256))
                elif manual_led == "strobe":
                    for i in range(NUM_LEDS): strip.setPixelColor(i, hsv((frame * 11) % 256, 100, 255) if (
                                                                                                                      frame // 3) % 2 == 0 else Color(
                        0, 0, 0))
            elif mood == "AGGRESSIVE":
                for i in range(NUM_LEDS): strip.setPixelColor(i,
                                                              Color(255, 255, 255) if beat_active else Color(255, 0, 0))
            elif mood == "ENERGY":
                fade_to_black_by(40);
                pos = beatsin(bpm if bpm > 0 else 120, 0, NUM_LEDS - 1)
                strip.setPixelColor(pos, Color(255, 255, 255) if beat_active else hsv(int(time.monotonic() * 50) % 256))
            elif mood == "CHILL":
                for i in range(NUM_LEDS): strip.setPixelColor(i, hsv(frame + i * 10, 230, 255 if beat_active else int(
                    25 + ((math.sin(frame * ((bpm / 60.0) * 0.1 if bpm > 0 else 0.1) - i * 0.5) + 1) / 2) * 200)))
            else:
                for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, int(10 + (
                            (math.sin(frame * 0.05) + 1) / 2) * 80), int(10 + ((math.sin(frame * 0.05) + 1) / 2) * 80)))
            strip.show();
            time.sleep(0.02)
        except Exception:
            time.sleep(1)


def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(spi, cs=digitalio.DigitalInOut(DISPLAY_CS_PIN), dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
                           rst=digitalio.DigitalInOut(DISPLAY_RST_PIN), rotation=90, baudrate=24000000)


def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy;
    r = min(corner_radius, (x1 - x0) // 2, (y1 - y0) // 2)
    if r <= 0: draw.rectangle([x0, y0, x1, y1], fill=fill); return
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill);
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.pieslice([x0, y0, x0 + r * 2, y0 + r * 2], 180, 270, fill=fill);
    draw.pieslice([x1 - r * 2, y1 - r * 2, x1, y1], 0, 90, fill=fill)
    draw.pieslice([x0, y1 - r * 2, x0 + r * 2, y1], 90, 180, fill=fill);
    draw.pieslice([x1 - r * 2, y0, x1, y0 + r * 2], 270, 360, fill=fill)


def display_loop():
    try:
        disp = init_display()
    except Exception:
        return
    width, height, eye_w, eye_h, lx, rx, cy = 320, 240, 70, 120, 90, 230, 120
    blink_timer, blink_interval, is_blinking = time.time(), random.uniform(2.0, 5.0), False

    while True:
        try:
            with state.lock:
                mood, va, cmd_t, bpm, syl, roll = state.mood, state.voice_active, state.command_detected_time, state.bpm, state.syllable_count, state.body_roll; beat_active = (
                                                                                                                                                                                           time.time() - state.last_beat_time) < 0.15
            dt = time.time() - cmd_t
            bg = (255, 255, 255) if dt < 0.25 else (30, 30, 80) if dt < 1.0 else (10, 35, 15) if va else (0, 0, 0)
            img, col, h, cy_r = Image.new("RGB", (width, height), color=bg), (0, 255, 255), eye_h, cy
            draw = ImageDraw.Draw(img)
            draw.text((5, 5), f"BPM: {bpm:.0f} | Syl: {syl}/3s | Mood: {mood}", fill=(100, 100, 100))

            if dt < 0.25:
                col, h, cy_r = (0, 0, 0), int(eye_h * 0.4), cy - 10
            elif dt < 1.0:
                col, h, cy_r = (0, 191, 255), int(eye_h * 0.4), cy - 10
            elif va:
                col, h = (0, 255, 100), int(eye_h * 0.75)
            elif mood == "AGGRESSIVE":
                col, h = (255, 50, 50), eye_h + 20
            elif mood == "ENERGY":
                col, h = (255, 150, 50), eye_h + 10
            elif mood == "CHILL":
                col, h = (150, 50, 255), int(eye_h * 0.6)

            ew = eye_w + 10 if (beat_active and not va and dt > 1.0) else eye_w
            if time.time() - blink_timer > blink_interval: is_blinking, blink_timer, blink_interval = True, time.time(), random.uniform(
                2.0, 5.0)
            if is_blinking and not va and dt > 1.0: h = 10; is_blinking = time.time() - blink_timer <= 0.15

            roll_offset = int(roll * 1.5)
            draw_rounded_rect(draw,
                              [lx - ew // 2, cy_r + roll_offset - h // 2, lx + ew // 2, cy_r + roll_offset + h // 2],
                              20, col)
            draw_rounded_rect(draw,
                              [rx - ew // 2, cy_r - roll_offset - h // 2, rx + ew // 2, cy_r - roll_offset + h // 2],
                              20, col)
            disp.image(img);
            time.sleep(0.03)
        except Exception:
            time.sleep(1)


yamnet_model, YAMNET_CLASSES = None, []


def run_yamnet_periodically():
    global yamnet_model, YAMNET_CLASSES
    try:
        yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
        with tf.io.gfile.GFile(yamnet_model.class_map_path().numpy().decode('utf-8')) as f:
            YAMNET_CLASSES = [row['display_name'] for row in csv.DictReader(f)]
    except Exception:
        pass
    while True:
        try:
            time.sleep(4)
            if yamnet_model is None: continue
            scores, _, _ = yamnet_model(np.copy(audio_buffer))
            with state.lock:
                if "CMD" not in state.genre: state.genre = YAMNET_CLASSES[int(np.argmax(np.mean(scores, axis=0)))]
        except Exception:
            time.sleep(1)


audio_buffer = np.zeros(RATE * 3, dtype=np.float32)
recognizer = sr.Recognizer()


def audio_listener():
    global audio_buffer
    aubio_tempo, aubio_syllable = aubio.tempo("specflux", 1024, CHUNK, RATE), aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5);
    aubio_syllable.set_threshold(0.3)
    mic = sc.get_microphone(id=str(sc.default_speaker().name),
                            include_loopback=True) if state.audio_source == "BT" else sc.default_microphone()
    syllables, beat_debounce = [], time.time()

    with mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            try:
                chunk = recorder.record(numframes=CHUNK).flatten().astype(np.float32)
                now = time.time()
                audio_buffer = np.roll(audio_buffer, -CHUNK);
                audio_buffer[-CHUNK:] = chunk
                if aubio_syllable(butter_bandpass_filter(chunk))[0]: syllables.append(now)
                syllables = [t for t in syllables if now - t <= 3.0]
                with state.lock:
                    state.syllable_count, va, override = len(
                        syllables), state.voice_active, now < state.voice_override_until

                if aubio_tempo(chunk)[0] and (now - beat_debounce > 0.15):
                    bpm = aubio_tempo.get_bpm()
                    if 40 < bpm < 90: bpm *= 2
                    if 50 < bpm < 200:
                        with state.lock: state.bpm_history.append(bpm)
                    with state.lock:
                        state.last_beat_time = now
                        if state.bpm_history: state.bpm = np.median(list(state.bpm_history))
                    beat_debounce = now

                if state.operating_mode == "AUTO":
                    with state.lock:
                        if (now - state.last_dance_command_time) >= 3.0 and not override and not va and len(
                                state.bpm_history) >= 3:
                            avg_bpm, syl, genre = np.median(list(state.bpm_history)), state.syllable_count, state.genre
                            if any(s in genre for s in ["Acoustic", "Classical", "Folk"]) or (
                                    avg_bpm < 105 and syl < 6):
                                state.mood, move = "CHILL", "DANCE_ROLL_SLOW"
                            elif avg_bpm > 135 or syl > 12:
                                state.mood, move = "AGGRESSIVE", "DANCE_ROLL_FAST"
                            else:
                                state.mood, move = "ENERGY", "DANCE_CIRCLE"
                            send_to_esp32(move);
                            state.last_dance_command_time = now;
                            state.bpm_history.clear()
            except Exception:
                time.sleep(0.1)


CLI_COMMANDS = {15: "STAND", 21: "DANCE_WAVE", 26: "DANCE_TWIST", 28: "DANCE_ROLL", 32: "DANCE_CIRCLE"}


def manual_testing_loop():
    print("\n[15] STAND | [21] WAVE | [26] TWIST | [28] ROLL | [32] CIRCLE | [0] EXIT")
    while True:
        try:
            choice = input("\nCommand >>> ").strip()
            if choice == '0': os._exit(0)
            if choice.isdigit() and int(choice) in CLI_COMMANDS:
                send_to_esp32(CLI_COMMANDS[int(choice)])
        except KeyboardInterrupt:
            os._exit(0)


if __name__ == "__main__":
    print("\n=== 🤖 CODEGENIX HEXABOT OS ===")
    state.operating_mode = "MANUAL"
    threading.Thread(target=esp32_reader_thread, daemon=True).start()
    threading.Thread(target=run_yamnet_periodically, daemon=True).start()
    threading.Thread(target=audio_listener, daemon=True).start()
    threading.Thread(target=led_thread, daemon=True).start()
    threading.Thread(target=display_loop, daemon=True).start()

    time.sleep(1.0)
    manual_testing_loop()