import sys
import importlib.util


# --- The "Smart" Python 3.13 Hack ---
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None


sys.modules['imp'] = FakeImp()
# ------------------------------------

import soundcard as sc
import numpy as np
import aubio
import tensorflow as tf
import tensorflow_hub as hub
import threading
import time
import csv

# Display & Graphics Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw

# --> FIXED THIS IMPORT: Changed from adafruit_ili9341 to adafruit_rgb_display <--
from adafruit_rgb_display import ili9341 as ili9341

# ==========================================
# 1. HARDWARE WIRING (From your config)
# ==========================================
DISPLAY_CS_PIN = board.CE0  # GPIO8
DISPLAY_DC_PIN = board.D24  # GPIO24
DISPLAY_RST_PIN = board.D25  # GPIO25


# ==========================================
# 2. GLOBAL STATE (Shared between AI & Display)
# ==========================================
class RobotState:
    def __init__(self):
        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"  # IDLE, SLOW, FAST
        self.lock = threading.Lock()


state = RobotState()

# ==========================================
# 3. SETUP YAMNET (AI AUDIO CLASSIFIER)
# ==========================================
print("Loading YAMNet AI Model... (This takes a moment)")
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

RATE = 16000
CHUNK = 256
BUFFER_LENGTH = RATE * 3
audio_buffer = np.zeros(BUFFER_LENGTH, dtype=np.float32)


# ==========================================
# 4. BACKGROUND THREADS: Audio Analysis
# ==========================================
def run_yamnet_periodically():
    while True:
        time.sleep(4)
        snapshot = np.copy(audio_buffer)
        scores, embeddings, spectrogram = yamnet_model(snapshot)
        mean_scores = np.mean(scores, axis=0)
        top_class_index = np.argmax(mean_scores)

        with state.lock:
            state.genre = YAMNET_CLASSES[top_class_index]


def audio_listener():
    global audio_buffer
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)

    # Listen to the Pi's internal Bluetooth Audio loopback
    default_speaker = sc.default_speaker()
    loopback_mic = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)

    with loopback_mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw_data = recorder.record(numframes=CHUNK)
            audio_chunk = raw_data.flatten().astype(np.float32)

            # Feed rolling buffer
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = audio_chunk

            # Detect Beats
            is_beat = aubio_tempo(audio_chunk)
            if is_beat[0]:
                bpm = aubio_tempo.get_bpm()

                with state.lock:
                    # Fix Half-Time Error for aggressive genres
                    fast_genres = ["Electronic", "Dance", "Rock", "Metal", "Pop"]
                    if 40 < bpm < 90 and any(g in state.genre for g in fast_genres):
                        bpm *= 2

                    state.bpm = bpm
                    state.beat_hit = True  # Trigger eye pulse

                    if bpm > 110:
                        state.music_speed = "FAST"
                    elif 0 < bpm <= 110 and "Music" in state.genre:
                        state.music_speed = "SLOW"
                    else:
                        state.music_speed = "IDLE"


ai_thread = threading.Thread(target=run_yamnet_periodically, daemon=True)
ai_thread.start()

audio_thread = threading.Thread(target=audio_listener, daemon=True)
audio_thread.start()


# ==========================================
# 5. DISPLAY ENGINE (Cozmo/Vector Style Eyes)
# ==========================================
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    cs_pin = digitalio.DigitalInOut(DISPLAY_CS_PIN)
    dc_pin = digitalio.DigitalInOut(DISPLAY_DC_PIN)
    rst_pin = digitalio.DigitalInOut(DISPLAY_RST_PIN)

    # --> FIXED INITIALIZATION: Use rotation=90 for 320x240 landscape layout <--
    disp = ili9341.ILI9341(
        spi, cs=cs_pin, dc=dc_pin, rst=rst_pin,
        rotation=90, baudrate=24000000
    )
    return disp


# Math function to draw thick rounded rectangles
def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    draw.rectangle([x0, y0 + corner_radius, x1, y1 - corner_radius], fill=fill)
    draw.rectangle([x0 + corner_radius, y0, x1 - corner_radius, y1], fill=fill)
    draw.pieslice([x0, y0, x0 + corner_radius * 2, y0 + corner_radius * 2], 180, 270, fill=fill)
    draw.pieslice([x1 - corner_radius * 2, y1 - corner_radius * 2, x1, y1], 0, 90, fill=fill)
    draw.pieslice([x0, y1 - corner_radius * 2, x0 + corner_radius * 2, y1], 90, 180, fill=fill)
    draw.pieslice([x1 - corner_radius * 2, y0, x1, y0 + corner_radius * 2], 270, 360, fill=fill)


def display_loop():
    disp = init_display()

    # 320x240 landscape dimensions
    width, height = 320, 240

    # Eye variables
    eye_width, eye_height = 70, 120
    left_x, right_x = 90, 230
    center_y = 120

    blink_timer = time.time()
    is_blinking = False

    while True:
        img = Image.new("RGB", (width, height), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)

        with state.lock:
            speed = state.music_speed
            bpm = state.bpm
            genre = state.genre
            beat_active = state.beat_hit
            state.beat_hit = False  # Reset beat immediately after reading

        # 1. Determine Eye Shape & Color based on Music Speed
        current_h = eye_height
        color = (0, 255, 255)  # Default Cyan (IDLE)

        if speed == "FAST":
            color = (255, 50, 50)  # Aggressive Red/Orange
            current_h = eye_height + 20  # Wide open
        elif speed == "SLOW":
            color = (150, 50, 255)  # Chill Purple
            current_h = int(eye_height * 0.6)  # Squinting / Relaxed

        # 2. Beat Pulse Animation (Expand slightly exactly on the beat)
        if beat_active:
            current_h += 30
            eye_width_render = eye_width + 10
        else:
            eye_width_render = eye_width

        # 3. Blinking Logic
        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True
            blink_timer = time.time()

        if is_blinking:
            current_h = 10  # Eyes close to slits
            if time.time() - blink_timer > 0.15:  # Blink lasts 150ms
                is_blinking = False

        # 4. Draw Left Eye
        draw_rounded_rect(draw,
                          [left_x - eye_width_render // 2, center_y - current_h // 2,
                           left_x + eye_width_render // 2, center_y + current_h // 2],
                          corner_radius=20, fill=color)

        # 5. Draw Right Eye
        draw_rounded_rect(draw,
                          [right_x - eye_width_render // 2, center_y - current_h // 2,
                           right_x + eye_width_render // 2, center_y + current_h // 2],
                          corner_radius=20, fill=color)

        # Push to screen using PIL image method
        disp.image(img)
        time.sleep(0.03)  # Limit to ~30 FPS to save CPU


# Start graphics thread
print("Starting Face Display Engine...")
display_loop()