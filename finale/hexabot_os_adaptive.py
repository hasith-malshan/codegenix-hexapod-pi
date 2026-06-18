#!/usr/bin/env python3
"""Hexabot OS - adaptive real-time music dancer for Raspberry Pi 4.

The audio engine is intentionally lightweight.  It measures three independent
properties of unknown music:

* tempo / beat confidence  -> movement timing
* adaptive energy          -> movement amplitude / intensity
* spectral activity        -> smooth versus busy movement style

YAMNet is optional and is used only for broad audio context.  It is never used
as a tempo, genre, mood, or vocal-speed detector.
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
from dataclasses import dataclass
from typing import Deque, Optional


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

import aubio
import numpy as np
import serial
import soundcard as sc

import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341
from rpi_ws281x import Color, PixelStrip, ws

# Optional features: the robot still dances if any of these are unavailable.
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
CHUNK = 512                   # 32 ms at 16 kHz
FFT_SIZE = 2048
TEMPO_WINDOW = 2048
STATE_UPDATE_SECONDS = 1.0
NO_BEAT_IDLE_SECONDS = 4.0

# No C++ update is required for this first listening movement.  The current
# implementation lasts roughly five seconds, during which the Pi analyses and
# plans the next dance.
INITIAL_LISTEN_MOVE = "DANCE_CHASSIS_BREATHE"

# Automatic speech triggering is deliberately disabled: music onsets are not
# a wake word.  Voice commands can be added later using a button/wake-word.
ENABLE_AUTO_VOICE_TRIGGER = False
ENABLE_YAMNET = True

DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25

LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
LED_BRIGHTNESS = 100


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
        self.beat_counter = 0
        self.beats_since_dance = 0

        self.energy_score = 0.0
        self.activity_score = 0.0
        self.onsets_per_second = 0.0
        self.bass_ratio = 0.0

        self.rhythm_speed = "UNKNOWN"   # SLOW / MEDIUM / FAST
        self.energy_level = "LOW"       # LOW / MEDIUM / HIGH
        self.activity_level = "SMOOTH"  # SMOOTH / MODERATE / BUSY
        self.mood = "IDLE"              # UI/legacy summary only

        self.audio_context = "Listening..."
        self.music_probability = 0.0
        self.speech_probability = 0.0

        self.voice_active = False
        self.command_detected_time = 0.0
        self.voice_override_until = 0.0

        self.body_roll = 0.0
        self.manual_led_pattern = None
        self.last_dance_command = "STAND"
        self.started_at = time.monotonic()

        # READY-driven choreography.  Only the serial-reader thread dispatches
        # automatic dances, so the Pi never piles commands into the ESP32's
        # serial buffer while a blocking Arduino dance is still running.
        self.robot_ready = False
        self.initial_listen_sent = False
        self.current_move = None
        self.planned_move = None
        self.last_plan_signature = None

        self.bpm_history: Deque[float] = collections.deque(maxlen=32)
        self.lock = threading.RLock()


state = RobotState()


# ---------------------------------------------------------------------------
# USB serial / ESP32
# ---------------------------------------------------------------------------
def connect_to_esp32():
    print("\n🔌 Searching for ESP32 via USB...")
    for port in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0"):
        try:
            connection = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Connected to ESP32 on {port}")
            return connection
        except Exception:
            continue
    print("❌ ESP32 not found. Commands will be simulated.")
    return None


esp32_serial = connect_to_esp32()


def esp32_reader_thread():
    while True:
        if not (esp32_serial and esp32_serial.is_open):
            time.sleep(0.1)
            continue
        try:
            line = esp32_serial.readline().decode("utf-8", errors="ignore").strip()
            if line == "READY":
                handle_robot_ready()
            elif line.startswith("TILT:"):
                roll = float(line.split(":", 1)[1])
                if math.isfinite(roll):
                    with state.lock:
                        state.body_roll = roll
        except (ValueError, OSError, serial.SerialException):
            time.sleep(0.1)


def send_to_esp32(command: str):
    with state.lock:
        state.last_dance_command = command
    if not (esp32_serial and esp32_serial.is_open):
        if state.show_audio_logs:
            print(f"🤖 [SIMULATED] {command}")
        return
    try:
        esp32_serial.write((command + "\n").encode("utf-8"))
        esp32_serial.flush()
    except Exception as exc:
        print(f"❌ Serial write error: {exc}")


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
            # Analysis can occasionally be uncertain.  A safe movement avoids
            # a visible pause while preserving stability.
            command = choose_dance(
                state.rhythm_speed, state.energy_level, state.activity_level
            )

        state.current_move = command
        state.robot_ready = False

    send_to_esp32(command)
    if state.show_audio_logs:
        print(f"▶️ [READY→DANCE] {command}")


# ---------------------------------------------------------------------------
# Lock-safe circular audio buffer for YAMNet and optional voice capture
# ---------------------------------------------------------------------------
class AudioRingBuffer:
    def __init__(self, samples: int):
        self.data = np.zeros(samples, dtype=np.float32)
        self.index = 0
        self.full = False
        self.lock = threading.Lock()

    def append(self, chunk: np.ndarray):
        chunk = np.asarray(chunk, dtype=np.float32)
        if len(chunk) >= len(self.data):
            chunk = chunk[-len(self.data):]
        with self.lock:
            end = self.index + len(chunk)
            if end <= len(self.data):
                self.data[self.index:end] = chunk
            else:
                first = len(self.data) - self.index
                self.data[self.index:] = chunk[:first]
                self.data[:end - len(self.data)] = chunk[first:]
            self.index = end % len(self.data)
            if end >= len(self.data):
                self.full = True

    def snapshot(self, sample_count: Optional[int] = None) -> np.ndarray:
        with self.lock:
            if self.full:
                ordered = np.concatenate((self.data[self.index:], self.data[:self.index]))
            else:
                ordered = self.data[:self.index].copy()
        if sample_count is not None:
            ordered = ordered[-sample_count:]
        return np.ascontiguousarray(ordered, dtype=np.float32)


audio_ring = AudioRingBuffer(RATE * 5)


# ---------------------------------------------------------------------------
# Adaptive music analyser
# ---------------------------------------------------------------------------
@dataclass
class FeatureFrame:
    rms_db: float
    flux: float
    bass_ratio: float
    onset: bool


def robust_normalize(value: float, history: Deque[float], default=0.5) -> float:
    """Map value to 0..1 using rolling 20th and 85th percentiles."""
    if len(history) < 30:
        return default
    values = np.asarray(history, dtype=np.float32)
    low, high = np.percentile(values, [20, 85])
    if high - low < 1e-6:
        return default
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
        self.bass_history: Deque[float] = collections.deque(maxlen=frames_per_30s)

        self.second_rms = []
        self.second_flux = []
        self.second_bass = []
        self.second_onsets = 0
        self.last_state_update = time.monotonic()
        self.last_accepted_beat = 0.0

        self.candidate_signature = None
        self.candidate_count = 0

    def spectral_features(self, chunk: np.ndarray) -> FeatureFrame:
        padded = np.zeros(FFT_SIZE, dtype=np.float32)
        padded[:len(chunk)] = chunk
        spectrum = np.abs(np.fft.rfft(padded * self.window)).astype(np.float32)
        spectrum /= max(float(np.sum(spectrum)), 1e-9)

        positive_change = np.maximum(spectrum - self.previous_spectrum, 0.0)
        flux = float(np.sqrt(np.sum(positive_change * positive_change)))
        self.previous_spectrum = spectrum

        bass_ratio = float(np.sum(spectrum[self.bass_mask]))
        rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-12))
        rms_db = 20.0 * math.log10(max(rms, 1e-6))

        # Adaptive transient threshold.  It is an overall musical onset, not a
        # syllable detector.
        if len(self.flux_history) >= 20:
            recent = np.asarray(list(self.flux_history)[-120:], dtype=np.float32)
            threshold = float(np.median(recent) + 1.8 * np.std(recent))
            onset = flux > max(threshold, 1e-4) and rms_db > -55.0
        else:
            onset = False

        self.rms_history.append(rms_db)
        self.flux_history.append(flux)
        self.bass_history.append(bass_ratio)
        return FeatureFrame(rms_db, flux, bass_ratio, onset)

    def update_tempo(self, chunk: np.ndarray, now: float) -> bool:
        detected = bool(self.tempo(np.ascontiguousarray(chunk, dtype=np.float32))[0])
        if not detected or now - self.last_accepted_beat < 0.18:
            return False

        raw_bpm = float(self.tempo.get_bpm())
        if not 45.0 <= raw_bpm <= 210.0:
            return False

        self.last_accepted_beat = now
        with state.lock:
            state.raw_bpm = raw_bpm
            state.bpm_history.append(raw_bpm)
            state.bpm = float(np.median(state.bpm_history))
            state.last_beat_time = now
            state.beat_counter += 1
            state.beats_since_dance += 1

            values = np.asarray(state.bpm_history, dtype=np.float32)
            if len(values) >= 4:
                median = float(np.median(values))
                mad = float(np.median(np.abs(values - median)))
                stability = float(np.clip(1.0 - (mad / max(median * 0.08, 1.0)), 0.0, 1.0))
                history_factor = min(1.0, len(values) / 10.0)
                try:
                    aubio_confidence = float(np.clip(self.tempo.get_confidence(), 0.0, 1.0))
                except Exception:
                    aubio_confidence = stability
                state.beat_confidence = 0.55 * stability + 0.25 * history_factor + 0.20 * aubio_confidence
            else:
                state.beat_confidence = len(values) / 10.0
        return True

    @staticmethod
    def classify_rhythm(bpm: float, activity: float) -> str:
        # Preserve the detected pulse.  Only treat a slow pulse as a likely
        # half-time fast rhythm when the sound is consistently very busy.
        if bpm <= 0:
            return "UNKNOWN"
        if bpm < 88:
            return "MEDIUM" if activity >= 0.78 else "SLOW"
        if bpm < 128:
            return "MEDIUM"
        return "FAST"

    @staticmethod
    def level_with_hysteresis(score: float, old: str, low_name: str,
                              medium_name: str, high_name: str) -> str:
        if old == high_name and score >= 0.60:
            return high_name
        if old == low_name and score <= 0.42:
            return low_name
        if score >= 0.70:
            return high_name
        if score <= 0.32:
            return low_name
        return medium_name

    def update_music_state(self, now: float):
        if now - self.last_state_update < STATE_UPDATE_SECONDS:
            return
        elapsed = max(now - self.last_state_update, 1e-3)
        self.last_state_update = now

        if not self.second_rms:
            return
        mean_rms = float(np.mean(self.second_rms))
        mean_flux = float(np.mean(self.second_flux))
        mean_bass = float(np.mean(self.second_bass))
        onset_rate = self.second_onsets / elapsed
        self.second_rms.clear()
        self.second_flux.clear()
        self.second_bass.clear()
        self.second_onsets = 0

        energy_score = robust_normalize(mean_rms, self.rms_history)
        flux_score = robust_normalize(mean_flux, self.flux_history)
        # Flux is the strongest activity signal; onset rate adds useful attack
        # density while remaining deliberately independent of vocals.
        onset_score = float(np.clip(onset_rate / 5.0, 0.0, 1.0))
        activity_score = 0.70 * flux_score + 0.30 * onset_score

        with state.lock:
            old_energy = state.energy_level
            old_activity = state.activity_level
            proposed_energy = self.level_with_hysteresis(
                energy_score, old_energy, "LOW", "MEDIUM", "HIGH"
            )
            proposed_activity = self.level_with_hysteresis(
                activity_score, old_activity, "SMOOTH", "MODERATE", "BUSY"
            )
            proposed_rhythm = self.classify_rhythm(state.bpm, activity_score)

            # Require three consecutive one-second estimates before changing
            # the qualitative state.  Numeric scores remain responsive.
            signature = (proposed_rhythm, proposed_energy, proposed_activity)
            if signature == self.candidate_signature:
                self.candidate_count += 1
            else:
                self.candidate_signature = signature
                self.candidate_count = 1

            state.energy_score = energy_score
            state.activity_score = activity_score
            state.onsets_per_second = onset_rate
            state.bass_ratio = mean_bass

            if self.candidate_count >= 3:
                state.rhythm_speed = proposed_rhythm
                state.energy_level = proposed_energy
                state.activity_level = proposed_activity

            if now - state.last_beat_time > NO_BEAT_IDLE_SECONDS:
                state.mood = "IDLE"
                state.beat_confidence *= 0.8
            elif state.energy_level == "HIGH" and state.activity_level == "BUSY":
                state.mood = "AGGRESSIVE"
            elif state.energy_level in ("MEDIUM", "HIGH"):
                state.mood = "ENERGY"
            else:
                state.mood = "CHILL"

    def process(self, chunk: np.ndarray, now: float) -> bool:
        features = self.spectral_features(chunk)
        self.second_rms.append(features.rms_db)
        self.second_flux.append(features.flux)
        self.second_bass.append(features.bass_ratio)
        if features.onset:
            self.second_onsets += 1
        beat = self.update_tempo(chunk, now)
        self.update_music_state(now)
        return beat


# ---------------------------------------------------------------------------
# READY-driven choreography and movement planning
# ---------------------------------------------------------------------------
DANCE_POOLS = {
    ("SLOW", "LOW", "SMOOTH"): [
        "DANCE_CHASSIS_BREATHE", "DANCE_BEG_WAVE", "DANCE_WAVE", "DANCE_PEACOCK"
    ],
    ("SLOW", "HIGH", "BUSY"): [
        "DANCE_HEADBANG", "DANCE_PITCH_PIVOT", "DANCE_PEACOCK", "DANCE_RIPPLE"
    ],
    ("FAST", "LOW", "SMOOTH"): [
        "DANCE_TWIST", "DANCE_CIRCLE", "DANCE_RIPPLE", "DANCE_WAVE"
    ],
    ("FAST", "HIGH", "SMOOTH"): [
        "DANCE_CIRCLE", "DANCE_SALSA", "DANCE_ROLL_FAST", "DANCE_PITCH_PIVOT"
    ],
    ("FAST", "HIGH", "BUSY"): [
        "DANCE_GALLOP", "DANCE_TWITCH", "DANCE_STROBE", "DANCE_PULSE", "DANCE_WORM"
    ],
}

SAFE_DANCES = [
    "DANCE_TWIST", "DANCE_RIPPLE", "DANCE_CIRCLE", "DANCE_WAVE", "DANCE_PITCH_PIVOT"
]


def choose_dance(rhythm: str, energy: str, activity: str) -> str:
    exact = DANCE_POOLS.get((rhythm, energy, activity))
    if exact:
        return random.choice(exact)

    if energy == "LOW":
        return random.choice(["DANCE_WAVE", "DANCE_BEG_WAVE", "DANCE_CHASSIS_BREATHE"])
    if activity == "BUSY":
        return random.choice(["DANCE_RIPPLE", "DANCE_HEADBANG", "DANCE_PULSE"])
    if rhythm == "FAST":
        return random.choice(["DANCE_SALSA", "DANCE_TWIST", "DANCE_CIRCLE"])
    return random.choice(SAFE_DANCES)


def update_dance_plan():
    """Keep one next movement ready while the current movement is running."""
    if state.operating_mode != "AUTO":
        return

    with state.lock:
        if state.voice_active or time.monotonic() < state.voice_override_until:
            return

        rhythm = state.rhythm_speed
        energy = state.energy_level
        activity = state.activity_level
        signature = (rhythm, energy, activity)

        # Preserve the existing plan while the musical profile is unchanged.
        # Once READY consumes it, planned_move becomes None and the next
        # one-second analysis pass prepares another movement.
        if state.planned_move is not None and signature == state.last_plan_signature:
            return

        planned = choose_dance(rhythm, energy, activity)
        if planned == state.current_move:
            alternatives = [move for move in SAFE_DANCES if move != state.current_move]
            if alternatives:
                planned = random.choice(alternatives)

        state.planned_move = planned
        state.last_plan_signature = signature

    if state.show_audio_logs:
        print(f"🧠 [PLANNED] {planned} | {rhythm}/{energy}/{activity}")


# ---------------------------------------------------------------------------
# Optional YAMNet context - never drives tempo or mood
# ---------------------------------------------------------------------------
yamnet_model = None
yamnet_classes = []


def yamnet_context_thread():
    global yamnet_model, yamnet_classes
    if not ENABLE_YAMNET or tf is None or hub is None:
        print("ℹ️ YAMNet disabled or unavailable; DSP dancing remains active.")
        return
    print("\n⏳ [AI] Loading optional YAMNet context model...")
    try:
        yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
        class_path = yamnet_model.class_map_path().numpy().decode("utf-8")
        with tf.io.gfile.GFile(class_path) as file:
            yamnet_classes = [row["display_name"] for row in csv.DictReader(file)]
        print("✅ [AI] YAMNet loaded.")
    except Exception as exc:
        print(f"❌ [AI] YAMNet unavailable: {exc}")
        return

    while True:
        time.sleep(5)
        try:
            waveform = audio_ring.snapshot(RATE * 3)
            if len(waveform) < RATE:
                continue
            scores, _, _ = yamnet_model(waveform)
            means = np.mean(scores.numpy(), axis=0)
            top_index = int(np.argmax(means))

            music = 0.0
            speech = 0.0
            for index, label in enumerate(yamnet_classes):
                lower = label.lower()
                probability = float(means[index])
                if any(word in lower for word in ("music", "singing", "musical instrument")):
                    music = max(music, probability)
                if any(word in lower for word in ("speech", "conversation", "narration")):
                    speech = max(speech, probability)

            with state.lock:
                state.audio_context = yamnet_classes[top_index]
                state.music_probability = music
                state.speech_probability = speech
        except Exception:
            time.sleep(1)


# ---------------------------------------------------------------------------
# Optional voice command implementation (activation should be button/wake-word)
# ---------------------------------------------------------------------------
recognizer = sr.Recognizer() if sr else None


def say_phrase_offline(text: str):
    if pyttsx3 is None:
        return

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
        with state.lock:
            state.voice_active = False
        return
    try:
        text = recognizer.recognize_google(
            sr.AudioData(audio_bytes, RATE, 2), language="en-US"
        ).lower()
        if state.show_audio_logs:
            print(f"🎤 [VOICE] {text!r}")

        command = None
        phrase = None
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
# Audio capture thread
# ---------------------------------------------------------------------------
def audio_listener():
    analyzer = AdaptiveMusicAnalyzer()

    try:
        if state.audio_source == "BT":
            speaker = sc.default_speaker()
            microphone = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            print(f"🎧 Analysing loopback audio: {speaker.name}")
        else:
            microphone = sc.default_microphone()
            print(f"🎙️ Analysing microphone: {microphone.name}")

        last_log = 0.0
        with microphone.recorder(samplerate=RATE, channels=1) as recorder:
            while True:
                chunk = recorder.record(numframes=CHUNK).reshape(-1).astype(np.float32)
                if len(chunk) != CHUNK:
                    continue
                chunk = np.nan_to_num(chunk, copy=False)
                chunk = np.clip(chunk, -1.0, 1.0)
                audio_ring.append(chunk)

                now = time.monotonic()
                analyzer.process(chunk, now)
                update_dance_plan()

                if state.show_audio_logs and now - last_log >= 1.0:
                    last_log = now
                    with state.lock:
                        print(
                            "🎵 "
                            f"BPM={state.bpm:5.1f} "
                            f"confidence={state.beat_confidence:.2f} | "
                            f"{state.rhythm_speed}/{state.energy_level}/{state.activity_level} | "
                            f"energy={state.energy_score:.2f} "
                            f"activity={state.activity_score:.2f} "
                            f"onsets={state.onsets_per_second:.1f}/s | "
                            f"context={state.audio_context[:18]}"
                        )
    except Exception as exc:
        print(f"❌ Audio listener stopped: {exc}")
        while True:
            time.sleep(1)


# ---------------------------------------------------------------------------
# LED engine
# ---------------------------------------------------------------------------
strip = PixelStrip(
    NUM_LEDS, LED_PIN, 800_000, 10, False, LED_BRIGHTNESS,
    LED_CHANNEL, ws.WS2811_STRIP_GRB
)
strip.begin()


def hsv(hue, sat=255, val=255):
    red, green, blue = colorsys.hsv_to_rgb(
        (hue % 256) / 256.0, sat / 255.0, val / 255.0
    )
    return Color(int(red * 255), int(green * 255), int(blue * 255))


def beatsin(bpm, low, high, phase=0):
    angle = time.monotonic() * bpm * 2 * math.pi / 60 + phase
    position = (math.sin(angle) + 1) / 2
    return int(low + position * (high - low))


def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255.0
    for index in range(NUM_LEDS):
        colour = strip.getPixelColor(index)
        red = (colour >> 16) & 0xFF
        green = (colour >> 8) & 0xFF
        blue = colour & 0xFF
        strip.setPixelColor(index, Color(int(red * scale), int(green * scale), int(blue * scale)))


def led_thread():
    frame = 0
    heat = [0] * NUM_LEDS
    while True:
        try:
            with state.lock:
                mood = state.mood
                voice_active = state.voice_active
                command_time = state.command_detected_time
                manual = state.manual_led_pattern
                bpm = state.bpm
                beat_active = time.monotonic() - state.last_beat_time < 0.15
            elapsed = time.time() - command_time
            frame += 1

            if elapsed < 0.25:
                for i in range(NUM_LEDS):
                    strip.setPixelColor(i, Color(255, 255, 255))
            elif elapsed < 1.0:
                for i in range(NUM_LEDS):
                    strip.setPixelColor(i, Color(0, 50, 255))
            elif voice_active:
                fade_to_black_by(60)
                position = frame % (NUM_LEDS * 2 - 2)
                if position >= NUM_LEDS:
                    position = NUM_LEDS * 2 - 2 - position
                strip.setPixelColor(position, Color(0, 255, 50))
            elif manual:
                render_manual_led(manual, frame, heat)
            elif mood == "AGGRESSIVE":
                if beat_active:
                    for i in range(NUM_LEDS):
                        strip.setPixelColor(i, Color(255, 255, 255))
                else:
                    render_fire(heat)
            elif mood == "ENERGY":
                fade_to_black_by(40)
                position = beatsin(bpm if bpm > 0 else 120, 0, NUM_LEDS - 1)
                strip.setPixelColor(
                    position,
                    Color(255, 255, 255) if beat_active else hsv(int(time.monotonic() * 50) % 256),
                )
            elif mood == "CHILL":
                wave_speed = (bpm / 60.0) * 0.1 if bpm > 0 else 0.1
                for i in range(NUM_LEDS):
                    level = (math.sin(frame * wave_speed - i * 0.5) + 1) / 2
                    brightness = 255 if beat_active else int(25 + level * 200)
                    strip.setPixelColor(i, hsv(frame + i * 10, 230, brightness))
            else:
                level = (math.sin(frame * 0.05) + 1) / 2
                value = int(10 + level * 80)
                for i in range(NUM_LEDS):
                    strip.setPixelColor(i, Color(0, value, value))

            strip.show()
            time.sleep(0.025)
        except Exception:
            time.sleep(1)


def render_fire(heat):
    for i in range(NUM_LEDS):
        heat[i] = max(0, heat[i] - random.randrange(10, 35))
    for i in range(NUM_LEDS - 1, 1, -1):
        heat[i] = (heat[i - 1] + heat[i - 2] * 2) // 3
    if random.randrange(256) < 130:
        spot = random.randrange(min(2, NUM_LEDS))
        heat[spot] = min(255, heat[spot] + random.randrange(160, 256))
    for i, temperature in enumerate(heat):
        ramp = (temperature & 0x3F) << 2
        colour = (
            Color(255, 255, ramp) if temperature > 0x80
            else Color(255, ramp, 0) if temperature > 0x40
            else Color(ramp, 0, 0)
        )
        strip.setPixelColor(i, colour)


def render_manual_led(pattern, frame, heat):
    if pattern == "rainbow":
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, hsv((frame * 5 + i * 18) % 256))
    elif pattern == "confetti":
        fade_to_black_by(25)
        if random.random() < 0.3:
            strip.setPixelColor(random.randrange(NUM_LEDS), hsv(random.randrange(256)))
    elif pattern == "sinelon":
        fade_to_black_by(35)
        strip.setPixelColor(beatsin(18, 0, NUM_LEDS - 1), hsv((frame * 8) % 256))
    elif pattern == "bpm":
        value = beatsin(90, 80, 255)
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, hsv((i * 24 + frame * 3) % 256, 255, value))
    elif pattern == "juggle":
        fade_to_black_by(40)
        for dot in range(4):
            strip.setPixelColor(beatsin(dot + 8, 0, NUM_LEDS - 1, dot * 0.6), hsv(dot * 64))
    elif pattern == "fire":
        render_fire(heat)
    elif pattern == "color_wipe":
        colours = [Color(255, 0, 0), Color(0, 255, 0), Color(0, 0, 255), Color(255, 100, 0)]
        colour = colours[(frame // (NUM_LEDS * 4)) % 4]
        strip.setPixelColor((frame // 4) % NUM_LEDS, colour)
    elif pattern == "theater_chase":
        for i in range(NUM_LEDS):
            colour = hsv((frame * 5 + i * 20) % 256) if (i + frame // 3) % 3 == 0 else Color(0, 0, 0)
            strip.setPixelColor(i, colour)
    elif pattern in ("comet", "dual_scanner"):
        fade_to_black_by(55)
        position = frame % (NUM_LEDS * 2 - 2)
        if position >= NUM_LEDS:
            position = NUM_LEDS * 2 - 2 - position
        strip.setPixelColor(position, Color(255, 20, 0) if pattern == "dual_scanner" else hsv(frame * 5))
        if pattern == "dual_scanner":
            strip.setPixelColor(NUM_LEDS - 1 - position, Color(0, 60, 255))
    elif pattern == "breathing":
        value = int(20 + ((math.sin(frame * 0.05) + 1) / 2) * 100)
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, Color(0, value, min(255, int(value * 2.5))))
    elif pattern == "sparkle_burst":
        fade_to_black_by(30)
        if frame % 40 == 0:
            for _ in range(random.randint(2, NUM_LEDS)):
                strip.setPixelColor(random.randrange(NUM_LEDS), hsv(random.randrange(256)))
    elif pattern == "strobe":
        colour = hsv((frame * 11) % 256, 100, 255) if (frame // 3) % 2 == 0 else Color(0, 0, 0)
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, colour)
    elif pattern == "wave":
        for i in range(NUM_LEDS):
            level = (math.sin(frame * 0.18 - i * 0.9) + 1) / 2
            strip.setPixelColor(i, hsv(frame * 2 + i * 16, 230, int(25 + level * 230)))
    elif pattern == "alternating":
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, Color(255, 0, 80) if (i + frame // 10) % 2 == 0 else Color(0, 180, 255))
    elif pattern == "random_palette":
        if frame % 100 == 0 or not hasattr(render_manual_led, "palette"):
            render_manual_led.palette = [hsv(random.randrange(256)) for _ in range(4)]
        for i in range(NUM_LEDS):
            strip.setPixelColor(i, render_manual_led.palette[i % 4])


# ---------------------------------------------------------------------------
# LCD display
# ---------------------------------------------------------------------------
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(
        spi,
        cs=digitalio.DigitalInOut(DISPLAY_CS_PIN),
        dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
        rst=digitalio.DigitalInOut(DISPLAY_RST_PIN),
        rotation=90,
        baudrate=24_000_000,
    )


def draw_rounded_rect(draw, coordinates, radius, fill):
    draw.rounded_rectangle(coordinates, radius=radius, fill=fill)


def display_loop():
    try:
        display = init_display()
    except Exception:
        print("Display not found. Running headlessly.")
        return

    width, height = 320, 240
    eye_width, eye_height = 70, 120
    left_x, right_x, center_y = 90, 230, 120
    blink_start = time.monotonic()
    blink_interval = random.uniform(2.0, 5.0)
    blinking = False

    while True:
        try:
            with state.lock:
                mood = state.mood
                voice_active = state.voice_active
                command_time = state.command_detected_time
                bpm = state.bpm
                confidence = state.beat_confidence
                rhythm = state.rhythm_speed
                energy = state.energy_level
                activity = state.activity_level
                roll = state.body_roll
                beat_active = time.monotonic() - state.last_beat_time < 0.15

            elapsed = time.time() - command_time
            background = (255, 255, 255) if elapsed < 0.25 else (10, 35, 15) if voice_active else (0, 0, 0)
            image = Image.new("RGB", (width, height), background)
            draw = ImageDraw.Draw(image)
            draw.text((5, 5), f"{bpm:.0f} BPM  C:{confidence:.2f}  {rhythm}", fill=(150, 150, 150))
            draw.text((5, 20), f"{energy} energy / {activity}", fill=(100, 100, 100))

            eye_colour, current_height = (0, 255, 255), eye_height
            if elapsed < 0.25:
                eye_colour, current_height = (0, 0, 0), int(eye_height * 0.4)
            elif voice_active:
                eye_colour, current_height = (0, 255, 100), int(eye_height * 0.75)
            elif mood == "AGGRESSIVE":
                eye_colour, current_height = (255, 50, 50), eye_height + 20
            elif mood == "ENERGY":
                eye_colour, current_height = (255, 150, 50), eye_height + 10
            elif mood == "CHILL":
                eye_colour, current_height = (150, 50, 255), int(eye_height * 0.6)

            now = time.monotonic()
            if now - blink_start > blink_interval:
                blinking = True
                blink_start = now
                blink_interval = random.uniform(2.0, 5.0)
            if blinking and not voice_active:
                current_height = 10
                if now - blink_start > 0.15:
                    blinking = False

            current_width = eye_width + 10 if beat_active and not voice_active else eye_width
            roll_offset = int(roll * 1.5)
            for x, y in ((left_x, center_y + roll_offset), (right_x, center_y - roll_offset)):
                draw_rounded_rect(
                    draw,
                    [x - current_width // 2, y - current_height // 2,
                     x + current_width // 2, y + current_height // 2],
                    20,
                    eye_colour,
                )
            display.image(image)
            time.sleep(0.04)
        except Exception:
            time.sleep(1)


# ---------------------------------------------------------------------------
# Manual controls
# ---------------------------------------------------------------------------
CLI_COMMANDS = {
    11: ("WALK_FORWARD", "Walk Fwd"), 12: ("WALK_BACKWARD", "Walk Back"),
    13: ("TURN_LEFT", "Turn L"), 14: ("TURN_RIGHT", "Turn R"),
    15: ("STAND", "Stand"), 16: ("RELAX", "Deactivate"),
    21: ("DANCE_WAVE", "Wave"), 22: ("DANCE_RIPPLE", "Ripple"),
    24: ("DANCE_PEACOCK", "Peacock"),
    25: ("DANCE_SALSA", "Salsa"), 26: ("DANCE_TWIST", "Twist"),
    30: ("DANCE_ROLL_FAST", "Fast Roll"), 32: ("DANCE_CIRCLE", "Circle"),
    34: ("DANCE_CRAWL", "Crawl"),
    35: ("DANCE_HEADBANG", "Headbang"), 36: ("DANCE_STROBE", "Strobe"),
    37: ("DANCE_PULSE", "Pulse"), 38: ("DANCE_GALLOP", "Gallop"),
    39: ("DANCE_BEG_WAVE", "Beg Wave"), 40: ("DANCE_CHASSIS_BREATHE", "Breathe"),
    41: ("DANCE_BELLY_CRAWL", "Belly Crawl"), 42: ("DANCE_PITCH_PIVOT", "Pitch Pivot"),
    43: ("DANCE_TWITCH", "Twitch"), 44: ("DANCE_WORM", "Worm"),
    70: ("TEST_LEG_0", "Test Leg 0"), 71: ("TEST_LEG_1", "Test Leg 1"),
    72: ("TEST_LEG_2", "Test Leg 2"), 73: ("TEST_LEG_3", "Test Leg 3"),
    74: ("TEST_LEG_4", "Test Leg 4"), 75: ("TEST_LEG_5", "Test Leg 5"),
}

LED_PATTERNS = {
    51: "rainbow", 52: "confetti", 53: "sinelon", 54: "bpm",
    55: "juggle", 56: "fire", 57: "color_wipe", 58: "theater_chase",
    59: "comet", 60: "dual_scanner", 61: "breathing", 62: "sparkle_burst",
    63: "strobe", 64: "wave", 65: "alternating", 66: "random_palette",
}


def print_menu():
    print("""
======================================================================
                     🤖 HEXAPOD CONTROL 🤖
======================================================================
 Movement: 11 Forward, 12 Back, 13 Left, 14 Right, 15 Stand, 16 Relax
 Dances:   21-44
 LEDs:     51-66, 69 return to adaptive mode
 Tests:    70-75 individual legs
 System:   91 toggle logs, 92 capture voice command, 0 exit
======================================================================
""")


def capture_voice_command():
    if recognizer is None:
        print("Speech-recognition packages are unavailable.")
        return
    with state.lock:
        state.voice_active = True
    print("🎤 Listening from the recent five-second audio buffer...")
    waveform = audio_ring.snapshot(RATE * 5)
    audio_bytes = (np.clip(waveform, -1, 1) * 32767).astype(np.int16).tobytes()
    threading.Thread(target=process_voice_command, args=(audio_bytes,), daemon=True).start()


def manual_testing_loop():
    print_menu()
    while True:
        try:
            choice = input("Enter command number ('m' for menu) >>> ").strip().lower()
            if choice in ("0", "q"):
                os._exit(0)
            if choice == "m":
                print_menu()
                continue
            if not choice.isdigit():
                print("Invalid input.")
                continue
            number = int(choice)
            if number in CLI_COMMANDS:
                send_to_esp32(CLI_COMMANDS[number][0])
                with state.lock:
                    state.command_detected_time = time.time()
            elif number in LED_PATTERNS:
                with state.lock:
                    state.manual_led_pattern = LED_PATTERNS[number]
            elif number == 69:
                with state.lock:
                    state.manual_led_pattern = None
            elif number == 91:
                with state.lock:
                    state.show_audio_logs = not state.show_audio_logs
                    print(f"Audio logs: {'ON' if state.show_audio_logs else 'OFF'}")
            elif number == 92:
                capture_voice_command()
            else:
                print("Invalid command.")
        except KeyboardInterrupt:
            os._exit(0)


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def start_background_threads():
    targets = (
        esp32_reader_thread,
        yamnet_context_thread,
        audio_listener,
        led_thread,
        display_loop,
    )
    for target in targets:
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
        print(" [2] Internal Bluetooth/YouTube/Spotify loopback (recommended)")
        source = input(">>> ").strip()
        state.audio_source = "BT" if source == "2" else "MIC"
    else:
        state.operating_mode = "MANUAL"
        state.audio_source = "MIC"

    os.system("amixer set Master 100% > /dev/null 2>&1")
    start_background_threads()

    if state.operating_mode == "AUTO":
        print("\n✅ Adaptive auto mode running. Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            send_to_esp32("STAND")
    else:
        time.sleep(1)
        manual_testing_loop()
