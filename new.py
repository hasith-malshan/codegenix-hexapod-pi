import sys
import importlib.util
import os
import random
import collections

class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
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
import speech_recognition as sr
import pyttsx3
from scipy.signal import butter, lfilter

import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341

# ==========================================
# USB SERIAL CONNECTION
# ==========================================
print("Connecting to ESP32 over USB...")
try:
    esp32_serial = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    print("Connected to ESP32 on /dev/ttyUSB0")
except Exception as e:
    print(f"Failed to connect: {e}")
    esp32_serial = None

# ==========================================
# FIX: ACK-GATED COMMAND SENDER
#
# ORIGINAL BUG: send_to_esp32() wrote commands directly with no
# flow control. If a beat-sync move fired while a previous dance
# was still running on the ESP32, the ESP32 received two commands
# in quick succession. It processed the first immediately and the
# second arrived mid-motion — causing processCommand() to set a
# new currentState while smoothLegsToTarget() was mid-loop.
# checkStop() only polled every 3rd step, so the ESP32 sometimes
# missed the new command entirely and continued with the old move.
#
# FIX: After every command we wait for "READY\n" from the ESP32
# before allowing the next command to be sent. A background thread
# reads the serial port continuously. send_to_esp32() queues
# commands and only releases them when the ESP32 is ready.
# A timeout of 8 seconds prevents a deadlock if READY is lost.
# ==========================================

_esp32_ready      = threading.Event()
_esp32_ready.set()   # Initially ready (ESP32 sends READY on boot)
_send_lock        = threading.Lock()
READY_TIMEOUT_SEC = 8.0

def esp32_reader_thread():
    """Background thread: reads all serial output from ESP32.
    Sets _esp32_ready when 'READY' is received.
    Prints everything else for debugging.
    """
    while True:
        if esp32_serial and esp32_serial.is_open:
            try:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                if line == "READY":
                    _esp32_ready.set()
                elif line:
                    print(f"[ESP32] {line}")
            except Exception as e:
                print(f"Serial read error: {e}")
                time.sleep(0.1)
        else:
            time.sleep(0.1)

def send_to_esp32(command):
    """Send a command only after the ESP32 signals it is ready.
    Blocks for up to READY_TIMEOUT_SEC seconds waiting for READY.
    """
    if not (esp32_serial and esp32_serial.is_open):
        return
    with _send_lock:
        if not _esp32_ready.wait(timeout=READY_TIMEOUT_SEC):
            print(f"WARNING: ESP32 READY timeout — sending '{command}' anyway")
        _esp32_ready.clear()
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
            print(f"Sent: {command}")
        except Exception as e:
            print(f"Send failed: {e}")
            _esp32_ready.set()   # Unblock on error

# ==========================================
# AUDIO CONFIG
# ==========================================
RATE  = 16000
CHUNK = 512

def _make_bandpass(lowcut, highcut, fs, order=4):
    nyq  = 0.5 * fs
    b, a = butter(order, [lowcut / nyq, highcut / nyq], btype='band')
    return b, a

_BP_B, _BP_A = _make_bandpass(300, 3400, RATE, order=4)

def bandpass(data):
    return np.ascontiguousarray(lfilter(_BP_B, _BP_A, data), dtype=np.float32)

DISPLAY_CS_PIN  = board.CE0
DISPLAY_DC_PIN  = board.D24
DISPLAY_RST_PIN = board.D25

# ==========================================
# 3-STAGE VOICE ACTIVITY DETECTOR
# ==========================================
class VAD:
    NOISE_ALPHA       = 0.97
    ENERGY_MULTIPLIER = 3.5
    ZCR_MIN           = 0.04
    ZCR_MAX           = 0.35
    BAND_RATIO_MIN    = 0.55
    CONFIRM_CHUNKS    = 5
    SILENCE_CHUNKS    = 18

    def __init__(self):
        self.noise_floor         = 0.02
        self.consecutive_voice   = 0
        self.consecutive_silence = 0
        self.in_speech           = False

    def _zcr(self, chunk):
        return np.sum(np.diff(np.sign(chunk)) != 0) / len(chunk)

    def _band_ratio(self, chunk):
        spec      = np.abs(np.fft.rfft(chunk))
        freqs     = np.fft.rfftfreq(len(chunk), 1.0 / RATE)
        voice_idx = (freqs >= 300) & (freqs <= 3400)
        total_e   = np.sum(spec ** 2)
        if total_e < 1e-10:
            return 0.0
        return float(np.sum(spec[voice_idx] ** 2) / total_e)

    def update(self, chunk):
        energy = float(np.sqrt(np.mean(chunk ** 2)))
        zcr    = self._zcr(chunk)

        if energy < self.noise_floor * 1.5:
            self.noise_floor = (self.NOISE_ALPHA * self.noise_floor +
                                (1.0 - self.NOISE_ALPHA) * energy)

        energy_ok  = energy > (self.noise_floor * self.ENERGY_MULTIPLIER)
        zcr_ok     = self.ZCR_MIN < zcr < self.ZCR_MAX
        band_ratio = self._band_ratio(chunk) if energy_ok else 0.0
        band_ok    = band_ratio > self.BAND_RATIO_MIN

        is_voice = energy_ok and zcr_ok and band_ok

        if is_voice:
            self.consecutive_voice   += 1
            self.consecutive_silence  = 0
        else:
            self.consecutive_silence += 1
            self.consecutive_voice    = 0

        if not self.in_speech:
            if self.consecutive_voice >= self.CONFIRM_CHUNKS:
                self.in_speech = True
                return 'START'
        else:
            if self.consecutive_silence >= self.SILENCE_CHUNKS:
                self.in_speech = False
                return 'END'
            return 'ACTIVE'

        return 'SILENT'

    def reset(self):
        self.consecutive_voice   = 0
        self.consecutive_silence = 0
        self.in_speech           = False

# ==========================================
# BEAT TRACKER
# ==========================================
class BeatTracker:
    def __init__(self):
        self.bpm_history     = collections.deque(maxlen=20)
        self.beat_timestamps = collections.deque(maxlen=10)
        self.smoothed_bpm    = 0.0
        self.beat_confidence = 0.0
        self.last_beat_time  = 0.0
        self.beat_interval   = 0.5

    def add_beat(self, bpm, t):
        if self.beat_timestamps:
            dt = t - self.last_beat_time
            ei = 60.0 / bpm if bpm > 0 else 0.5
            self.beat_confidence = max(0.0, 1.0 - abs(dt - ei) / ei) if ei > 0.1 else 0.5
        else:
            self.beat_confidence = 0.8

        self.bpm_history.append(bpm)
        self.beat_timestamps.append(t)
        self.last_beat_time   = t
        self.smoothed_bpm     = 0.3 * bpm + 0.7 * self.smoothed_bpm

        if len(self.beat_timestamps) >= 2:
            intervals          = [self.beat_timestamps[i] - self.beat_timestamps[i-1]
                                  for i in range(1, len(self.beat_timestamps))]
            self.beat_interval = float(np.median(intervals))

    def get_valid_bpm(self):
        valid = [b for b in self.bpm_history if 50 < b < 200]
        return float(np.median(valid)) if valid else self.smoothed_bpm

# ==========================================
# ROBOT STATE
# ==========================================
class RobotState:
    def __init__(self):
        self.bpm                     = 0.0
        self.genre                   = "Listening..."
        self.beat_hit                = False
        self.music_speed             = "IDLE"
        self.voice_active            = False
        self.command_detected_time   = 0.0
        self.beat_tracker            = BeatTracker()
        self.vad                     = VAD()
        self.last_dance_command_time = time.time()
        self.voice_override_until    = 0.0
        self.dance_interval          = 1.0
        self.lock                    = threading.Lock()

state = RobotState()

# ==========================================
# TWO-STAGE AUDIO CAPTURE BUFFER
# ==========================================
PRE_BUFFER_SEC  = 0.7
CAPTURE_MAX_SEC = 3.0

_pre_buf_lock  = threading.Lock()
_pre_buf       = collections.deque(maxlen=int(RATE * PRE_BUFFER_SEC / CHUNK))
_capture_buf   = []
_capturing     = False
_capture_start = 0.0

# ==========================================
# YAMNET AI ENGINE
# ==========================================
yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')

def get_class_names():
    class_map_path = yamnet_model.class_map_path().numpy().decode('utf-8')
    names = []
    with tf.io.gfile.GFile(class_map_path) as f:
        for row in csv.DictReader(f):
            names.append(row['display_name'])
    return names

YAMNET_CLASSES = get_class_names()
audio_buffer   = np.zeros(RATE * 3, dtype=np.float32)

# ==========================================
# SPEECH RECOGNIZER + CALIBRATION
# ==========================================
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = False

def calibrate_recognizer():
    print("Calibrating microphone (2 seconds of silence please)...")
    mic = sc.default_microphone()
    samples = []
    with mic.recorder(samplerate=RATE, channels=1) as rec:
        for _ in range(int(RATE * 2 / CHUNK)):
            chunk   = rec.record(numframes=CHUNK).flatten().astype(np.float32)
            samples.append(float(np.sqrt(np.mean(bandpass(chunk) ** 2))))
    noise = float(np.percentile(samples, 75))
    recognizer.energy_threshold = max(300, noise * 8 * 32767)
    print(f"Calibrated. Noise floor: {noise:.4f} | SR threshold: {recognizer.energy_threshold:.0f}")

def say_phrase_offline(text):
    def _speak():
        try:
            e = pyttsx3.init()
            e.setProperty('rate', 145)
            e.setProperty('volume', 1.0)
            e.say(text)
            e.runAndWait()
        except:
            pass
    threading.Thread(target=_speak, daemon=True).start()

# ==========================================
# YAMNET THREAD
# ==========================================
def run_yamnet_periodically():
    while True:
        time.sleep(4)
        snap   = np.copy(audio_buffer)
        scores, _, _ = yamnet_model(snap)
        top    = int(np.argmax(np.mean(scores, axis=0)))
        with state.lock:
            if "CMD" not in state.genre:
                state.genre = YAMNET_CLASSES[top]

# ==========================================
# COMMAND MATCHING + GOOGLE SPEECH
# ==========================================
def _normalize(audio_float32):
    peak = np.max(np.abs(audio_float32))
    if peak > 0.01:
        return audio_float32 / peak * 0.9
    return audio_float32

COMMANDS = [
    (["forward",  "advance"],                   "WALK_FORWARD",   "walking forward"),
    (["backward", "back",   "reverse"],         "WALK_BACKWARD",  "walking backward"),
    (["left"],                                  "TURN_LEFT",      "turning left"),
    (["right"],                                 "TURN_RIGHT",     "turning right"),
    (["stop",     "stand",  "halt"],            "STAND",          "stopping"),
    (["dance",    "party",  "groove"],          "DANCE_CIRCLE",   "lets party"),
    (["slow",     "acoustic","ballad"],         "DANCE_ROLL_SLOW","slow mode"),
    (["fast",     "speed",  "rapid", "quick"],  "DANCE_ROLL_FAST","high speed"),
    (["twist"],                                 "DANCE_TWIST",    "doing the twist"),
    (["wave",     "hello"],                     "DANCE_WAVE",     "waving hello"),
    (["circle",   "spin"],                      "DANCE_CIRCLE_2", "spinning around"),
]

def process_voice_command(audio_bytes):
    print("Processing command...")

    for attempt in range(3):
        try:
            audio_data = sr.AudioData(audio_bytes, RATE, 2)
            text = recognizer.recognize_google(audio_data, language='en-US').lower()
            print(f"Heard: '{text}'")

            matched = False
            for keywords, cmd, phrase in COMMANDS:
                if any(kw in text for kw in keywords):
                    send_to_esp32(cmd)
                    say_phrase_offline(phrase)
                    with state.lock:
                        state.command_detected_time = time.time()
                        # FIX: Extended override window to 15s (was 12s).
                        # The ACK protocol now enforces sequencing anyway,
                        # but a longer window prevents beat-sync from firing
                        # a new dance command before the voice command finishes.
                        state.voice_override_until  = time.time() + 15.0
                        state.beat_tracker.bpm_history.clear()
                    print(f"Executed: {cmd}")
                    matched = True
                    break

            if not matched:
                print(f"No command matched: '{text}'")
                # FIX: If no command matched, unblock the ready event so
                # beat-sync can resume immediately (no command was sent,
                # so no READY will arrive from ESP32).
                _esp32_ready.set()
            break

        except sr.UnknownValueError:
            print(f"Could not understand (attempt {attempt+1}/3)")
            if attempt < 2:
                time.sleep(0.08)
        except sr.RequestError as e:
            print(f"Google API error: {e}")
            # FIX: Unblock on API error (no command was sent).
            _esp32_ready.set()
            break
        except Exception as e:
            print(f"Error: {e}")
            _esp32_ready.set()
            break

    with state.lock:
        state.voice_active = False
    state.vad.reset()

# ==========================================
# TRIGGER HELPER
# ==========================================
def _trigger_recognition():
    global _capturing, _capture_buf
    if not _capture_buf:
        _capturing = False
        return

    audio = np.concatenate(_capture_buf).astype(np.float32)
    audio = _normalize(audio)
    audio_bytes = (audio * 32767).astype(np.int16).tobytes()

    with state.lock:
        state.voice_active = True

    _capturing   = False
    _capture_buf = []

    threading.Thread(
        target=process_voice_command,
        args=(audio_bytes,),
        daemon=True
    ).start()

# ==========================================
# MAIN AUDIO LISTENER
# ==========================================
def audio_listener():
    global audio_buffer, _capture_buf, _capturing, _capture_start

    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)

    mic           = sc.default_microphone()
    beat_debounce = time.time()

    print("\n=== AI DANCER — HIGH ACCURACY VAD ===\n")

    with mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw   = recorder.record(numframes=CHUNK)
            chunk = raw.flatten().astype(np.float32)
            now   = time.time()

            # Rolling buffer for YAMNet
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = chunk

            # Always update pre-buffer
            with _pre_buf_lock:
                _pre_buf.append(chunk.copy())

            # VAD on bandpass-filtered chunk
            vocal = bandpass(chunk)

            with state.lock:
                skip_vad = state.voice_active or (now < state.voice_override_until)

            if not skip_vad:
                vad_result = state.vad.update(vocal)

                if vad_result == 'START' and not _capturing:
                    _capturing     = True
                    _capture_start = now
                    with _pre_buf_lock:
                        _capture_buf = [c.copy() for c in _pre_buf]
                    _capture_buf.append(chunk.copy())
                    print("Voice start")

                elif vad_result in ('ACTIVE', 'START') and _capturing:
                    _capture_buf.append(chunk.copy())
                    if now - _capture_start > CAPTURE_MAX_SEC:
                        print("Max length reached, sending...")
                        _trigger_recognition()

                elif vad_result == 'END' and _capturing:
                    print("Voice end")
                    _trigger_recognition()

            elif _capturing:
                _capturing   = False
                _capture_buf = []
                state.vad.reset()

            # Beat detection
            if aubio_tempo(chunk)[0]:
                if now - beat_debounce > 0.2:
                    bpm = aubio_tempo.get_bpm()
                    with state.lock:
                        g = state.genre
                        if bpm < 30:    bpm *= 2
                        elif bpm > 200: bpm /= 2
                        if 40 < bpm < 90 and any(x in g for x in ["Electronic","Dance","Rock","Pop"]):
                            bpm *= 2
                        state.beat_tracker.add_beat(bpm, now)
                        state.bpm      = state.beat_tracker.smoothed_bpm
                        state.beat_hit = True
                    beat_debounce = now

            # Beat-synced choreography
            with state.lock:
                t         = time.time()
                overdue   = (t - state.last_dance_command_time) >= state.dance_interval
                free      = t > state.voice_override_until and not state.voice_active
                has_beats = len(state.beat_tracker.bpm_history) >= 3
                confident = state.beat_tracker.beat_confidence > 0.4

                # FIX: Also gate on _esp32_ready so we don't queue a beat-sync
                # command while the ESP32 is still executing the previous one.
                esp32_free = _esp32_ready.is_set()

                if overdue and free and has_beats and confident and esp32_free:
                    bpm_val = state.beat_tracker.get_valid_bpm()
                    genre   = state.genre
                    slow    = ["Acoustic","Vocal","Speech","Choir","Folk","Singer","Ballad","Blues"]

                    if any(s in genre for s in slow) or bpm_val < 100:
                        state.music_speed = "SLOW"
                        move = random.choice(["DANCE_ROLL_SLOW","DANCE_PEACOCK","DANCE_WAVE","DANCE_RIPPLE"])
                        print(f"SLOW {bpm_val:.0f} BPM -> {move}")
                    elif bpm_val < 130:
                        state.music_speed = "MEDIUM"
                        move = random.choice(["DANCE_TWIST","DANCE_RIPPLE_2","DANCE_CIRCLE","DANCE_SALSA"])
                        print(f"MEDIUM {bpm_val:.0f} BPM -> {move}")
                    else:
                        state.music_speed = "FAST"
                        move = random.choice(["DANCE_ROLL_FAST","DANCE_TWIST_2","DANCE_CIRCLE_2"])
                        print(f"FAST {bpm_val:.0f} BPM -> {move}")

                    send_to_esp32(move)
                    state.last_dance_command_time = t
                    state.beat_tracker.bpm_history.clear()

# ==========================================
# DISPLAY
# ==========================================
def init_display():
    spi  = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    disp = ili9341.ILI9341(
        spi,
        cs=digitalio.DigitalInOut(DISPLAY_CS_PIN),
        dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
        rst=digitalio.DigitalInOut(DISPLAY_RST_PIN),
        rotation=90, baudrate=24000000
    )
    return disp

def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    r = min(corner_radius, (x1-x0)//2, (y1-y0)//2)
    if r <= 0:
        draw.rectangle([x0,y0,x1,y1], fill=fill)
        return
    draw.rectangle([x0, y0+r, x1, y1-r], fill=fill)
    draw.rectangle([x0+r, y0, x1-r, y1], fill=fill)
    draw.pieslice([x0,        y0,        x0+r*2, y0+r*2], 180, 270, fill=fill)
    draw.pieslice([x1-r*2,    y1-r*2,    x1,     y1    ],   0,  90, fill=fill)
    draw.pieslice([x0,        y1-r*2,    x0+r*2, y1    ],  90, 180, fill=fill)
    draw.pieslice([x1-r*2,    y0,        x1,     y0+r*2], 270, 360, fill=fill)

def display_loop():
    os.system("amixer set Master 100% > /dev/null 2>&1")
    disp         = init_display()
    eye_w, eye_h = 70, 120
    lx, rx       = 90, 230
    cy           = 120
    blink_timer  = time.time()
    is_blinking  = False

    while True:
        with state.lock:
            speed       = state.music_speed
            beat_active = state.beat_hit
            va          = state.voice_active
            cmd_t       = state.command_detected_time
            bpm         = state.bpm
            state.beat_hit = False

        dt = time.time() - cmd_t

        bg = (255,255,255) if dt<0.25 else (30,30,80) if dt<1.0 else (10,35,15) if va else (0,0,0)
        img  = Image.new("RGB", (320,240), color=bg)
        draw = ImageDraw.Draw(img)

        if bpm > 0:
            draw.text((10,10), f"{bpm:.0f}", fill=(100,100,100))

        h   = eye_h
        col = (0,255,255)
        cy_r = cy

        if   dt < 0.25: col,h,cy_r = (0,0,0),        int(eye_h*0.4), cy-10
        elif dt < 1.0:  col,h,cy_r = (0,191,255),     int(eye_h*0.4), cy-10
        elif va:        col,h      = (0,255,100),      int(eye_h*0.75)
        elif speed=="FAST":   col,h = (255,50,50),     eye_h+20
        elif speed=="MEDIUM": col,h = (255,150,50),    eye_h+10
        elif speed=="SLOW":   col,h = (150,50,255),    int(eye_h*0.6)

        ew = eye_w+10 if (beat_active and not va and dt>1.0) else eye_w

        if time.time()-blink_timer > np.random.uniform(2.0,5.0):
            is_blinking = True
            blink_timer = time.time()
        if is_blinking and not va and dt>1.0:
            h = 10
            if time.time()-blink_timer > 0.15:
                is_blinking = False

        for cx in [lx, rx]:
            draw_rounded_rect(draw,
                [cx-ew//2, cy_r-h//2, cx+ew//2, cy_r+h//2],
                corner_radius=20, fill=col)

        disp.image(img)
        time.sleep(0.03)

# ==========================================
# STARTUP
# ==========================================
calibrate_recognizer()
threading.Thread(target=esp32_reader_thread,      daemon=True).start()
threading.Thread(target=run_yamnet_periodically,  daemon=True).start()
threading.Thread(target=audio_listener,           daemon=True).start()
display_loop()
