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
SERIAL_PORT = '/dev/ttyUSB0'  # CHANGE THIS TO '/dev/ttyACM0' IF NEEDED
print(f"Connecting to ESP32 over {SERIAL_PORT}...")
try:
    esp32_serial = serial.Serial(SERIAL_PORT, 115200, timeout=1)
    print(f"Connected to ESP32 on {SERIAL_PORT}")
except Exception as e:
    print(f"Failed to connect: {e}")
    print("WARNING: Manual mode will run, but no commands will physically reach the robot.")
    esp32_serial = None

# ==========================================
# ACK-GATED COMMAND SENDER
# ==========================================
_esp32_ready      = threading.Event()
_esp32_ready.set()
_send_lock        = threading.Lock()
READY_TIMEOUT_SEC = 8.0

def esp32_reader_thread():
    while True:
        if esp32_serial and esp32_serial.is_open:
            try:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                if line == "READY":
                    _esp32_ready.set()
                elif line:
                    print(f"[ESP32] {line}")
            except Exception as e:
                time.sleep(0.1)
        else:
            time.sleep(0.1)

def send_to_esp32(command):
    if not (esp32_serial and esp32_serial.is_open):
        print(f" [Simulation] ESP32 Not Connected. Would have sent: {command}")
        return
    with _send_lock:
        if not _esp32_ready.wait(timeout=READY_TIMEOUT_SEC):
            print(f"WARNING: ESP32 READY timeout — sending '{command}' anyway")
        _esp32_ready.clear()
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
        except Exception as e:
            print(f"Send failed: {e}")
            _esp32_ready.set()

# ==========================================
# AUDIO CONFIG & VAD (AI)
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
        if total_e < 1e-10: return 0.0
        return float(np.sum(spec[voice_idx] ** 2) / total_e)

    def update(self, chunk):
        energy = float(np.sqrt(np.mean(chunk ** 2)))
        zcr    = self._zcr(chunk)
        if energy < self.noise_floor * 1.5:
            self.noise_floor = (self.NOISE_ALPHA * self.noise_floor + (1.0 - self.NOISE_ALPHA) * energy)
        energy_ok  = energy > (self.noise_floor * self.ENERGY_MULTIPLIER)
        zcr_ok     = self.ZCR_MIN < zcr < self.ZCR_MAX
        band_ratio = self._band_ratio(chunk) if energy_ok else 0.0
        band_ok    = band_ratio > self.BAND_RATIO_MIN
        is_voice = energy_ok and zcr_ok and band_ok
        if is_voice:
            self.consecutive_voice += 1; self.consecutive_silence = 0
        else:
            self.consecutive_silence += 1; self.consecutive_voice = 0
        if not self.in_speech:
            if self.consecutive_voice >= self.CONFIRM_CHUNKS:
                self.in_speech = True; return 'START'
        else:
            if self.consecutive_silence >= self.SILENCE_CHUNKS:
                self.in_speech = False; return 'END'
            return 'ACTIVE'
        return 'SILENT'

    def reset(self):
        self.consecutive_voice = 0; self.consecutive_silence = 0; self.in_speech = False

class BeatTracker:
    def __init__(self):
        self.bpm_history = collections.deque(maxlen=20)
        self.beat_timestamps = collections.deque(maxlen=10)
        self.smoothed_bpm = 0.0
        self.beat_confidence = 0.0
        self.last_beat_time = 0.0
        self.beat_interval = 0.5

    def add_beat(self, bpm, t):
        if self.beat_timestamps:
            dt = t - self.last_beat_time
            ei = 60.0 / bpm if bpm > 0 else 0.5
            self.beat_confidence = max(0.0, 1.0 - abs(dt - ei) / ei) if ei > 0.1 else 0.5
        else:
            self.beat_confidence = 0.8
        self.bpm_history.append(bpm)
        self.beat_timestamps.append(t)
        self.last_beat_time = t
        self.smoothed_bpm = 0.3 * bpm + 0.7 * self.smoothed_bpm
        if len(self.beat_timestamps) >= 2:
            intervals = [self.beat_timestamps[i] - self.beat_timestamps[i-1] for i in range(1, len(self.beat_timestamps))]
            self.beat_interval = float(np.median(intervals))

    def get_valid_bpm(self):
        valid = [b for b in self.bpm_history if 50 < b < 200]
        return float(np.median(valid)) if valid else self.smoothed_bpm

class RobotState:
    def __init__(self):
        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"
        self.voice_active = False
        self.command_detected_time = 0.0
        self.beat_tracker = BeatTracker()
        self.vad = VAD()
        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0
        self.dance_interval = 1.0
        self.lock = threading.Lock()

state = RobotState()

PRE_BUFFER_SEC  = 0.7
CAPTURE_MAX_SEC = 3.0
_pre_buf_lock  = threading.Lock()
_pre_buf       = collections.deque(maxlen=int(RATE * PRE_BUFFER_SEC / CHUNK))
_capture_buf   = []
_capturing     = False
_capture_start = 0.0

audio_buffer   = np.zeros(RATE * 3, dtype=np.float32)

recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = False

# LAZY LOAD YAMNET GLOBALS
yamnet_model = None
YAMNET_CLASSES = []

def init_ai_models():
    """Only called if Mode 1 is selected. Prevents crashes if Pi has no internet during Testing mode."""
    global yamnet_model, YAMNET_CLASSES
    print("Downloading/Loading YAMNet model from TensorFlow Hub. This requires Internet...")
    try:
        yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
        class_map_path = yamnet_model.class_map_path().numpy().decode('utf-8')
        names = []
        with tf.io.gfile.GFile(class_map_path) as f:
            for row in csv.DictReader(f):
                names.append(row['display_name'])
        YAMNET_CLASSES = names
        print("YAMNet Loaded Successfully.")
    except Exception as e:
        print(f"\nCRITICAL ERROR: Failed to load YAMNet. Check Pi Internet connection. \nDetails: {e}")
        os._exit(1)

def calibrate_recognizer():
    print("\nCalibrating microphone (2 seconds of silence please)...")
    try:
        mic = sc.default_microphone()
        samples = []
        with mic.recorder(samplerate=RATE, channels=1) as rec:
            for _ in range(int(RATE * 2 / CHUNK)):
                chunk = rec.record(numframes=CHUNK).flatten().astype(np.float32)
                samples.append(float(np.sqrt(np.mean(bandpass(chunk) ** 2))))
        noise = float(np.percentile(samples, 75))
        recognizer.energy_threshold = max(300, noise * 8 * 32767)
        print(f"Calibrated. Noise floor: {noise:.4f} | SR threshold: {recognizer.energy_threshold:.0f}")
    except Exception as e:
        print(f"Audio calibration failed (No mic found?): {e}")

def say_phrase_offline(text):
    def _speak():
        try:
            e = pyttsx3.init()
            e.setProperty('rate', 145)
            e.setProperty('volume', 1.0)
            e.say(text)
            e.runAndWait()
        except: pass
    threading.Thread(target=_speak, daemon=True).start()

def run_yamnet_periodically():
    while True:
        time.sleep(4)
        if yamnet_model is None: continue
        snap   = np.copy(audio_buffer)
        scores, _, _ = yamnet_model(snap)
        top    = int(np.argmax(np.mean(scores, axis=0)))
        with state.lock:
            if "CMD" not in state.genre:
                state.genre = YAMNET_CLASSES[top]

def _normalize(audio_float32):
    peak = np.max(np.abs(audio_float32))
    if peak > 0.01: return audio_float32 / peak * 0.9
    return audio_float32

COMMANDS = [
    (["forward",  "advance"],                          "WALK_FORWARD",   "walking forward"),
    (["backward", "back",   "reverse"],                "WALK_BACKWARD",  "walking backward"),
    (["left"],                                         "TURN_LEFT",      "turning left"),
    (["right"],                                        "TURN_RIGHT",     "turning right"),
    (["stop",     "stand",  "halt"],                   "STAND",          "stopping"),
    (["dance",    "party",  "groove"],                 "DANCE_CIRCLE",   "lets party"),
    (["slow",     "acoustic","ballad"],                "DANCE_ROLL_SLOW","slow mode"),
    (["fast",     "speed",  "rapid", "quick"],         "DANCE_ROLL_FAST","high speed"),
    (["twist"],                                        "DANCE_TWIST",    "doing the twist"),
    (["wave",     "hello"],                            "DANCE_WAVE",     "waving hello"),
    (["circle",   "spin"],                             "DANCE_CIRCLE_2", "spinning around"),
    (["crawl",    "creep",  "slow walk"],              "DANCE_CRAWL",    "creeping slowly"),
    (["headbang", "bang",   "slam"],                   "DANCE_HEADBANG", "headbanging"),
    (["strobe",   "flash"],                            "DANCE_STROBE",   "strobing"),
    (["pulse",    "heartbeat", "expand"],              "DANCE_PULSE",    "pulsing"),
    (["gallop",   "trot"],                             "DANCE_GALLOP",   "galloping"),
]

def process_voice_command(audio_bytes):
    print("\nProcessing command...")
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
                        state.voice_override_until  = time.time() + 15.0
                        state.beat_tracker.bpm_history.clear()
                    print(f"Executed: {cmd}")
                    matched = True
                    break
            if not matched: _esp32_ready.set()
            break
        except sr.UnknownValueError:
            if attempt < 2: time.sleep(0.08)
        except Exception:
            _esp32_ready.set(); break
    with state.lock: state.voice_active = False
    state.vad.reset()

def _trigger_recognition():
    global _capturing, _capture_buf
    if not _capture_buf:
        _capturing = False; return
    audio = np.concatenate(_capture_buf).astype(np.float32)
    audio = _normalize(audio)
    audio_bytes = (audio * 32767).astype(np.int16).tobytes()
    with state.lock: state.voice_active = True
    _capturing = False; _capture_buf = []
    threading.Thread(target=process_voice_command, args=(audio_bytes,), daemon=True).start()

def audio_listener():
    global audio_buffer, _capture_buf, _capturing, _capture_start
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)
    mic = sc.default_microphone()
    beat_debounce = time.time()
    print("\n=== AI DANCER — HIGH ACCURACY VAD RUNNING ===\n")
    with mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw = recorder.record(numframes=CHUNK)
            chunk = raw.flatten().astype(np.float32)
            now = time.time()
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = chunk
            with _pre_buf_lock: _pre_buf.append(chunk.copy())
            vocal = bandpass(chunk)

            with state.lock: skip_vad = state.voice_active or (now < state.voice_override_until)
            if not skip_vad:
                vad_result = state.vad.update(vocal)
                if vad_result == 'START' and not _capturing:
                    _capturing = True; _capture_start = now
                    with _pre_buf_lock: _capture_buf = [c.copy() for c in _pre_buf]
                    _capture_buf.append(chunk.copy())
                elif vad_result in ('ACTIVE', 'START') and _capturing:
                    _capture_buf.append(chunk.copy())
                    if now - _capture_start > CAPTURE_MAX_SEC: _trigger_recognition()
                elif vad_result == 'END' and _capturing:
                    _trigger_recognition()
            elif _capturing:
                _capturing = False; _capture_buf = []
                state.vad.reset()

            if aubio_tempo(chunk)[0]:
                if now - beat_debounce > 0.2:
                    bpm = aubio_tempo.get_bpm()
                    with state.lock:
                        g = state.genre
                        if bpm < 30: bpm *= 2
                        elif bpm > 200: bpm /= 2
                        if 40 < bpm < 90 and any(x in g for x in ["Electronic","Dance","Rock","Pop"]): bpm *= 2
                        state.beat_tracker.add_beat(bpm, now)
                        state.bpm = state.beat_tracker.smoothed_bpm
                        state.beat_hit = True
                    beat_debounce = now

            with state.lock:
                t = time.time()
                overdue = (t - state.last_dance_command_time) >= state.dance_interval
                free = t > state.voice_override_until and not state.voice_active
                has_beats = len(state.beat_tracker.bpm_history) >= 3
                confident = state.beat_tracker.beat_confidence > 0.4
                esp32_free = _esp32_ready.is_set()

                if overdue and free and has_beats and confident and esp32_free:
                    bpm_val = state.beat_tracker.get_valid_bpm()
                    genre = state.genre
                    slow = ["Acoustic","Vocal","Speech","Choir","Folk","Singer","Ballad","Blues"]
                    if any(s in genre for s in slow) or bpm_val < 100:
                        state.music_speed = "SLOW"
                        move = random.choice(["DANCE_ROLL_SLOW", "DANCE_PEACOCK", "DANCE_WAVE", "DANCE_CRAWL", "DANCE_HEADBANG", "DANCE_BEG_WAVE", "DANCE_BELLY_CRAWL"])
                        print(f"SLOW {bpm_val:.0f} BPM -> {move}")
                    elif bpm_val < 130:
                        state.music_speed = "MEDIUM"
                        move = random.choice(["DANCE_TWIST", "DANCE_RIPPLE_2", "DANCE_CIRCLE", "DANCE_SALSA", "DANCE_GALLOP", "DANCE_CHASSIS_BREATHE", "DANCE_PITCH_PIVOT"])
                        print(f"MEDIUM {bpm_val:.0f} BPM -> {move}")
                    else:
                        state.music_speed = "FAST"
                        move = random.choice(["DANCE_ROLL_FAST", "DANCE_TWIST_2", "DANCE_CIRCLE_2", "DANCE_STROBE", "DANCE_PULSE", "DANCE_TWITCH", "DANCE_WORM"])
                        print(f"FAST {bpm_val:.0f} BPM -> {move}")
                    send_to_esp32(move)
                    state.last_dance_command_time = t
                    state.beat_tracker.bpm_history.clear()

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
    r = min(corner_radius, (x1-x0)//2, (y1-y0)//2)
    if r <= 0:
        draw.rectangle([x0,y0,x1,y1], fill=fill); return
    draw.rectangle([x0, y0+r, x1, y1-r], fill=fill)
    draw.rectangle([x0+r, y0, x1-r, y1], fill=fill)
    draw.pieslice([x0, y0, x0+r*2, y0+r*2], 180, 270, fill=fill)
    draw.pieslice([x1-r*2, y1-r*2, x1, y1], 0, 90, fill=fill)
    draw.pieslice([x0, y1-r*2, x0+r*2, y1], 90, 180, fill=fill)
    draw.pieslice([x1-r*2, y0, x1, y0+r*2], 270, 360, fill=fill)

def display_loop():
    os.system("amixer set Master 100% > /dev/null 2>&1")
    try:
        disp = init_display()
    except Exception as e:
        print(f"Failed to load display: {e}")
        return
        
    eye_w, eye_h = 70, 120
    lx, rx, cy = 90, 230, 120
    blink_timer = time.time()
    is_blinking = False

    while True:
        with state.lock:
            speed = state.music_speed
            beat_active = state.beat_hit
            va = state.voice_active
            cmd_t = state.command_detected_time
            bpm = state.bpm
            state.beat_hit = False

        dt = time.time() - cmd_t
        bg = (255,255,255) if dt<0.25 else (30,30,80) if dt<1.0 else (10,35,15) if va else (0,0,0)
        img = Image.new("RGB", (320,240), color=bg)
        draw = ImageDraw.Draw(img)

        if bpm > 0: draw.text((10,10), f"{bpm:.0f}", fill=(100,100,100))

        h, col, cy_r = eye_h, (0,255,255), cy
        if dt < 0.25: col, h, cy_r = (0,0,0), int(eye_h*0.4), cy-10
        elif dt < 1.0: col, h, cy_r = (0,191,255), int(eye_h*0.4), cy-10
        elif va: col, h = (0,255,100), int(eye_h*0.75)
        elif speed=="FAST": col, h = (255,50,50), eye_h+20
        elif speed=="MEDIUM": col, h = (255,150,50), eye_h+10
        elif speed=="SLOW": col, h = (150,50,255), int(eye_h*0.6)

        ew = eye_w+10 if (beat_active and not va and dt>1.0) else eye_w

        if time.time()-blink_timer > np.random.uniform(2.0,5.0):
            is_blinking = True; blink_timer = time.time()
        if is_blinking and not va and dt>1.0:
            h = 10
            if time.time()-blink_timer > 0.15: is_blinking = False

        for cx in [lx, rx]:
            draw_rounded_rect(draw, [cx-ew//2, cy_r-h//2, cx+ew//2, cy_r+h//2], corner_radius=20, fill=col)

        disp.image(img)
        time.sleep(0.03)


# ==========================================
# CLI MANUAL TESTING INTERFACE
# ==========================================
MANUAL_COMMANDS = {
    # Locomotion / Basics
    1: ("WALK_FORWARD", "Walk Forward"),
    2: ("WALK_BACKWARD", "Walk Backward"),
    3: ("TURN_LEFT", "Turn Left"),
    4: ("TURN_RIGHT", "Turn Right"),
    5: ("STAND", "Stand / Stop / Reset"),
    # Existing Slow / Medium
    6: ("DANCE_WAVE", "Dance: Wave"),
    7: ("DANCE_RIPPLE", "Dance: Ripple"),
    8: ("DANCE_PEACOCK", "Dance: Peacock"),
    9: ("DANCE_SALSA", "Dance: Salsa"),
    10: ("DANCE_TWIST", "Dance: Twist"),
    11: ("DANCE_CIRCLE", "Dance: Circle"),
    12: ("DANCE_CRAWL", "Dance: Crawl"),
    13: ("DANCE_HEADBANG", "Dance: Headbang"),
    14: ("DANCE_GALLOP", "Dance: Gallop"),
    # Existing Fast
    15: ("DANCE_ROLL_FAST", "Dance: Fast Roll"),
    16: ("DANCE_STROBE", "Dance: Strobe"),
    17: ("DANCE_PULSE", "Dance: Pulse"),
    # ====== NEW MOVEMENTS ======
    18: ("DANCE_BEG_WAVE", "NEW: Humanoid Beg & Wave"),
    19: ("DANCE_CHASSIS_BREATHE", "NEW: Sine Wave Chassis Breathe"),
    20: ("DANCE_BELLY_CRAWL", "NEW: Low-Rider Belly Crawl"),
    21: ("DANCE_PITCH_PIVOT", "NEW: Pitch & Pivot Sway"),
    22: ("DANCE_TWITCH", "NEW: High-Frequency Twitch/Shiver"),
    23: ("DANCE_WORM", "NEW: Brownian Ripple Worm"),

    # ====== NEW INDIVIDUAL LEG DIAGNOSTICS ======
    24: ("TEST_LEG_0", "DIAGNOSTIC: Test Leg 0 (Front Left)"),
    25: ("TEST_LEG_1", "DIAGNOSTIC: Test Leg 1 (Mid Left)"),
    26: ("TEST_LEG_2", "DIAGNOSTIC: Test Leg 2 (Back Left)"),
    27: ("TEST_LEG_3", "DIAGNOSTIC: Test Leg 3 (Front Right)"),
    28: ("TEST_LEG_4", "DIAGNOSTIC: Test Leg 4 (Mid Right)"),
    29: ("TEST_LEG_5", "DIAGNOSTIC: Test Leg 5 (Back Right)")
}

def manual_testing_loop():
    print("\n" + "="*50)
    print("   🤖 HEXAPOD MANUAL TESTING CLI 🤖")
    print("="*50)
    for k, v in MANUAL_COMMANDS.items():
        if k == 6: print("--- Current Dances ---")
        if k == 18: print("--- NEW Experimental Dances ---")
        print(f"  [{k:02d}] {v[1]}")
    print("\n  [ 0] EXIT SCRIPT")
    print("="*50)

    while True:
        try:
            choice = input("\nEnter move number >>> ").strip()
            if choice == '0' or choice.lower() == 'q':
                print("Exiting...")
                os._exit(0)
            
            cmd_idx = int(choice)
            if cmd_idx in MANUAL_COMMANDS:
                cmd_str = MANUAL_COMMANDS[cmd_idx][0]
                print(f" >> Sending: {cmd_str}")
                send_to_esp32(cmd_str)
                with state.lock:
                    state.command_detected_time = time.time()  # Triggers display flash
            else:
                print("Invalid selection. Try again.")
        except ValueError:
            print("Please enter a valid number.")
        except KeyboardInterrupt:
            print("\nExiting...")
            os._exit(0)

# ==========================================
# MAIN STARTUP MENU
# ==========================================
if __name__ == "__main__":
    print("\n" + "="*50)
    print("      HEXAPOD STARTUP MENU")
    print("="*50)
    print(" [1] Autonomous AI / Voice Dancer Mode (Needs Internet & Mic)")
    print(" [2] Manual SSH Testing Mode (No Internet Needed)")
    print("="*50)

    try:
        mode = input("Select mode (1 or 2): ").strip()
    except KeyboardInterrupt:
        os._exit(0)

    threading.Thread(target=esp32_reader_thread, daemon=True).start()

    if mode == '1':
        print("\nStarting AI Mode...")
        init_ai_models()
        calibrate_recognizer()
        threading.Thread(target=run_yamnet_periodically, daemon=True).start()
        threading.Thread(target=audio_listener, daemon=True).start()
        display_loop() # Blocks main thread with display

    elif mode == '2':
        print("\nStarting Testing Mode. AI is DISABLED.")
        # Start display in a background thread so we can use CLI in main thread
        threading.Thread(target=display_loop, daemon=True).start()
        manual_testing_loop() # Blocks main thread with CLI UI
    
    else:
        print("Invalid selection. Restart script.")