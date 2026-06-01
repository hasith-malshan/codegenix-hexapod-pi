import sys
import importlib.util
import os  # NEW: Imported to run system-level volume commands


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
import colorsys
import speech_recognition as sr
import pyttsx3  # Offline Text-To-Speech
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
        self.music_speed = "IDLE"  # IDLE, SLOW, FAST, DANCE
        self.voice_active = False
        self.command_detected_time = 0.0
        self.lock = threading.Lock()


state = RobotState()

# ==========================================
# 5. SETUP AI ENGINE & WAKE WORD DETECTORS
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

# Audio and voice buffers
BUFFER_LENGTH = RATE * 3
audio_buffer = np.zeros(BUFFER_LENGTH, dtype=np.float32)

# Rolling buffer for raw 16-bit PCM voice data (4 seconds capacity)
voice_byte_buffer = b""

# Setup Speech Recognizer
recognizer = sr.Recognizer()

# We will use a list to store the exact timestamps of every detected syllable
syllable_timestamps = []


# ==========================================
# 6. TEXT-TO-SPEECH (OFFLINE SYNTHESIZER)
# ==========================================
def say_phrase_offline(text_to_say):
    """Generates and speaks a custom phrase offline in a background thread."""

    def speak_worker():
        try:
            # Initialize the enlist locally within the thread to avoid locking [2]
            engine = pyttsx3.init()

            # Set properties (145 rate gives a clear, deliberate, sci-fi robot voice)
            engine.setProperty('rate', 145)
            engine.setProperty('volume', 1.0)  # Maximized digital volume

            print(f"🔊 [TTS] Robot saying: '{text_to_say}'")
            engine.say(text_to_say)
            engine.runAndWait()
        except Exception as e:
            print(f"❌ [TTS] Speech synthesis failed: {e}")

    # Run in a background thread so it doesn't freeze the eye animations [2]
    t = threading.Thread(target=speak_worker, daemon=True)
    t.start()


# ==========================================
# 7. BACKGROUND THREADS: Audio Analysis & Voice
# ==========================================
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


def process_voice_command(audio_bytes):
    """Processes the buffered 16-bit voice bytes using Google Speech API without blocking."""
    print("\n🎤 [VOICE] Syllable spike detected! Analyzing voice command...")

    try:
        # Convert the raw byte array back into an AudioData structure for SpeechRecognition
        audio_data = sr.AudioData(audio_bytes, RATE, 2)  # 2 bytes per sample (16-bit)
        text = recognizer.recognize_google(audio_data).lower()
        print(f"🎤 [VOICE] Recognized: '{text}'")

        # Check for commands
        matched = False
        if any(word in text for word in ["stop", "halt", "stand", "quiet"]):
            with state.lock:
                state.music_speed = "IDLE"
                state.genre = "CMD: STOPPED"
                state.command_detected_time = time.time()  # Trigger success flash [2]
            say_phrase_offline("stopping now")  # Synthesize voice response
            matched = True
        elif any(word in text for word in ["dance", "show me", "party"]):
            with state.lock:
                state.music_speed = "DANCE"
                state.genre = "CMD: PARTY MODE"
                state.command_detected_time = time.time()  # Trigger success flash [2]
            say_phrase_offline("lets dance")  # Synthesize voice response
            matched = True
        elif any(word in text for word in ["slow", "acoustic", "relax"]):
            with state.lock:
                state.music_speed = "SLOW"
                state.genre = "CMD: SLOW"
                state.command_detected_time = time.time()  # Trigger success flash [2]
            say_phrase_offline("entering slow mode")  # Synthesize voice response
            matched = True
        elif any(word in text for word in ["fast", "quick", "speed"]):
            with state.lock:
                state.music_speed = "FAST"
                state.genre = "CMD: FAST"
                state.command_detected_time = time.time()  # Trigger success flash [2]
            say_phrase_offline("initiating high speed")  # Synthesize voice response
            matched = True

        if not matched:
            print("🎤 [VOICE] Command did not match library.")

    except sr.UnknownValueError:
        print("🎤 [VOICE] Speech was unclear.")
    except sr.RequestError as e:
        print(f"🎤 [VOICE] API Error: {e}")
    finally:
        # Prevent immediate re-trigger and lower active flag
        time.sleep(2.0)
        with state.lock:
            state.voice_active = False


def audio_listener():
    global audio_buffer, syllable_timestamps, voice_byte_buffer
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.6)

    # Syllable/vocal onset tracker
    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.3)

    # Listen to default physical microphone
    default_mic = sc.default_microphone()

    print(f"\n=== AI DANCER PROTOTYPE READY ===")
    print(f"Listening to physical microphone: [{default_mic.name}]")
    print("Say 'dance', 'stop', or play music! Press Ctrl+C to stop.\n")

    with default_mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw_data = recorder.record(numframes=CHUNK)
            audio_chunk = raw_data.flatten().astype(np.float32)

            # A. Feed rolling float32 buffer (for YAMNet)
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = audio_chunk

            # B. Convert float32 chunk to 16-bit PCM bytes for the Speech Buffer
            int16_chunk = (audio_chunk * 32767).astype(np.int16).tobytes()
            voice_byte_buffer += int16_chunk

            # Keep only the last 4 seconds of voice bytes
            max_bytes = RATE * 4 * 2  # 4s * 16000Hz * 2 bytes/sample
            if len(voice_byte_buffer) > max_bytes:
                voice_byte_buffer = voice_byte_buffer[-max_bytes:]

            # C. Filter vocal frequencies
            vocal_audio = butter_bandpass_filter(audio_chunk)

            # D. Count Syllables
            current_time = time.time()
            is_syllable = aubio_syllable(vocal_audio)
            if is_syllable[0]:
                syllable_timestamps.append(current_time)

            # Keep only last 3 seconds of syllables
            syllable_timestamps = [t for t in syllable_timestamps if current_time - t <= 3.0]
            syllables_in_last_3_sec = len(syllable_timestamps)

            # E. Trigger non-blocking Voice recognition if Syllable Threshold is breached
            with state.lock:
                is_voice_busy = state.voice_active

            if syllables_in_last_3_sec > 8 and not is_voice_busy:
                with state.lock:
                    state.voice_active = True
                # Snapshot the current voice bytes and spawn processing thread
                voice_snapshot = bytes(voice_byte_buffer)
                vt = threading.Thread(target=process_voice_command, args=(voice_snapshot,), daemon=True)
                vt.start()

            # F. Detect Beats
            is_beat = aubio_tempo(audio_chunk)
            if is_beat[0]:
                bpm = aubio_tempo.get_bpm()

                with state.lock:
                    genre = state.genre

                    # Fix Half-Time Error for aggressive music
                    fast_genres = ["Electronic", "Dance", "Rock", "Metal", "Pop"]
                    if 40 < bpm < 90 and any(g in genre for g in fast_genres):
                        bpm *= 2

                    state.bpm = bpm
                    state.beat_hit = True  # Trigger eye pulse

                    # If we are locked in a manual voice command, skip automatic updates
                    if "CMD" in genre:
                        dance_style = f"EXECUTING {genre}"
                        reasoning = "Voice Command Override"
                    else:
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

                    print(
                        f"🎵 [BEAT] BPM: {bpm:5.1f} | Syllables: {syllables_in_last_3_sec:2d}/3s | AI: {genre:15.15} | CMD: {dance_style:24.24} | Reason: {reasoning}")


ai_thread = threading.Thread(target=run_yamnet_periodically, daemon=True)
ai_thread.start()

audio_thread = threading.Thread(target=audio_listener, daemon=True)
audio_thread.start()


# ==========================================
# 8. DISPLAY ENGINE (Cozmo/Vector Style Eyes)
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
    # --- AUTOMATIC VOLUME MAXIMIZER ---
    # Automatically force the Pi's ALSA master volume, playbacks, and PulseAudio sinks to 100% on boot
    print("🔊 Maximizing system-level volume outputs...")
    os.system("amixer set Master 100% > /dev/null 2>&1")
    os.system("amixer sset 'Playback' 100% > /dev/null 2>&1")
    os.system("amixer sset 'Speaker' 100% > /dev/null 2>&1")
    os.system("pactl set-sink-volume @DEFAULT_SINK@ 100% > /dev/null 2>&1")

    disp = init_display()
    width, height = 320, 240

    # Eye variables
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

            # Calculate time elapsed since a command was successfully detected
        time_since_cmd = time.time() - cmd_time

        # Background state machine
        if time_since_cmd < 0.25:
            # 1. PURE WHITE FLASH immediately upon detection (lasts 250ms) [2]
            bg_color = (255, 255, 255)
        elif time_since_cmd < 1.0:
            # 2. DEEP ELECTRIC BLUE GLOW for the next 750ms [2]
            bg_color = (30, 30, 80)
        elif voice_active:
            # 3. DEEP FOREST GREEN when actively listening to voice
            bg_color = (10, 35, 15)
        else:
            # 4. BLACK standard background
            bg_color = (0, 0, 0)

        img = Image.new("RGB", (width, height), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Determine Eye Shape, Height, and Color
        current_h = eye_height
        color = (0, 255, 255)
        center_y_render = center_y

        # Eyes State Machine
        if time_since_cmd < 0.25:
            # 1. JET BLACK SILHOUETTE EYES during the white flash [2]
            color = (0, 0, 0)
            current_h = int(eye_height * 0.4)
            center_y_render = center_y - 10
        elif time_since_cmd < 1.0:
            # 2. ELECTRIC BLUE HAPPY SQUINT EYES during success confirmation [2]
            color = (0, 191, 255)
            current_h = int(eye_height * 0.4)
            center_y_render = center_y - 10
        elif voice_active:
            # 3. CONCENTRATING NEON-GREEN eyes when listening
            color = (0, 255, 100)
            current_h = int(eye_height * 0.75)
        elif speed == "DANCE":
            # 4. DYNAMIC RAINBOW CYCLE for active party mode
            hue = (time.time() * 2) % 1.0
            r_val, g_val, b_val = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            color = (int(r_val * 255), int(g_val * 255), int(b_val * 255))
            current_h = eye_height + 15
        elif speed == "FAST":
            color = (255, 50, 50)
            current_h = eye_height + 20
        elif speed == "SLOW":
            color = (150, 50, 255)
            current_h = int(eye_height * 0.6)

            # 2. Beat Pulse Animation (Expand slightly exactly on the beat)
        if beat_active and not voice_active and time_since_cmd > 1.0:
            current_h += 30
            eye_width_render = eye_width + 10
        else:
            eye_width_render = eye_width

        # 3. Blinking Logic
        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True
            blink_timer = time.time()

        if is_blinking and not voice_active and time_since_cmd > 1.0:
            current_h = 10
            if time.time() - blink_timer > 0.15:
                is_blinking = False

        # 4. Draw Left Eye
        draw_rounded_rect(draw,
                          [left_x - eye_width_render // 2, center_y_render - current_h // 2,
                           left_x + eye_width_render // 2, center_y_render + current_h // 2],
                          corner_radius=20, fill=color)

        # 5. Draw Right Eye
        draw_rounded_rect(draw,
                          [right_x - eye_width_render // 2, center_y_render - current_h // 2,
                           right_x + eye_width_render // 2, center_y_render + current_h // 2],
                          corner_radius=20, fill=color)

        # Push to screen using PIL image method
        disp.image(img)
        time.sleep(0.03)

    # Start display loop


display_loop()