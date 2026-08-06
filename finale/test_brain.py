#!/usr/bin/env python3
"""Hexabot OS - adaptive real-time music dancer for Raspberry Pi 4.
USB Serial Version with READY Handshake, Original Features, and File Logging.
"""

from __future__ import annotations

import collections
import colorsys
import csv
import importlib.util
import math
import os
import random
import sys
import threading
import time
import logging
from dataclasses import dataclass
from typing import Deque, Optional

# ===========================================================================
# 0. LOGGING SETUP (Writes to hexabot.log instead of console)
# ===========================================================================
log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hexabot.log")
logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%H:%M:%S'
)


def log_event(message: str):
    """Writes telemetry and background events to the log file."""
    logging.info(message)


# Let sudo-launched Python see packages installed for the normal Pi user.
USER_SITE = "/home/codegenix/.local/lib/python3.13/site-packages"
if USER_SITE not in sys.path:
    sys.path.append(USER_SITE)

# Connect a root-launched process to the user's PipeWire/PulseAudio server.
os.environ["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"
for cookie_path in (
        "/home/codegenix/.config/pulse/cookie",
        "/home/codegenix/.pulse-cookie",
        "/home/codegenix/.config/pulse-cookie",
):
    if os.path.exists(cookie_path):
        os.environ["PULSE_COOKIE"] = cookie_path
        break
os.environ.pop("XDG_RUNTIME_DIR", None)
os.environ.setdefault("TFHUB_CACHE_DIR", "./ai_model_cache")


# Compatibility for an old dependency that still imports Python's removed imp.
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None


sys.modules.setdefault("imp", FakeImp())

# Standard Python Libraries
import serial
import aubio
import numpy as np
import soundcard as sc
from scipy.signal import butter, lfilter

# Display & Graphics Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341

# LED Libraries
from rpi_ws281x import Color, PixelStrip, ws

# Optional AI / Voice Features
try:
    import tensorflow as tf
    import tensorflow_hub as hub
except Exception:
    tf = None
    hub = None

try:
    import speech_recognition as sr
    import pyttsx3
except Exception:
    sr = None
    pyttsx3 = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RATE = 16_000
CHUNK = 512  # 32 ms at 16 kHz
FFT_SIZE = 2048
TEMPO_WINDOW = 2048
STATE_UPDATE_SECONDS = 1.0
NO_BEAT_IDLE_SECONDS = 4.0

INITIAL_LISTEN_MOVE = "DANCE_CHASSIS_BREATHE"
ENABLE_YAMNET = True

DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25

LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
LED_BRIGHTNESS = 100


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


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
class RobotState:
    def __init__(self):
        self.operating_mode = "AUTO"
        self.audio_source = "MIC"
        self.show_audio_logs = False

        self.bpm = 0.0
        self.raw_bpm = 0.0
        self.beat_confidence = 0.0
        self.last_beat_time = 0.0
        self.syllable_count = 0

        self.energy_score = 0.0
        self.activity_score = 0.0
        self.rhythm_speed = "UNKNOWN"
        self.energy_level = "LOW"
        self.activity_level = "SMOOTH"
        self.mood = "IDLE"
        self.genre = "Listening..."
        self.audio_context = "Listening..."
        self.rms_db = -60.0
        self.peak_amplitude = 0.0

        self.voice_active = False
        self.command_detected_time = 0.0
        self.voice_override_until = 0.0

        self.body_roll = 0.0
        self.manual_led_pattern = None
        self.manual_mood = None

        # Buffer-safe Handshake Architecture
        self.robot_ready = False
        self.initial_listen_sent = False
        self.current_move = None
        self.planned_move = None
        self.last_plan_signature = None

        self.bpm_history: Deque[float] = collections.deque(maxlen=32)
        self.lock = threading.RLock()


state = RobotState()


# ---------------------------------------------------------------------------
# USB SERIAL / ESP32 COMMUNICATION (READY HANDSHAKE)
# ---------------------------------------------------------------------------
def connect_to_esp32():
    print("\n🔌 Searching for ESP32 via USB...")
    for port in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0"):
        try:
            connection = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Connected to ESP32 on {port}")
            log_event(f"--- SYSTEM BOOT: USB CONNECTED ON {port} ---")
            return connection
        except Exception:
            continue
    print("❌ ESP32 not found. Commands will be simulated.")
    return None


esp32_serial = connect_to_esp32()


def esp32_reader_thread():
    """Reads incoming USB serial data and triggers the READY handshake."""
    while True:
        if not (esp32_serial and esp32_serial.is_open):
            time.sleep(0.1)
            continue
        try:
            line = esp32_serial.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            # The Handshake Magic: When ESP32 says READY, send the next command
            if line == "READY":
                handle_robot_ready()
            elif line.startswith("TILT:"):
                try:
                    roll = float(line.split(":", 1)[1])
                    if math.isfinite(roll):
                        with state.lock:
                            state.body_roll = roll
                except ValueError:
                    pass
            else:
                log_event(f"🤖 [ESP32 SAYS]: {line}")
        except (ValueError, OSError, serial.SerialException) as e:
            log_event(f"⚠️ Serial read error: {e}")
            time.sleep(0.1)


def send_to_esp32(command: str):
    """Sends action command to ESP32 over USB Serial."""
    with state.lock:
        state.current_move = command

    if not (esp32_serial and esp32_serial.is_open):
        if state.show_audio_logs:
            log_event(f"🤖 [SIMULATED] {command}")
        return

    try:
        esp32_serial.write((command + "\n").encode("utf-8"))
        esp32_serial.flush()
        log_event(f"📡 [PI SENT]: {command}")
    except Exception as exc:
        log_event(f"❌ Serial write error: {exc}")


def handle_robot_ready():
    """Dispatch exactly one dance whenever the ESP32 reports completion."""
    if state.operating_mode != "AUTO":
        with state.lock:
            state.robot_ready = True
        return

    with state.lock:
        state.robot_ready = True

        if not state.initial_listen_sent:
            command = INITIAL_LISTEN_MOVE
            state.initial_listen_sent = True
        elif state.planned_move:
            command = state.planned_move
            state.planned_move = None
        else:
            command = choose_dance(state.rhythm_speed, state.energy_level, state.activity_level)

        state.robot_ready = False

    send_to_esp32(command)
    log_event(f"▶️ [READY→DANCE] Dispatched: {command}")


# ---------------------------------------------------------------------------
# Lock-safe circular audio buffer
# ---------------------------------------------------------------------------
class AudioRingBuffer:
    def __init__(self, samples: int):
        self.data = np.zeros(samples, dtype=np.float32)
        self.index = 0
        self.full = False
        self.lock = threading.Lock()

    def append(self, chunk: np.ndarray):
        chunk = np.asarray(chunk, dtype=np.float32)
        if len(chunk) >= len(self.data): chunk = chunk[-len(self.data):]
        with self.lock:
            end = self.index + len(chunk)
            if end <= len(self.data):
                self.data[self.index:end] = chunk
            else:
                first = len(self.data) - self.index
                self.data[self.index:] = chunk[:first]
                self.data[:end - len(self.data)] = chunk[first:]
            self.index = end % len(self.data)
            if end >= len(self.data): self.full = True

    def snapshot(self, sample_count: Optional[int] = None) -> np.ndarray:
        with self.lock:
            if self.full:
                ordered = np.concatenate((self.data[self.index:], self.data[:self.index]))
            else:
                ordered = self.data[:self.index].copy()
        if sample_count is not None: ordered = ordered[-sample_count:]
        return np.ascontiguousarray(ordered, dtype=np.float32)


audio_ring = AudioRingBuffer(RATE * 5)


# ---------------------------------------------------------------------------
# Adaptive music analyser
# ---------------------------------------------------------------------------
def robust_normalize(value: float, history: Deque[float], default=0.5) -> float:
    if len(history) < 30: return default
    values = np.asarray(history, dtype=np.float32)
    low, high = np.percentile(values, [20, 85])
    if high - low < 1e-6: return default
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


class AdaptiveMusicAnalyzer:
    def __init__(self):
        self.tempo = aubio.tempo("specflux", TEMPO_WINDOW, CHUNK, RATE)
        self.tempo.set_threshold(0.45)
        self.window = np.hanning(FFT_SIZE).astype(np.float32)
        self.previous_spectrum = np.zeros(FFT_SIZE // 2 + 1, dtype=np.float32)
        self.frequencies = np.fft.rfftfreq(FFT_SIZE, d=1.0 / RATE)
        self.bass_mask = (self.frequencies >= 40) & (self.frequencies < 250)

        frames_per_30s = int(30 * RATE / CHUNK)
        self.rms_history: Deque[float] = collections.deque(maxlen=frames_per_30s)
        self.flux_history: Deque[float] = collections.deque(maxlen=frames_per_30s)

        self.second_rms = []
        self.second_flux = []
        self.second_onsets = 0
        self.last_state_update = time.monotonic()
        self.last_accepted_beat = 0.0

        self.candidate_signature = None
        self.candidate_count = 0

    def process(self, chunk: np.ndarray, now: float) -> bool:
        padded = np.zeros(FFT_SIZE, dtype=np.float32)
        padded[:len(chunk)] = chunk
        spectrum = np.abs(np.fft.rfft(padded * self.window)).astype(np.float32)
        spectrum /= max(float(np.sum(spectrum)), 1e-9)

        positive_change = np.maximum(spectrum - self.previous_spectrum, 0.0)
        flux = float(np.sqrt(np.sum(positive_change * positive_change)))
        self.previous_spectrum = spectrum

        rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))
        rms_db = 20.0 * math.log10(max(rms, 1e-6))
        peak = float(np.max(np.abs(chunk)))

        with state.lock:
            state.rms_db = rms_db
            state.peak_amplitude = peak

        if len(self.flux_history) >= 20:
            recent = np.asarray(list(self.flux_history)[-120:], dtype=np.float32)
            onset = flux > max(float(np.median(recent) + 1.8 * np.std(recent)), 1e-4) and rms_db > -55.0
        else:
            onset = False

        self.rms_history.append(rms_db)
        self.flux_history.append(flux)

        self.second_rms.append(rms_db)
        self.second_flux.append(flux)
        if onset: self.second_onsets += 1

        # Tempo Update
        detected = bool(self.tempo(np.ascontiguousarray(chunk, dtype=np.float32))[0])
        beat = False
        if detected and now - self.last_accepted_beat >= 0.18:
            raw_bpm = float(self.tempo.get_bpm())
            if 45.0 <= raw_bpm <= 210.0:
                self.last_accepted_beat = now
                beat = True
                with state.lock:
                    state.raw_bpm = raw_bpm
                    state.bpm_history.append(raw_bpm)
                    state.bpm = float(np.median(state.bpm_history))
                    state.last_beat_time = now

        # Music State Update (Every 1 second)
        if now - self.last_state_update >= STATE_UPDATE_SECONDS and self.second_rms:
            elapsed = max(now - self.last_state_update, 1e-3)
            self.last_state_update = now

            mean_rms = float(np.mean(self.second_rms))
            mean_flux = float(np.mean(self.second_flux))
            onset_rate = self.second_onsets / elapsed
            self.second_rms.clear();
            self.second_flux.clear();
            self.second_onsets = 0

            energy_score = robust_normalize(mean_rms, self.rms_history)
            flux_score = robust_normalize(mean_flux, self.flux_history)
            activity_score = 0.70 * flux_score + 0.30 * float(np.clip(onset_rate / 5.0, 0.0, 1.0))

            with state.lock:
                rhythm = "UNKNOWN" if state.bpm <= 0 else (
                    "MEDIUM" if state.bpm < 128 else "FAST") if state.bpm >= 88 else (
                    "MEDIUM" if activity_score >= 0.78 else "SLOW")
                energy = "HIGH" if energy_score >= 0.70 else "LOW" if energy_score <= 0.32 else "MEDIUM"
                activity = "BUSY" if activity_score >= 0.70 else "SMOOTH" if activity_score <= 0.32 else "MODERATE"

                signature = (rhythm, energy, activity)
                if signature == self.candidate_signature:
                    self.candidate_count += 1
                else:
                    self.candidate_signature, self.candidate_count = signature, 1

                if self.candidate_count >= 3:
                    state.rhythm_speed, state.energy_level, state.activity_level = rhythm, energy, activity

                if now - state.last_beat_time > NO_BEAT_IDLE_SECONDS:
                    state.mood = "IDLE"
                elif state.energy_level == "HIGH" and state.activity_level == "BUSY":
                    state.mood = "AGGRESSIVE"
                elif state.energy_level in ("MEDIUM", "HIGH"):
                    state.mood = "ENERGY"
                else:
                    state.mood = "CHILL"

        return beat


# ---------------------------------------------------------------------------
# READY-driven choreography
# ---------------------------------------------------------------------------
DANCE_POOLS = {
    ("SLOW", "LOW", "SMOOTH"): ["DANCE_CHASSIS_BREATHE", "DANCE_BEG_WAVE", "DANCE_WAVE", "DANCE_PEACOCK"],
    ("SLOW", "HIGH", "BUSY"): ["DANCE_HEADBANG", "DANCE_PITCH_PIVOT", "DANCE_PEACOCK", "DANCE_RIPPLE"],
    ("FAST", "LOW", "SMOOTH"): ["DANCE_TWIST", "DANCE_CIRCLE", "DANCE_RIPPLE", "DANCE_WAVE"],
    ("FAST", "HIGH", "SMOOTH"): ["DANCE_CIRCLE", "DANCE_SALSA", "DANCE_ROLL_FAST", "DANCE_PITCH_PIVOT"],
    ("FAST", "HIGH", "BUSY"): ["DANCE_GALLOP", "DANCE_TWITCH", "DANCE_STROBE", "DANCE_PULSE", "DANCE_WORM"],
}

SAFE_DANCES = ["DANCE_TWIST", "DANCE_RIPPLE", "DANCE_CIRCLE", "DANCE_WAVE", "DANCE_PITCH_PIVOT"]


def choose_dance(rhythm: str, energy: str, activity: str) -> str:
    exact = DANCE_POOLS.get((rhythm, energy, activity))
    if exact: return random.choice(exact)
    if energy == "LOW": return random.choice(["DANCE_WAVE", "DANCE_BEG_WAVE", "DANCE_CHASSIS_BREATHE"])
    if activity == "BUSY": return random.choice(["DANCE_RIPPLE", "DANCE_HEADBANG", "DANCE_PULSE"])
    if rhythm == "FAST": return random.choice(["DANCE_SALSA", "DANCE_TWIST", "DANCE_CIRCLE"])
    return random.choice(SAFE_DANCES)


def update_dance_plan():
    """Keep one next movement ready while the current movement is running."""
    if state.operating_mode != "AUTO":
        return

    with state.lock:
        if state.voice_active or time.monotonic() < state.voice_override_until:
            return

        signature = (state.rhythm_speed, state.energy_level, state.activity_level)
        if state.planned_move is not None and signature == state.last_plan_signature:
            return

        planned = choose_dance(state.rhythm_speed, state.energy_level, state.activity_level)
        if planned == state.current_move:
            alternatives = [move for move in SAFE_DANCES if move != state.current_move]
            if alternatives: planned = random.choice(alternatives)

        state.planned_move = planned
        state.last_plan_signature = signature

    if state.show_audio_logs:
        log_event(f"🧠 [PLANNED] {planned} | {signature[0]}/{signature[1]}/{signature[2]}")


# ---------------------------------------------------------------------------
# Audio capture thread & Voice Trigger
# ---------------------------------------------------------------------------
def audio_listener():
    analyzer = AdaptiveMusicAnalyzer()
    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.3)
    syllables = []

    try:
        if state.audio_source == "BT":
            speaker = sc.default_speaker()
            microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            log_event(f"🎧 Analysing loopback audio: {speaker.name}")
        else:
            microphone = sc.default_microphone()
            log_event(f"🎙️ Analysing microphone: {microphone.name}")

        last_log = 0.0
        with microphone.recorder(samplerate=RATE, channels=1) as recorder:
            while True:
                chunk = recorder.record(numframes=CHUNK).reshape(-1).astype(np.float32)
                if len(chunk) != CHUNK: continue
                chunk = np.nan_to_num(chunk, copy=False)
                chunk = np.clip(chunk, -1.0, 1.0)
                audio_ring.append(chunk)

                now = time.monotonic()
                analyzer.process(chunk, now)
                update_dance_plan()

                # Syllable Counting / Auto Voice Trigger (Restored from Original Code)
                if aubio_syllable(butter_bandpass_filter(chunk))[0]: syllables.append(now)
                syllables = [t for t in syllables if now - t <= 3.0]

                with state.lock:
                    state.syllable_count = len(syllables)
                    va = state.voice_active
                    override = now < state.voice_override_until

                # Trigger Google Speech if > 8 syllables detected
                if len(syllables) > 8 and not va and not override:
                    with state.lock: state.voice_active = True
                    audio_bytes = (np.clip(audio_ring.snapshot(RATE * 4), -1, 1) * 32767).astype(np.int16).tobytes()
                    threading.Thread(target=process_voice_command, args=(audio_bytes,), daemon=True).start()
                    syllables.clear()

                if state.show_audio_logs and now - last_log >= 1.0:
                    last_log = now
                    with state.lock:
                        log_event(
                            f"🎵 BPM={state.bpm:5.1f} | Syl/3s={state.syllable_count} | "
                            f"Mood={state.mood} | Ctx={state.audio_context[:18]}"
                        )
    except Exception as exc:
        log_event(f"❌ Audio listener stopped: {exc}")
        while True: time.sleep(1)


# ---------------------------------------------------------------------------
# Optional YAMNet & Voice Commands (Restored)
# ---------------------------------------------------------------------------
yamnet_model = None
yamnet_classes = []


def yamnet_context_thread():
    global yamnet_model, yamnet_classes
    if not ENABLE_YAMNET or tf is None or hub is None:
        log_event("ℹ️ YAMNet disabled or unavailable; DSP dancing remains active.")
        return
    log_event("⏳ [AI] Loading optional YAMNet context model...")
    try:
        yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
        class_path = yamnet_model.class_map_path().numpy().decode("utf-8")
        with tf.io.gfile.GFile(class_path) as file:
            yamnet_classes = [row["display_name"] for row in csv.DictReader(file)]
        log_event("✅ [AI] YAMNet loaded.")
    except Exception as exc:
        log_event(f"❌ [AI] YAMNet unavailable: {exc}")
        return

    while True:
        time.sleep(5)
        try:
            waveform = audio_ring.snapshot(RATE * 3)
            if len(waveform) < RATE: continue
            scores, _, _ = yamnet_model(waveform)
            means = np.mean(scores.numpy(), axis=0)

            with state.lock:
                if "CMD" not in state.genre:
                    state.audio_context = yamnet_classes[int(np.argmax(means))]
        except Exception:
            time.sleep(1)


recognizer = sr.Recognizer() if sr else None


def say_phrase_offline(text: str):
    if pyttsx3 is None: return

    def speak():
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 145)
            engine.say(text)
            engine.runAndWait()
        except Exception:
            pass

    threading.Thread(target=speak, daemon=True).start()


def process_voice_command(audio_bytes: bytes):
    if recognizer is None:
        with state.lock: state.voice_active = False
        return
    try:
        text = recognizer.recognize_google(sr.AudioData(audio_bytes, RATE, 2), language="en-US").lower()
        log_event(f"🎤 [VOICE] Recognized: '{text}'")
        command, phrase = None, None

        if "stop" in text or "stand" in text:
            command, phrase = "STAND", "stopping"
        elif "forward" in text:
            command, phrase = "WALK_FORWARD", "walking forward"
        elif "back" in text:
            command, phrase = "WALK_BACKWARD", "walking backward"
        elif "dance" in text or "party" in text:
            command, phrase = "DANCE_CIRCLE", "party mode"
        elif "slow" in text or "relax" in text:
            command, phrase = "DANCE_CHASSIS_BREATHE", "slow mode"
        elif "fast" in text or "speed" in text:
            command, phrase = "DANCE_ROLL_FAST", "high speed"

        if command:
            send_to_esp32(command)
            say_phrase_offline(phrase)
            with state.lock:
                state.command_detected_time = time.time()
                state.voice_override_until = time.monotonic() + 15.0
    except Exception:
        pass
    finally:
        with state.lock:
            state.voice_active = False


# ---------------------------------------------------------------------------
# LED Engine (Restored with all 16 Patterns)
# ---------------------------------------------------------------------------
strip = PixelStrip(NUM_LEDS, LED_PIN, 800_000, 10, False, LED_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB)
strip.begin()


def hsv(hue, sat=255, val=255):
    red, green, blue = colorsys.hsv_to_rgb((hue % 256) / 256.0, sat / 255.0, val / 255.0)
    return Color(int(red * 255), int(green * 255), int(blue * 255))


def beatsin(bpm, low, high, phase=0):
    return int(low + ((math.sin(time.monotonic() * bpm * 2 * math.pi / 60 + phase) + 1) / 2) * (high - low))


def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255.0
    for i in range(NUM_LEDS):
        c = strip.getPixelColor(i)
        strip.setPixelColor(i, Color(int(((c >> 16) & 0xFF) * scale), int(((c >> 8) & 0xFF) * scale),
                                     int((c & 0xFF) * scale)))


def render_fire(heat):
    for i in range(NUM_LEDS): heat[i] = max(0, heat[i] - random.randrange(10, 35))
    for i in range(NUM_LEDS - 1, 1, -1): heat[i] = (heat[i - 1] + heat[i - 2] * 2) // 3
    if random.randrange(256) < 130:
        spot = random.randrange(min(2, NUM_LEDS))
        heat[spot] = min(255, heat[spot] + random.randrange(160, 256))
    for i, temperature in enumerate(heat):
        ramp = (temperature & 0x3F) << 2
        colour = Color(255, 255, ramp) if temperature > 0x80 else Color(255, ramp, 0) if temperature > 0x40 else Color(
            ramp, 0, 0)
        strip.setPixelColor(i, colour)


def render_manual_led(pattern, frame, heat):
    if pattern == "rainbow":
        for i in range(NUM_LEDS): strip.setPixelColor(i, hsv((frame * 5 + i * 18) % 256))
    elif pattern == "confetti":
        fade_to_black_by(25)
        if random.random() < 0.3: strip.setPixelColor(random.randrange(NUM_LEDS), hsv(random.randrange(256)))
    elif pattern == "sinelon":
        fade_to_black_by(35);
        strip.setPixelColor(beatsin(18, 0, NUM_LEDS - 1), hsv((frame * 8) % 256))
    elif pattern == "bpm":
        value = beatsin(90, 80, 255)
        for i in range(NUM_LEDS): strip.setPixelColor(i, hsv((i * 24 + frame * 3) % 256, 255, value))
    elif pattern == "juggle":
        fade_to_black_by(40)
        for d in range(4): strip.setPixelColor(beatsin(d + 8, 0, NUM_LEDS - 1, d * 0.6), hsv(d * 64))
    elif pattern == "fire":
        render_fire(heat)
    elif pattern == "color_wipe":
        colors = [Color(255, 0, 0), Color(0, 255, 0), Color(0, 0, 255), Color(255, 100, 0)]
        strip.setPixelColor((frame // 4) % NUM_LEDS, colors[(frame // (NUM_LEDS * 4)) % 4])
    elif pattern == "theater_chase":
        for i in range(NUM_LEDS): strip.setPixelColor(i, hsv((frame * 5 + i * 20) % 256) if (i + (
                    frame // 3)) % 3 == 0 else Color(0, 0, 0))
    elif pattern == "comet":
        fade_to_black_by(50)
        pos = frame % (NUM_LEDS * 2 - 2)
        strip.setPixelColor(NUM_LEDS * 2 - 2 - pos if pos >= NUM_LEDS else pos, hsv((frame * 5) % 256))
    elif pattern == "dual_scanner":
        fade_to_black_by(65)
        pos = frame % (NUM_LEDS * 2 - 2)
        pos = NUM_LEDS * 2 - 2 - pos if pos >= NUM_LEDS else pos
        strip.setPixelColor(pos, Color(255, 20, 0))
        strip.setPixelColor(NUM_LEDS - 1 - pos, Color(0, 60, 255))
    elif pattern == "breathing":
        c_val = int(20 + ((math.sin(frame * 0.05) + 1) / 2) * 100)
        for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, c_val, int(c_val * 2.5)))
    elif pattern == "sparkle_burst":
        if frame % 40 == 0:
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, 0, 0))
            for _ in range(random.randint(2, NUM_LEDS)): strip.setPixelColor(random.randint(0, NUM_LEDS - 1),
                                                                             hsv(random.randint(0, 255)))
        else:
            fade_to_black_by(30)
    elif pattern == "strobe":
        for i in range(NUM_LEDS): strip.setPixelColor(i, hsv((frame * 11) % 256, 100, 255) if (
                                                                                                          frame // 3) % 2 == 0 else Color(
            0, 0, 0))
    elif pattern == "wave":
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, hsv((frame * 2 + i * 16) % 256, 230,
                                       int(25 + ((math.sin(frame * 0.18 - i * 0.9) + 1) / 2) * 230)))
    elif pattern == "alternating":
        for i in range(NUM_LEDS): strip.setPixelColor(i, Color(255, 0, 80) if (i + (frame // 10)) % 2 == 0 else Color(0,
                                                                                                                      180,
                                                                                                                      255))
    elif pattern == "random_palette":
        if frame % 100 == 0 or not hasattr(state, 'rand_pal'): state.rand_pal = [hsv(random.randint(0, 255)) for _ in
                                                                                 range(4)]
        for i in range(NUM_LEDS): strip.setPixelColor(i, state.rand_pal[i % 4])


def led_thread():
    frame = 0;
    heat = [0] * NUM_LEDS
    while True:
        try:
            with state.lock:
                mood, voice_active, command_time = state.mood, state.voice_active, state.command_detected_time
                manual, bpm, beat_active = state.manual_led_pattern, state.bpm, time.monotonic() - state.last_beat_time < 0.15
            elapsed = time.time() - command_time
            frame += 1

            if elapsed < 0.25:
                for i in range(NUM_LEDS): strip.setPixelColor(i, Color(255, 255, 255))
            elif elapsed < 1.0:
                for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, 50, 255))
            elif voice_active:
                strip.setPixelColor(0, Color(0, 0, 0));
                fade_to_black_by(60);
                pos = frame % (NUM_LEDS * 2 - 2)
                strip.setPixelColor(NUM_LEDS * 2 - 2 - pos if pos >= NUM_LEDS else pos, Color(0, 255, 50))
            elif manual:
                render_manual_led(manual, frame, heat)
            elif mood == "AGGRESSIVE":
                if beat_active:
                    for i in range(NUM_LEDS): strip.setPixelColor(i, Color(255, 255, 255))
                else:
                    render_fire(heat)
            elif mood == "ENERGY":
                fade_to_black_by(40);
                strip.setPixelColor(beatsin(bpm if bpm > 0 else 120, 0, NUM_LEDS - 1),
                                    Color(255, 255, 255) if beat_active else hsv(int(time.monotonic() * 50) % 256))
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


# ---------------------------------------------------------------------------
# LCD display (Restored Eye UI)
# ---------------------------------------------------------------------------
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(spi, cs=digitalio.DigitalInOut(DISPLAY_CS_PIN), dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
                           rst=digitalio.DigitalInOut(DISPLAY_RST_PIN), rotation=90, baudrate=24_000_000)


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
    try:
        display = init_display()
    except Exception:
        return

    width, height, eye_w, eye_h, lx, rx, cy = 320, 240, 70, 120, 90, 230, 120
    blink_timer, blink_interval, is_blinking = time.time(), random.uniform(2.0, 5.0), False

    while True:
        try:
            with state.lock:
                effective_mood = state.manual_mood if state.manual_mood is not None else state.mood
                mood, va, cmd_t, bpm, syl, roll = effective_mood, state.voice_active, state.command_detected_time, state.bpm, state.syllable_count, state.body_roll
                beat_active = time.monotonic() - state.last_beat_time < 0.15

            dt = time.time() - cmd_t
            bg = (255, 255, 255) if dt < 0.25 else (30, 30, 80) if dt < 1.0 else (10, 35, 15) if va else (0, 0, 0)
            img = Image.new("RGB", (width, height), color=bg)
            draw = ImageDraw.Draw(img)

            draw.text((5, 5), f"BPM: {bpm:.0f} | Syl: {syl}/3s | Mood: {mood}", fill=(100, 100, 100))

            h, col, cy_r = eye_h, (0, 255, 255), cy
            if dt < 0.25:
                col, h, cy_r = (0, 0, 0), int(eye_h * 0.4), cy - 10
            elif dt < 1.0:
                col, h, cy_r = (0, 191, 255), int(eye_h * 0.4), cy - 10
            elif va or mood == "VOICE_ACTIVE":
                col, h = (0, 255, 100), int(eye_h * 0.75)
            elif mood == "AGGRESSIVE":
                col, h = (255, 50, 50), eye_h + 20
            elif mood == "ENERGY":
                col, h = (255, 150, 50), eye_h + 10
            elif mood == "CHILL":
                col, h = (150, 50, 255), int(eye_h * 0.6)
            elif mood == "HAPPY":
                col, h = (255, 220, 0), eye_h + 15
            elif mood == "CONFUSED":
                col, h = (255, 105, 180), eye_h

            h_l, h_r = (h - 30, h + 20) if mood == "CONFUSED" else (h, h)

            ew = eye_w + 10 if (beat_active and not va and dt > 1.0) else eye_w
            if time.time() - blink_timer > blink_interval: is_blinking, blink_timer, blink_interval = True, time.time(), random.uniform(
                2.0, 5.0)
            if is_blinking and not va and dt > 1.0: h_l, h_r = 10, 10; is_blinking = time.time() - blink_timer <= 0.15

            roll_offset = int(roll * 1.5)
            draw_rounded_rect(draw,
                              [lx - ew // 2, cy_r + roll_offset - h_l // 2, lx + ew // 2, cy_r + roll_offset + h_l // 2],
                              20, col)
            draw_rounded_rect(draw,
                              [rx - ew // 2, cy_r - roll_offset - h_r // 2, rx + ew // 2, cy_r - roll_offset + h_r // 2],
                              20, col)
            display.image(img);
            time.sleep(0.03)
        except Exception:
            time.sleep(1)


# ---------------------------------------------------------------------------
# Manual controls (Restored CLI Menu)
# ---------------------------------------------------------------------------
CLI_COMMANDS = {
    11: ("WALK_FORWARD", "Walk Fwd"), 12: ("WALK_BACKWARD", "Walk Back"),
    13: ("TURN_LEFT", "Turn L"), 14: ("TURN_RIGHT", "Turn R"),
    15: ("STAND", "Stand"), 16: ("RELAX", "Deactivate"),
    21: ("DANCE_WAVE", "Wave"), 22: ("DANCE_RIPPLE", "Ripple"), 23: ("DANCE_RIPPLE_2", "Ripple 2"),
    24: ("DANCE_PEACOCK", "Peacock"), 25: ("DANCE_SALSA", "Salsa"), 26: ("DANCE_TWIST", "Twist"),
    27: ("DANCE_TWIST_2", "Twist 2"),
    28: ("DANCE_ROLL", "Roll"), 29: ("DANCE_ROLL_2", "Roll 2"), 30: ("DANCE_ROLL_FAST", "Fast Roll"),
    31: ("DANCE_ROLL_SLOW", "Slow Roll"),
    32: ("DANCE_CIRCLE", "Circle"), 33: ("DANCE_CIRCLE_2", "Circle 2"), 34: ("DANCE_CRAWL", "Crawl"),
    35: ("DANCE_HEADBANG", "Headbang"), 36: ("DANCE_STROBE", "Strobe"),
    37: ("DANCE_PULSE", "Pulse"), 38: ("DANCE_GALLOP", "Gallop"),
    39: ("DANCE_BEG_WAVE", "Beg Wave"), 40: ("DANCE_CHASSIS_BREATHE", "Breathe"),
    41: ("DANCE_BELLY_CRAWL", "Belly Crawl"), 42: ("DANCE_PITCH_PIVOT", "Pitch Pivot"),
    43: ("DANCE_TWITCH", "Twitch"), 44: ("DANCE_WORM", "Worm"),
    70: ("TEST_LEG_0", "Test Leg 0"), 71: ("TEST_LEG_1", "Test Leg 1"), 72: ("TEST_LEG_2", "Test Leg 2"),
    73: ("TEST_LEG_3", "Test Leg 3"), 74: ("TEST_LEG_4", "Test Leg 4"), 75: ("TEST_LEG_5", "Test Leg 5"),
}

LED_PATTERNS = {
    51: "rainbow", 52: "confetti", 53: "sinelon", 54: "bpm",
    55: "juggle", 56: "fire", 57: "color_wipe", 58: "theater_chase",
    59: "comet", 60: "dual_scanner", 61: "breathing", 62: "sparkle_burst",
    63: "strobe", 64: "wave", 65: "alternating", 66: "random_palette",
}

SCREEN_EMOTIONS = {
    80: ("IDLE", "Cyan (Normal/Idle)"),
    81: ("AGGRESSIVE", "Red (Aggressive/Angry)"),
    82: ("ENERGY", "Orange (Energetic/Hyped)"),
    83: ("CHILL", "Purple (Chill/Relaxed)"),
    84: ("VOICE_ACTIVE", "Green (Listening/Voice)"),
    85: ("HAPPY", "Yellow (Happy/Excited)"),
    86: ("CONFUSED", "Pink (Confused/Asymmetric)"),
}


def run_emotion_screen_test():
    """Cycles through all LCD screen emotion states with CLI status output."""
    print("\n==================================================")
    print("📺 STARTING AUTOMATED SCREEN EMOTION TEST CYCLE")
    print("==================================================")
    for num, (mood, desc) in SCREEN_EMOTIONS.items():
        with state.lock:
            state.manual_mood = mood
        print(f"  [Option {num}] Displaying Emotion: {mood:12s} ({desc})")
        time.sleep(2.5)

    with state.lock:
        state.manual_mood = None
    print("--------------------------------------------------")
    print("✅ Screen Emotion Test Complete! Reset to AUTO.\n")


def print_mic_readings():
    """Prints a single real-time snapshot of microphone readings to the CLI."""
    with state.lock:
        rms_db = state.rms_db
        peak = state.peak_amplitude
        bpm = state.bpm
        syl = state.syllable_count
        mood = state.mood
        energy = state.energy_level
        activity = state.activity_level
        ctx = state.audio_context

    # Visual VU meter bar based on RMS dB (-60 dB to 0 dB)
    normalized_val = max(0, min(30, int((rms_db + 60) / 2)))
    vu_bar = "[" + "#" * normalized_val + "-" * (30 - normalized_val) + "]"

    print("\n--------------------------------------------------")
    print("🎙️ LIVE MICROPHONE READINGS SNAPSHOT")
    print("--------------------------------------------------")
    print(f"  Volume (RMS) : {rms_db:6.1f} dB  {vu_bar}")
    print(f"  Peak Signal  : {peak:6.3f}")
    print(f"  Estimated BPM: {bpm:5.1f} BPM")
    print(f"  Speech/Syll  : {syl} syllables / 3s")
    print(f"  Energy Level : {energy} | Activity: {activity} | Mood: {mood}")
    print(f"  Audio Context: {ctx}")
    print("--------------------------------------------------\n")


def stream_mic_readings(duration_sec: float = 10.0):
    """Streams live microphone VU meter and telemetry directly in the CLI for N seconds."""
    print("\n🎙️ Streaming Live Microphone Readings (Press Ctrl+C to stop early)...")
    end_time = time.time() + duration_sec
    try:
        while time.time() < end_time:
            with state.lock:
                rms_db = state.rms_db
                peak = state.peak_amplitude
                bpm = state.bpm
                syl = state.syllable_count
                mood = state.mood

            normalized_val = max(0, min(20, int((rms_db + 60) / 3)))
            vu_bar = "#" * normalized_val + "-" * (20 - normalized_val)
            sys.stdout.write(
                f"\r🎙️ [{vu_bar}] RMS:{rms_db:5.1f}dB | Peak:{peak:.2f} | BPM:{bpm:5.1f} | Syl:{syl} | Mood:{mood}  "
            )
            sys.stdout.flush()
            time.sleep(0.1)
        print("\n✅ Stream completed.\n")
    except KeyboardInterrupt:
        print("\n🛑 Stream stopped.\n")


def print_menu():
    print("""
======================================================================
                     🤖 HEXAPOD GOD-MODE CLI 🤖
======================================================================
 --- MOVEMENTS (11-16) ---
  [11] Walk Fwd      [12] Walk Back     [13] Turn L
  [14] Turn Right    [15] STAND (Stop)  [16] RELAX (Safety)

 --- DANCES (21-44) ---
  [21] Wave          [22] Ripple        [23] Ripple 2
  [24] Peacock       [25] Salsa         [26] Twist
  [27] Twist 2       [28] Roll          [29] Roll 2
  [30] Fast Roll     [31] Slow Roll     [32] Circle
  [33] Circle 2      [34] Crawl         [35] Headbang
  [36] Strobe        [37] Pulse         [38] Gallop
  [39] Beg Wave      [40] Breathe       [41] Belly Crawl
  [42] Pitch Pivot   [43] Twitch        [44] Worm

 --- 16 LED PATTERN OVERRIDES (51-66) ---
  [51] Rainbow       [52] Confetti      [53] Sinelon
  [54] BPM           [55] Juggle        [56] Fire
  [57] Color Wipe    [58] Theater Chase [59] Comet
  [60] Dual Scanner  [61] Breathing     [62] Sparkle Burst
  [63] Strobe        [64] Wave          [65] Alternating
  [66] Random Palette
  [69] RETURN LEDS TO AUTO MOOD SYNC

 --- LCD SCREEN EMOTION OVERRIDES (80-89) ---
  [80] Normal (Cyan)  [81] Aggressive (Red) [82] Energy (Orange)
  [83] Chill (Purple) [84] Voice (Green)    [85] Happy (Yellow)
  [86] Confused (Pink)
  [88] RUN AUTOMATED SCREEN EMOTION TEST CYCLE (2.5s each)
  [89] RETURN DISPLAY TO AUTO MOOD SYNC

 --- DIAGNOSTICS & SYSTEM ---
  [70] to [75] Test Individual Legs 0 through 5
  [91] Toggle Telemetry Logging      [92] Print Mic Reading Snapshot
  [93] Live Mic VU Stream (10s)      [ 0] EXIT PROGRAM
======================================================================
""")


def manual_testing_loop():
    print_menu()
    while True:
        try:
            choice = input("\nEnter command number (or 'm' for menu) >>> ").strip().lower()
            if choice in ("0", "q"): os._exit(0)
            if choice == "m": print_menu(); continue
            if not choice.isdigit(): print("Invalid input."); continue

            number = int(choice)
            if number in CLI_COMMANDS:
                send_to_esp32(CLI_COMMANDS[number][0])
                with state.lock:
                    state.command_detected_time = time.time()
            elif number in LED_PATTERNS:
                with state.lock:
                    state.manual_led_pattern = LED_PATTERNS[number]
                print(f"✨ LED Pattern Overridden to: {LED_PATTERNS[number]}")
            elif number == 69:
                with state.lock:
                    state.manual_led_pattern = None
                print("🎵 LEDs returned to AUTO MOOD SYNC mode.")
            elif number in SCREEN_EMOTIONS:
                mood_key, desc = SCREEN_EMOTIONS[number]
                with state.lock:
                    state.manual_mood = mood_key
                print(f"📺 Display Emotion Overridden to: {mood_key} ({desc})")
            elif number == 88:
                run_emotion_screen_test()
            elif number == 89:
                with state.lock:
                    state.manual_mood = None
                print("📺 LCD Screen returned to AUTO MOOD SYNC mode.")
            elif number == 91:
                with state.lock:
                    state.show_audio_logs = not state.show_audio_logs
                print(f"📁 Background Audio Logging: {'ON' if state.show_audio_logs else 'OFF'} (Check hexabot.log)")
            elif number == 92:
                print_mic_readings()
            elif number == 93:
                stream_mic_readings(duration_sec=10.0)
            else:
                print("Invalid command.")
        except KeyboardInterrupt:
            os._exit(0)


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def start_background_threads():
    for target in (esp32_reader_thread, yamnet_context_thread, audio_listener, led_thread, display_loop):
        threading.Thread(target=target, daemon=True, name=target.__name__).start()


if __name__ == "__main__":
    print("\n" + "=" * 54)
    print("       🤖 CODEGENIX HEXABOT OS - ADAPTIVE MUSIC 🤖")
    print("=" * 54)
    print(" [1] AUTO MODE - adaptive unknown-song dancing")
    print(" [2] MANUAL MODE - CLI control")
    try:
        mode = input(">>> ").strip()
    except KeyboardInterrupt:
        raise SystemExit(0)

    if mode == "1":
        state.operating_mode = "AUTO"
        state.show_audio_logs = True
        print("\n [1] Physical microphone")
        print(" [2] Internal Bluetooth/YouTube/Spotify loopback")
        source = input(">>> ").strip()
        state.audio_source = "BT" if source == "2" else "MIC"
    else:
        state.operating_mode = "MANUAL"
        state.audio_source = "MIC"

    os.system("amixer set Master 100% > /dev/null 2>&1")
    start_background_threads()

    if state.operating_mode == "AUTO":
        print("\n✅ Adaptive auto mode running. Press Ctrl+C to exit.")
        print(f"📁 Live Telemetry is being written to: {log_file_path}")
        try:
            while True: time.sleep(1)
        except KeyboardInterrupt:
            send_to_esp32("STAND")
    else:
        time.sleep(1)
        print(f"\n📁 Live Debug logs are being written to: {log_file_path}")
        manual_testing_loop()