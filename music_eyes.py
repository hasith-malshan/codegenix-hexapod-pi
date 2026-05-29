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
from scipy.signal import butter, lfilter

# Display & Graphics Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw

# Legacy PIL-compatible SPI display driver
from adafruit_rgb_display import ili9341 as ili9341

# ==========================================
# 1. AUDIO CONFIGURATION
# ==========================================
RATE = 16000
CHUNK = 256  # 16ms of audio for high-resolution tracking


# ==========================================
# 2. VOCAL BANDPASS FILTER (300Hz - 3000Hz)
# ==========================================
def butter_bandpass(lowcut, highcut, fs, order=4):
    nyq = 0.5 * fs
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype='band')
    return b, a


def butter_bandpass_filter(data, lowcut=300, highcut=3000, fs=RATE, order=4):
    b, a = butter_bandpass(lowcut, highcut, fs, order=order)
    y = lfilter(b, a, data)
    # Scipy outputs float64, Aubio requires float32 C-contiguous arrays
    return np.ascontiguousarray(y, dtype=np.float32)


# ==========================================
# 3. HARDWARE WIRING (From your config)
# ==========================================
DISPLAY_CS_PIN = board.CE0  # GPIO8
DISPLAY_DC_PIN = board.D24  # GPIO24
DISPLAY_RST_PIN = board.D25  # GPIO25


# ==========================================
# 4. GLOBAL STATE (Shared between AI & Display)
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
# 5. SETUP YAMNET (AI AUDIO CLASSIFIER)
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

BUFFER_LENGTH = RATE * 3
audio_buffer = np.zeros(BUFFER_LENGTH, dtype=np.float32)

# We will use a list to store the exact timestamps of every detected syllable
syllable_timestamps = []


# ==========================================
# 6. BACKGROUND THREADS: Audio Analysis
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
    global audio_buffer, syllable_timestamps
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.6)  # Ignore light instrument plucks

    # Syllable/vocal onset tracker
    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.3)

    # Listen to the Pi's internal Bluetooth Audio loopback
    default_speaker = sc.default_speaker()
    loopback_mic = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)

    with loopback_mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw_data = recorder.record(numframes=CHUNK)
            audio_chunk = raw_data.flatten().astype(np.float32)

            # Feed rolling buffer (for YAMNet)
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = audio_chunk

            # Filter vocal frequencies
            vocal_audio = butter_bandpass_filter(audio_chunk)

            # Count Syllables
            current_time = time.time()
            is_syllable = aubio_syllable(vocal_audio)
            if is_syllable[0]:
                syllable_timestamps.append(current_time)

            # Keep only last 3 seconds of syllables
            syllable_timestamps = [t for t in syllable_timestamps if current_time - t <= 3.0]
            syllables_in_last_3_sec = len(syllable_timestamps)

            # Detect Beats
            is_beat = aubio_tempo(audio_chunk)
            if is_beat[0]:
                bpm = aubio_tempo.get_bpm()

                with state.lock:
                    genre = state.genre

                    # Fix Half-Time Error for aggressive genres
                    fast_genres = ["Electronic", "Dance", "Rock", "Metal", "Pop"]
                    if 40 < bpm < 90 and any(g in genre for g in fast_genres):
                        bpm *= 2

                    state.bpm = bpm
                    state.beat_hit = True  # Trigger eye pulse

                    # Categories that trigger the Vocal/Syllable Override
                    vocal_genres = ["Acoustic", "Vocal", "Speech", "Choir", "Folk", "Singer",
                                    "Plucked string instrument"]

                    # 1. THE VOCAL OVERRIDE LOGIC
                    if any(g in genre for g in vocal_genres) or syllables_in_last_3_sec > 5:
                        if syllables_in_last_3_sec >= 12:
                            state.music_speed = "FAST"
                            dance_style = "FAST AGGRESSIVE IK MOVES"
                            reasoning = f"Rapid Speech ({syllables_in_last_3_sec} syll/3s)"
                        else:
                            state.music_speed = "SLOW"
                            dance_style = "SLOW SWAYING IK MOVES"
                            reasoning = f"Slow Vocals ({syllables_in_last_3_sec} syll/3s)"

                    # 2. THE STANDARD BPM LOGIC
                    else:
                        if bpm > 110:
                            state.music_speed = "FAST"
                            dance_style = "FAST AGGRESSIVE IK MOVES"
                            reasoning = "BPM Fast"
                        elif 0 < bpm <= 110 and "Music" in genre:
                            state.music_speed = "SLOW"
                            dance_style = "SLOW SWAYING IK MOVES"
                            reasoning = "BPM Slow"
                        else:
                            state.music_speed = "IDLE"
                            dance_style = "STOP DANCING - IDLE"
                            reasoning = "No stable rhythm"

                    # --> SINGLE-LINE CONSOLE OUTPUT: All telemetry packed into one clean line <--
                    print(
                        f"🎵 [BEAT] BPM: {bpm:5.1f} | Syllables: {syllables_in_last_3_sec:2d}/3s | AI: {genre:15.15} | CMD: {dance_style:24.24} | Reason: {reasoning}")


ai_thread = threading.Thread(target=run_yamnet_periodically, daemon=True)
ai_thread.start()

audio_thread = threading.Thread(target=audio_listener, daemon=True)
audio_thread.start()


# ==========================================
# 7. DISPLAY ENGINE (Cozmo/Vector Style Eyes)
# ==========================================
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    cs_pin = digitalio.DigitalInOut(DISPLAY_CS_PIN)
    dc_pin = digitalio.DigitalInOut(DISPLAY_DC_PIN)
    rst_pin = digitalio.DigitalInOut(DISPLAY_RST_PIN)

    disp = ili9341.ILI9341(
        spi, cs=cs_pin, dc=dc_pin, rst=rst_pin,
        rotation=90, baudrate=24000000
    )
    return disp


# Math function to draw thick rounded rectangles safely
def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    w = x1 - x0
    h = y1 - y0

    # Corner radius must never be larger than half the width or half the height of the shape
    r = min(corner_radius, w // 2, h // 2)

    # If the eye is completely shut/slit during a blink, draw a standard flat rectangle
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