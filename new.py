import sys
import importlib.util
import os
import random


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
import threading
import time
import csv
import colorsys
import speech_recognition as sr
import pyttsx3
from scipy.signal import butter, lfilter

# Display & Graphics Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341

# ==========================================
# 1. SETUP USB SERIAL CABLE (PI -> ESP32)
# ==========================================
print("🔌 Connecting to ESP32 over USB...")
try:
    # Most USB cables on Pi map to ttyUSB0 or ttyACM0
    esp32_serial = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    print("✅ Successfully connected to ESP32 on /dev/ttyUSB0")
except Exception as e:
    print(f"❌ Failed to connect to ESP32 via USB. Is it plugged in? Error: {e}")
    esp32_serial = None


def send_to_esp32(command):
    """Sends a string command over the USB cable to the ESP32."""
    if esp32_serial and esp32_serial.is_open:
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
            print(f"📡 [USB] Sent to ESP32: {command}")
        except Exception as e:
            print(f"❌ [USB] Failed to send: {e}")


# ==========================================
# 2. AUDIO CONFIGURATION
# ==========================================
RATE = 16000
CHUNK = 256


def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a


def butter_bandpass_filter(data, lowcut=300, highcut=3000, fs=RATE, order=4):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    return np.ascontiguousarray(y, dtype=np.float32)


DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25


# ==========================================
# 3. GLOBAL STATE
# ==========================================
class RobotState:
    def __init__(self):
        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"
        self.voice_active = False
        self.command_detected_time = 0.0

        # --- IMPROVED: Faster 1.5-Second Window ---
        self.bpm_history = []
        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0
        self.dance_window_seconds = 1.5  # REDUCED from 3.0

        self.lock = threading.Lock()


state = RobotState()

# ==========================================
# 4. SETUP AI ENGINE
# ==========================================
yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')


def get_class_names():
    class_map_path = yamnet_model.class_map_path().numpy().decode('utf-8')
    class_names = []
    with tf.io.gfile.GFile(class_map_path) as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            class_names.append(row['display_name'])
    return class_names


YAMNET_CLASSES = get_class_names()

audio_buffer = np.zeros(RATE * 3, dtype=np.float32)
voice_byte_buffer = b""
recognizer = sr.Recognizer()
syllable_timestamps = []


def say_phrase_offline(text_to_say):
    def speak_worker():
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 145)
            engine.setProperty('volume', 1.0)
            engine.say(text_to_say)
            engine.runAndWait()
        except Exception as e:
            pass

    t = threading.Thread(target=speak_worker, daemon=True)
    t.start()


def run_yamnet_periodically():
    while True:
        time.sleep(4)
        snapshot = np.copy(audio_buffer)
        scores, embeddings, spectrogram = yamnet_model(snapshot)
        mean_scores = np.mean(scores, axis=0)
        top_class_index = np.argmax(mean_scores)
        with state.lock:
            if "CMD" not in state.genre:
                state.genre = YAMNET_CLASSES[top_class_index]


# ==========================================
# 5. VOICE COMMAND PROCESSOR (Instant Priority)
# ==========================================
def process_voice_command(audio_bytes):
    print("\n🎤 [VOICE] Syllable spike detected! Analyzing...")
    try:
        audio_data = sr.AudioData(audio_bytes, RATE, 2)
        text = recognizer.recognize_google(audio_data).lower()
        print(f"🎤 [VOICE] Recognized: '{text}'")

        matched = False

        # Priority 1: Walks & Stops
        if "forward" in text:
            send_to_esp32("WALK_FORWARD")
            say_phrase_offline("walking forward")
            matched = True
        elif "back" in text:
            send_to_esp32("WALK_BACKWARD")
            say_phrase_offline("walking backward")
            matched = True
        elif "left" in text:
            send_to_esp32("TURN_LEFT")
            say_phrase_offline("turning left")
            matched = True
        elif "right" in text:
            send_to_esp32("TURN_RIGHT")
            say_phrase_offline("turning right")
            matched = True
        elif "stop" in text or "stand" in text:
            send_to_esp32("STAND")
            say_phrase_offline("stopping now")
            matched = True

        # Priority 2: Manual Dances
        elif "dance" in text or "party" in text:
            send_to_esp32("DANCE_CIRCLE")
            say_phrase_offline("lets party")
            matched = True
        elif "slow" in text or "acoustic" in text:
            send_to_esp32("DANCE_ROLL_SLOW")
            say_phrase_offline("entering slow mode")
            matched = True
        elif "fast" in text or "speed" in text:
            send_to_esp32("DANCE_ROLL_FAST")
            say_phrase_offline("initiating high speed")
            matched = True

        if matched:
            with state.lock:
                state.command_detected_time = time.time()
                # Lock out the music AI for 10 seconds so the manual command can finish!
                state.voice_override_until = time.time() + 10.0
                state.bpm_history.clear()

    except Exception as e:
        print(f"🎤 [VOICE] Failed/Unclear: {e}")
    finally:
        time.sleep(2.0)
        with state.lock:
            state.voice_active = False


# ==========================================
# 6. AUDIO & FASTER TRANSITION LISTENER
# ==========================================
def audio_listener():
    global audio_buffer, syllable_timestamps, voice_byte_buffer
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.6)
    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.3)

    default_mic = sc.default_microphone()

    print(f"\n=== AI DANCER PROTOTYPE READY ===")
    print("Say 'walk forward', 'dance', 'stop', or play music!\n")

    with default_mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw_data = recorder.record(numframes=CHUNK)
            audio_chunk = raw_data.flatten().astype(np.float32)

            # Feed buffers
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = audio_chunk

            int16_chunk = (audio_chunk * 32767).astype(np.int16).tobytes()
            voice_byte_buffer += int16_chunk
            if len(voice_byte_buffer) > RATE * 4 * 2:
                voice_byte_buffer = voice_byte_buffer[-(RATE * 4 * 2):]

            vocal_audio = butter_bandpass_filter(audio_chunk)
            current_time = time.time()

            # Voice Trigger
            if aubio_syllable(vocal_audio)[0]:
                syllable_timestamps.append(current_time)
            syllable_timestamps = [t for t in syllable_timestamps if current_time - t <= 3.0]

            with state.lock:
                is_voice_busy = state.voice_active

            if len(syllable_timestamps) > 8 and not is_voice_busy:
                with state.lock:
                    state.voice_active = True
                voice_snapshot = bytes(voice_byte_buffer)
                threading.Thread(target=process_voice_command, args=(voice_snapshot,), daemon=True).start()

            # Beat Detection
            if aubio_tempo(audio_chunk)[0]:
                bpm = aubio_tempo.get_bpm()
                with state.lock:
                    genre = state.genre
                    if 40 < bpm < 90 and any(g in genre for g in ["Electronic", "Dance", "Rock"]):
                        bpm *= 2

                    state.bpm = bpm
                    state.beat_hit = True
                    state.bpm_history.append(bpm)

            # --- IMPROVED: 1.5-SECOND WINDOW FOR FASTER BEAT SYNCING ---
            with state.lock:
                window_size = state.dance_window_seconds
                # Check if it has been N seconds since we last sent a dance command
                if current_time - state.last_dance_command_time >= window_size:

                    # Ensure we are not currently locked out by a Voice Override
                    if current_time > state.voice_override_until and not state.voice_active:

                        if len(state.bpm_history) > 0:
                            # Calculate the average BPM over the window
                            avg_bpm = sum(state.bpm_history) / len(state.bpm_history)
                            vocal_genres = ["Acoustic", "Vocal", "Speech", "Choir", "Folk", "Singer"]

                            # Decision Engine
                            if any(g in state.genre for g in vocal_genres) or avg_bpm <= 110:
                                # SLOW DANCE POOL
                                state.music_speed = "SLOW"
                                next_move = random.choice(
                                    ["DANCE_ROLL_SLOW", "DANCE_PEACOCK", "DANCE_WAVE", "DANCE_RIPPLE"])
                                print(
                                    f"🎼 [MUSIC AI] Slow/Acoustic detected (Avg {avg_bpm:.1f} BPM). Executing: {next_move}")
                            else:
                                # FAST DANCE POOL
                                state.music_speed = "FAST"
                                next_move = random.choice(
                                    ["DANCE_ROLL_FAST", "DANCE_TWIST_2", "DANCE_CIRCLE_2", "DANCE_SALSA"])
                                print(
                                    f"🔥 [MUSIC AI] Fast/Upbeat detected (Avg {avg_bpm:.1f} BPM). Executing: {next_move}")

                            # Send the automated dance to ESP32
                            send_to_esp32(next_move)

                        # Reset the window
                        state.last_dance_command_time = current_time
                        state.bpm_history.clear()


ai_thread = threading.Thread(target=run_yamnet_periodically, daemon=True)
ai_thread.start()

audio_thread = threading.Thread(target=audio_listener, daemon=True)
audio_thread.start()


# ==========================================
# 7. DISPLAY ENGINE
# ==========================================
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    disp = ili9341.ILI9341(
        spi, cs=digitalio.DigitalInOut(DISPLAY_CS_PIN),
        dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
        rst=digitalio.DigitalInOut(DISPLAY_RST_PIN),
        rotation=90, baudrate=24000000
    )
    return disp


def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    w, h = x1 - x0, y1 - y0
    r = min(corner_radius, w // 2, h // 2)
    if r <= 0:
        draw.rectangle([x0, y0, x1, y1], fill=fill)
        return
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.pieslice([x0, y0, x0 + r * 2, y0 + r * 2], 180, 270, fill=fill)
    draw.pieslice([x1 - r * 2, y1 - r * 2, x1, y1], 0, 90, fill=fill)
    draw.pieslice([x0, y1 - r * 2, x0 + r * 2, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - r * 2, y0, x1, y0 + r * 2], 270, 360, fill=fill)


def display_loop():
    os.system("amixer set Master 100% > /dev/null 2>&1")
    disp = init_display()
    width, height = 320, 240

    eye_width, eye_height = 70, 120
    left_x, right_x = 90, 230
    center_y = 120
    blink_timer = time.time()
    is_blinking = False

    while True:
        with state.lock:
            speed = state.music_speed
            beat_active = state.beat_hit
            voice_active = state.voice_active
            cmd_time = state.command_detected_time
            state.beat_hit = False

        time_since_cmd = time.time() - cmd_time

        # Background
        if time_since_cmd < 0.25:
            bg_color = (255, 255, 255)
        elif time_since_cmd < 1.0:
            bg_color = (30, 30, 80)
        elif voice_active:
            bg_color = (10, 35, 15)
        else:
            bg_color = (0, 0, 0)

        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Eyes
        current_h = eye_height
        color = (0, 255, 255)
        center_y_render = center_y

        if time_since_cmd < 0.25:
            color, current_h, center_y_render = (0, 0, 0), int(eye_height * 0.4), center_y - 10
        elif time_since_cmd < 1.0:
            color, current_h, center_y_render = (0, 191, 255), int(eye_height * 0.4), center_y - 10
        elif voice_active:
            color, current_h = (0, 255, 100), int(eye_height * 0.75)
        elif speed == "DANCE":
            hue = (time.time() * 2) % 1.0
            r_val, g_val, b_val = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            color, current_h = (int(r_val * 255), int(g_val * 255), int(b_val * 255)), eye_height + 15
        elif speed == "FAST":
            color, current_h = (255, 50, 50), eye_height + 20
        elif speed == "SLOW":
            color, current_h = (150, 50, 255), int(eye_height * 0.6)

            # Beat Pulse
        eye_width_render = eye_width + 10 if (beat_active and not voice_active and time_since_cmd > 1.0) else eye_width

        # Blinking
        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True
            blink_timer = time.time()
        if is_blinking and not voice_active and time_since_cmd > 1.0:
            current_h = 10
            if time.time() - blink_timer > 0.15: is_blinking = False

        draw_rounded_rect(draw, [left_x - eye_width_render // 2, center_y_render - current_h // 2,
                                 left_x + eye_width_render // 2, center_y_render + current_h // 2], corner_radius=20,
                          fill=color)
        draw_rounded_rect(draw, [right_x - eye_width_render // 2, center_y_render - current_h // 2,
                                 right_x + eye_width_render // 2, center_y_render + current_h // 2], corner_radius=20,
                          fill=color)

        disp.image(img)
        time.sleep(0.03)

    # Start


display_loop()
