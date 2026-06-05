"""
hexapod_controller.py  —  AI Dancer  v3  [FIXED-3]
===================================================
Root cause (confirmed):
  PipeWire intercepts all audio APIs on this Pi.
  soundcard → needs PulseAudio daemon (not running) → null device → silence
  sounddevice → only sees "pulse" and "default" PipeWire virtual devices → silence
  arecord hw:0,0 → works perfectly (direct ALSA, bypasses PipeWire)

Fix:
  Replace soundcard/sounddevice with PyAudio using device string "hw:0,0"
  (card 0, device 0 = Google Voice HAT, confirmed by arecord -l).
  PyAudio with ALSA backend bypasses PipeWire entirely.

  ALSA_CARD / ALSA_DEVICE constants at the top of the file — change them
  if your hardware is on a different card.

All prior fixes retained (supervisor loop, silence detector, SR threshold,
stale-process guard, fuzzy matching, beat phase tracker).
"""

import sys, os, time, threading, collections, csv, random, queue, traceback
import importlib.util

# Force ALSA backend — must be set before any audio import
os.environ['SDL_AUDIODRIVER']  = 'alsa'
os.environ['AUDIODEV']         = 'hw:0,0'

# ── compatibility shim ────────────────────────────────────────────────────────
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None
sys.modules['imp'] = FakeImp()

import pyaudio
import serial
import numpy as np
import aubio
import tensorflow as tf
import tensorflow_hub as hub
import speech_recognition as sr
import pyttsx3
from scipy.signal import butter, lfilter

import board, busio, digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341

# ══════════════════════════════════════════════════════════════════════════════
# AUDIO HARDWARE CONFIG
# Change ALSA_CARD / ALSA_DEVICE if your Voice HAT is on a different card.
# Confirmed working: arecord -l showed card 0, device 0.
# ══════════════════════════════════════════════════════════════════════════════
ALSA_CARD     = 0       # from arecord -l: card 0: sndrpigooglevoi
ALSA_DEVICE   = 0       # device 0
ALSA_CHANNELS = 2       # Google Voice HAT exposes 2 input channels (confirmed by PyAudio query)
RATE          = 16000
CHUNK         = 512

def _find_pyaudio_device(pa):
    """
    Find the PyAudio device index matching ALSA_CARD/ALSA_DEVICE.
    PyAudio on ALSA names devices like "hw:CARD=sndrpigooglevoi,DEV=0"
    or just uses the card index. We print all devices and pick the best match.
    """
    n = pa.get_device_count()
    print("\n--- PyAudio input devices ---")
    best_idx      = None
    fallback_idx  = None

    for i in range(n):
        info = pa.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            print(f"  [{i}] {info['name']}  "
                  f"(inputs={info['maxInputChannels']}, "
                  f"rate={int(info['defaultSampleRate'])})")
            name = info['name'].lower()
            # Prefer exact hw:0,0 or Google Voice HAT
            if ('hw:0,0' in name
                    or 'googlevoice' in name
                    or 'google' in name
                    or 'voicehat' in name
                    or 'snd_rpi' in name
                    or 'sndrpi' in name):
                best_idx = i
            # Fallback: first real hw device (not pulse/default/pipewire)
            if (fallback_idx is None
                    and 'pulse' not in name
                    and 'default' not in name
                    and 'pipewire' not in name):
                fallback_idx = i

    print("-----------------------------\n")

    chosen = best_idx if best_idx is not None else fallback_idx
    if chosen is None:
        # Last resort: device index = ALSA_CARD (usually correct on single-card Pi)
        chosen = ALSA_CARD
        print(f"No named match — falling back to device index {chosen}")
    else:
        info = pa.get_device_info_by_index(chosen)
        print(f"Selected PyAudio device [{chosen}]: {info['name']}")

    return chosen

# ══════════════════════════════════════════════════════════════════════════════
# STALE PROCESS GUARD
# ══════════════════════════════════════════════════════════════════════════════
def _check_stale_process():
    import subprocess
    my_pid  = os.getpid()
    my_name = os.path.basename(__file__)
    try:
        out    = subprocess.check_output(["pgrep", "-f", my_name], text=True).strip().split()
        others = [int(p) for p in out if int(p) != my_pid]
        if others:
            print(
                f"\n⚠️  WARNING: another instance of {my_name} is already running "
                f"(PID {others}).\n"
                f"   Kill it first:  kill {' '.join(str(p) for p in others)}\n"
            )
    except Exception:
        pass

_check_stale_process()

# ══════════════════════════════════════════════════════════════════════════════
# SERIAL — ACK/NAK PROTOCOL
# ══════════════════════════════════════════════════════════════════════════════
print("Connecting to ESP32 over USB...")
try:
    esp32_serial = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    print("Connected on /dev/ttyUSB0")
except Exception as e:
    print(f"Failed: {e}")
    esp32_serial = None

_cmd_queue   = queue.PriorityQueue()
_esp32_ready = threading.Event()
_esp32_ready.set()

def _ack_listener():
    while True:
        if esp32_serial and esp32_serial.is_open:
            try:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                if line == "READY":
                    _esp32_ready.set()
            except Exception:
                pass
        time.sleep(0.001)

threading.Thread(target=_ack_listener, daemon=True).start()

def send_to_esp32(command, priority=1):
    if priority > 0 and not _esp32_ready.is_set():
        print(f"ESP32 busy, skipping beat-sync: {command}")
        return
    _cmd_queue.put((priority, command))

def _cmd_sender():
    while True:
        try:
            pri, cmd = _cmd_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        timeout = 15.0 if pri == 0 else 5.0
        if not _esp32_ready.wait(timeout=timeout):
            print(f"ESP32 not ready after {timeout:.0f}s, dropping: {cmd}")
            continue
        _esp32_ready.clear()
        if esp32_serial and esp32_serial.is_open:
            try:
                esp32_serial.write((cmd + "\n").encode('utf-8'))
                print(f"Sent: {cmd}")
            except Exception as e:
                print(f"Send failed: {e}")
                _esp32_ready.set()

threading.Thread(target=_cmd_sender, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# PRE-COMPUTED BANDPASS FILTER
# ══════════════════════════════════════════════════════════════════════════════
def _make_bandpass(lo, hi, fs, order=4):
    nyq  = 0.5 * fs
    b, a = butter(order, [lo/nyq, hi/nyq], btype='band')
    return b, a

_BP_B, _BP_A = _make_bandpass(300, 3400, RATE, order=4)

def bandpass(data):
    return np.ascontiguousarray(lfilter(_BP_B, _BP_A, data), dtype=np.float32)

# ══════════════════════════════════════════════════════════════════════════════
# CIRCULAR AUDIO BUFFER
# ══════════════════════════════════════════════════════════════════════════════
class CircularBuffer:
    def __init__(self, capacity):
        self._buf  = np.zeros(capacity, dtype=np.float32)
        self._cap  = capacity
        self._head = 0

    def write(self, chunk):
        n   = len(chunk)
        end = (self._head + n) % self._cap
        if end > self._head:
            self._buf[self._head:end] = chunk
        else:
            split = self._cap - self._head
            self._buf[self._head:]  = chunk[:split]
            self._buf[:end]         = chunk[split:]
        self._head = end

    def read(self, n=None):
        n     = n or self._cap
        n     = min(n, self._cap)
        end   = self._head
        start = (end - n) % self._cap
        if start < end:
            return self._buf[start:end].copy()
        return np.concatenate([self._buf[start:], self._buf[:end]])

_audio_ring = CircularBuffer(RATE * 3)
_yamnet_win  = CircularBuffer(RATE)

# ══════════════════════════════════════════════════════════════════════════════
# 3-STAGE VOICE ACTIVITY DETECTOR
# ══════════════════════════════════════════════════════════════════════════════
class VAD:
    NOISE_ALPHA       = 0.97
    ENERGY_MULTIPLIER = 3.5
    ZCR_MIN           = 0.04
    ZCR_MAX           = 0.35
    BAND_RATIO_MIN    = 0.55
    CONFIRM_CHUNKS    = 5
    SILENCE_CHUNKS    = 18
    MIN_NOISE_FLOOR   = 0.001

    _freqs     = np.fft.rfftfreq(CHUNK, 1.0 / RATE)
    _voice_idx = (_freqs >= 300) & (_freqs <= 3400)

    def __init__(self):
        self.noise_floor         = 0.02
        self.consecutive_voice   = 0
        self.consecutive_silence = 0
        self.in_speech           = False

    def _zcr(self, chunk):
        return np.sum(np.diff(np.sign(chunk)) != 0) / len(chunk)

    def _band_ratio(self, chunk):
        spec    = np.abs(np.fft.rfft(chunk))
        total_e = np.sum(spec ** 2)
        if total_e < 1e-10:
            return 0.0
        return float(np.sum(spec[self._voice_idx] ** 2) / total_e)

    def update(self, chunk):
        energy    = float(np.sqrt(np.mean(chunk ** 2)))
        zcr       = self._zcr(chunk)
        energy_ok = energy > (self.noise_floor * self.ENERGY_MULTIPLIER)
        zcr_ok    = self.ZCR_MIN < zcr < self.ZCR_MAX
        band_ok   = (self._band_ratio(chunk) > self.BAND_RATIO_MIN
                     if (energy_ok and zcr_ok) else False)
        is_voice  = energy_ok and zcr_ok and band_ok

        if not is_voice:
            self.noise_floor = max(
                self.MIN_NOISE_FLOOR,
                self.NOISE_ALPHA * self.noise_floor
                + (1.0 - self.NOISE_ALPHA) * energy
            )

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

# ══════════════════════════════════════════════════════════════════════════════
# BEAT PHASE TRACKER
# ══════════════════════════════════════════════════════════════════════════════
class BeatPhaseTracker:
    def __init__(self):
        self.bpm_history      = collections.deque(maxlen=20)
        self.beat_times       = collections.deque(maxlen=16)
        self.smoothed_bpm     = 0.0
        self.beat_interval    = 0.5
        self.next_beat_time   = 0.0
        self.beat_confidence  = 0.0
        self._last_genre      = ""
        self._phase_error_avg = 0.0

    def add_beat(self, bpm, t):
        if self.beat_times:
            phase_err = t - self.next_beat_time
            if len(self.beat_times) >= 2:
                measured_interval  = t - self.beat_times[-1]
                self.beat_interval = (0.3 * measured_interval
                                      + 0.7 * self.beat_interval)
            self._phase_error_avg = (0.8 * self._phase_error_avg
                                     + 0.2 * abs(phase_err))
            max_err = self.beat_interval * 0.5
            self.beat_confidence = (
                max(0.0, 1.0 - self._phase_error_avg / max_err)
                if max_err > 0 else 0.5
            )
        else:
            self.beat_confidence = 0.7
            self.beat_interval   = 60.0 / bpm if bpm > 0 else 0.5

        self.bpm_history.append(bpm)
        self.beat_times.append(t)
        self.smoothed_bpm   = 0.3 * bpm + 0.7 * self.smoothed_bpm
        self.next_beat_time = t + self.beat_interval

    def predict_next_beat(self):
        now = time.time()
        if self.next_beat_time > 0 and now > self.next_beat_time + self.beat_interval:
            skipped = int((now - self.next_beat_time) / self.beat_interval)
            self.next_beat_time += skipped * self.beat_interval

    def is_on_beat(self, window=0.06):
        return abs(time.time() - self.next_beat_time) < window

    def get_valid_bpm(self):
        valid = [b for b in self.bpm_history if 50 < b < 200]
        return float(np.median(valid)) if valid else self.smoothed_bpm

    def soft_clear(self):
        keep    = int(len(self.bpm_history) * 0.6)
        trimmed = list(self.bpm_history)[-keep:]
        self.bpm_history.clear()
        self.bpm_history.extend(trimmed)

# ══════════════════════════════════════════════════════════════════════════════
# ROBOT STATE
# ══════════════════════════════════════════════════════════════════════════════
class RobotState:
    def __init__(self):
        self.bpm                     = 0.0
        self.genre                   = "Listening..."
        self.genre_scores            = np.zeros(521)
        self.beat_hit                = False
        self.music_speed             = "IDLE"
        self.voice_active            = False
        self.command_detected_time   = 0.0
        self.beat_tracker            = BeatPhaseTracker()
        self.vad                     = VAD()
        self.last_dance_command_time = time.time()
        self.voice_override_until    = 0.0
        self.last_dance_move         = ""
        self.lock                    = threading.Lock()

state = RobotState()

# ══════════════════════════════════════════════════════════════════════════════
# PRE-BUFFER + CAPTURE BUFFER
# ══════════════════════════════════════════════════════════════════════════════
PRE_BUFFER_SEC  = 0.7
CAPTURE_MAX_SEC = 3.0

_pre_buf_lock  = threading.Lock()
_pre_buf       = collections.deque(maxlen=int(RATE * PRE_BUFFER_SEC / CHUNK))
_capture_buf   : list = []
_capturing     = False
_capture_start = 0.0

# ══════════════════════════════════════════════════════════════════════════════
# YAMNET
# ══════════════════════════════════════════════════════════════════════════════
yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')

def get_class_names():
    path  = yamnet_model.class_map_path().numpy().decode('utf-8')
    names = []
    with tf.io.gfile.GFile(path) as f:
        for row in csv.DictReader(f):
            names.append(row['display_name'])
    return names

YAMNET_CLASSES = get_class_names()

def run_yamnet_periodically():
    alpha = 0.4
    while True:
        time.sleep(1.5)
        snap = _yamnet_win.read(RATE).astype(np.float32)
        try:
            scores, _, _ = yamnet_model(snap)
            mean_scores  = np.mean(scores.numpy(), axis=0)
            with state.lock:
                state.genre_scores = (alpha * mean_scores
                                      + (1 - alpha) * state.genre_scores)
                top = int(np.argmax(state.genre_scores))
                if "CMD" not in state.genre:
                    state.genre = YAMNET_CLASSES[top]
        except Exception as e:
            print(f"YAMNet error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# SPEECH RECOGNITION
# ══════════════════════════════════════════════════════════════════════════════
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = False

def calibrate_recognizer():
    print("Calibrating mic — VAD adaptive threshold active (no separate capture)...")
    recognizer.energy_threshold = 300
    print("SR threshold=300  (VAD will adapt from live audio every 60 s)")

def _update_sr_threshold_from_vad():
    last_threshold = recognizer.energy_threshold
    while True:
        time.sleep(60)
        with state.lock:
            if state.voice_active:
                continue
            noise = state.vad.noise_floor
        if noise <= state.vad.MIN_NOISE_FLOOR * 1.05:
            print("SR threshold NOT updated — mic silent (noise at floor). "
                  "Check mic connection.")
            continue
        new_threshold = max(300, noise * 8 * 32767)
        if abs(new_threshold - last_threshold) / max(last_threshold, 1) > 0.10:
            print(f"SR threshold updated: noise={noise:.4f}  "
                  f"threshold={new_threshold:.0f}")
            last_threshold = new_threshold
        recognizer.energy_threshold = new_threshold

threading.Thread(target=_update_sr_threshold_from_vad, daemon=True).start()

def say_phrase_offline(text):
    def _speak():
        engine = None
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 145)
            engine.setProperty('volume', 1.0)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception:
            pass
        finally:
            engine = None
    threading.Thread(target=_speak, daemon=True).start()

# ── Fuzzy keyword matching ────────────────────────────────────────────────────
def _edit_distance(a, b):
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp  = dp[j]
            dp[j] = prev if a[i-1] == b[j-1] else 1 + min(prev, dp[j], dp[j-1])
            prev  = temp
    return dp[n]

def _fuzzy_match(word, keyword, max_dist=2):
    max_d = min(max_dist, max(1, len(keyword) // 3))
    return _edit_distance(word, keyword) <= max_d

def _text_matches_keywords(text, keywords):
    words = text.lower().split()
    for kw in keywords:
        if kw in text:
            return True
        for w in words:
            if _fuzzy_match(w, kw):
                return True
    return False

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
    (["ripple"],                                "DANCE_RIPPLE",   "doing the ripple"),
    (["peacock"],                               "DANCE_PEACOCK",  "striking a pose"),
    (["salsa"],                                 "DANCE_SALSA",    "salsa time"),
]

def _normalize_audio(audio_float32):
    peak = np.max(np.abs(audio_float32))
    return audio_float32 / peak * 0.9 if peak > 0.01 else audio_float32

def _normalize_chunk_rms(chunk, target_rms=0.05):
    rms = float(np.sqrt(np.mean(chunk ** 2)))
    return chunk * (target_rms / rms) if rms > 1e-6 else chunk

def process_voice_command(audio_bytes):
    print("Processing voice command...")
    for attempt in range(3):
        try:
            audio_data   = sr.AudioData(audio_bytes, RATE, 2)
            result       = recognizer.recognize_google(
                audio_data, language='en-US', show_all=True
            )
            if not result or 'alternative' not in result:
                raise sr.UnknownValueError()
            alternatives = [alt['transcript'].lower()
                            for alt in result['alternative']]
            print(f"Alternatives: {alternatives}")

            matched = False
            for text in alternatives:
                for keywords, cmd, phrase in COMMANDS:
                    if _text_matches_keywords(text, keywords):
                        send_to_esp32(cmd, priority=0)
                        say_phrase_offline(phrase)
                        with state.lock:
                            state.command_detected_time = time.time()
                            state.voice_override_until  = time.time() + 12.0
                            state.beat_tracker.soft_clear()
                        print(f"Executed (from '{text}'): {cmd}")
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                print(f"No command matched. Heard: {alternatives}")
            break
        except sr.UnknownValueError:
            print(f"Could not understand (attempt {attempt+1}/3)")
            if attempt < 2:
                time.sleep(0.08)
        except sr.RequestError as e:
            print(f"Google API error: {e}")
            break
        except Exception as e:
            print(f"Error: {e}")
            break

    with state.lock:
        state.voice_active = False
    state.vad.reset()

def _trigger_recognition():
    global _capturing, _capture_buf
    if not _capture_buf:
        _capturing = False
        return
    audio       = np.concatenate(_capture_buf).astype(np.float32)
    audio       = _normalize_audio(audio)
    audio_bytes = (audio * 32767).astype(np.int16).tobytes()
    with state.lock:
        state.voice_active = True
    _capturing   = False
    _capture_buf = []
    threading.Thread(target=process_voice_command,
                     args=(audio_bytes,), daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# DANCE SELECTION
# ══════════════════════════════════════════════════════════════════════════════
SLOW_GENRES   = ["Acoustic","Vocal","Speech","Choir","Folk","Singer","Ballad","Blues"]
MEDIUM_GENRES = ["Pop","Indie","R&B","Soul","Country"]
FAST_GENRES   = ["Electronic","Dance","Rock","House","Techno","Drum","Bass","Hip"]

DANCE_MATRIX = {
    ("SLOW",   "slow"):   ["DANCE_ROLL_SLOW","DANCE_PEACOCK","DANCE_WAVE",    "DANCE_RIPPLE"],
    ("SLOW",   "medium"): ["DANCE_ROLL_SLOW","DANCE_PEACOCK","DANCE_RIPPLE"],
    ("SLOW",   "fast"):   ["DANCE_ROLL_SLOW","DANCE_WAVE",   "DANCE_RIPPLE"],
    ("SLOW",   "other"):  ["DANCE_ROLL_SLOW","DANCE_PEACOCK","DANCE_WAVE"],
    ("MEDIUM", "slow"):   ["DANCE_SALSA",    "DANCE_RIPPLE_2","DANCE_CIRCLE"],
    ("MEDIUM", "medium"): ["DANCE_TWIST",    "DANCE_SALSA",  "DANCE_CIRCLE", "DANCE_RIPPLE_2"],
    ("MEDIUM", "fast"):   ["DANCE_TWIST",    "DANCE_CIRCLE", "DANCE_SALSA"],
    ("MEDIUM", "other"):  ["DANCE_TWIST",    "DANCE_RIPPLE_2","DANCE_CIRCLE"],
    ("FAST",   "slow"):   ["DANCE_ROLL_FAST","DANCE_TWIST_2","DANCE_CIRCLE_2"],
    ("FAST",   "medium"): ["DANCE_ROLL_FAST","DANCE_CIRCLE_2","DANCE_TWIST_2"],
    ("FAST",   "fast"):   ["DANCE_ROLL_FAST","DANCE_TWIST_2","DANCE_CIRCLE_2","DANCE_SALSA"],
    ("FAST",   "other"):  ["DANCE_ROLL_FAST","DANCE_CIRCLE_2","DANCE_TWIST_2"],
}

def _genre_type(genre):
    if any(s in genre for s in SLOW_GENRES):   return "slow"
    if any(s in genre for s in MEDIUM_GENRES): return "medium"
    if any(s in genre for s in FAST_GENRES):   return "fast"
    return "other"

def pick_dance(speed, genre, last_move):
    key  = (speed, _genre_type(genre))
    pool = DANCE_MATRIX.get(key, ["DANCE_CIRCLE"])
    opts = [d for d in pool if d != last_move] or pool
    return random.choice(opts)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN AUDIO LISTENER  —  PyAudio → ALSA hw:0,0  (bypasses PipeWire entirely)
# ══════════════════════════════════════════════════════════════════════════════
MIC_SILENCE_THRESHOLD = 1e-5
MIC_SILENCE_CHUNKS    = 50     # ~1.6 s

def audio_listener():
    """Supervisor: opens PyAudio; restarts on silence or crash."""
    while True:
        print("audio_listener: opening microphone (PyAudio/ALSA)...")
        try:
            _run_audio_loop()
        except Exception:
            print("audio_listener: CRASHED — restarting in 2s...")
            traceback.print_exc()
        time.sleep(2.0)
        print("audio_listener: restarting...")

def _run_audio_loop():
    global _capture_buf, _capturing, _capture_start

    aubio_tempo   = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)
    beat_debounce = time.time()
    _rms_window   = collections.deque(maxlen=MIC_SILENCE_CHUNKS)
    _loop_start   = time.time()

    pa         = pyaudio.PyAudio()
    device_idx = _find_pyaudio_device(pa)

    print(f"\n=== AI DANCER v3 [FIXED-3] — PyAudio ALSA direct, "
          f"device={device_idx} ===\n")

    stream = pa.open(
        format=pyaudio.paFloat32,
        channels=ALSA_CHANNELS,   # 2 — Google Voice HAT requires stereo open
        rate=RATE,
        input=True,
        input_device_index=device_idx,
        frames_per_buffer=CHUNK,
    )

    try:
        while True:
            raw    = stream.read(CHUNK, exception_on_overflow=False)
            # Stereo -> mono: CHUNK*ALSA_CHANNELS interleaved samples -> mean across channels
            stereo = np.frombuffer(raw, dtype=np.float32).reshape(-1, ALSA_CHANNELS)
            chunk  = stereo.mean(axis=1)   # shape (CHUNK,) mono float32
            now    = time.time()

            # Silence / stale-device detector
            chunk_rms = float(np.sqrt(np.mean(chunk ** 2)))
            _rms_window.append(chunk_rms)
            if (len(_rms_window) == MIC_SILENCE_CHUNKS
                    and now - _loop_start > 3.0):
                mean_rms = float(np.mean(_rms_window))
                if mean_rms < MIC_SILENCE_THRESHOLD:
                    print(
                        f"audio_listener: mic still silent after PyAudio switch "
                        f"(mean RMS={mean_rms:.2e}).\n"
                        "  Check: is the Google Voice HAT physically connected?\n"
                        "  Run:   arecord -d 3 -f S16_LE -r 16000 -c 1 /tmp/t.wav "
                        "&& aplay /tmp/t.wav"
                    )
                    return   # supervisor restarts

            # Ring buffers
            _audio_ring.write(chunk)
            _yamnet_win.write(chunk)

            with _pre_buf_lock:
                _pre_buf.append(chunk.copy())

            vocal = bandpass(chunk)

            with state.lock:
                skip_vad = state.voice_active or (now < state.voice_override_until)

            # VAD
            if not skip_vad:
                vad_result = state.vad.update(vocal)

                if vad_result == 'START' and not _capturing:
                    _capturing     = True
                    _capture_start = now
                    with _pre_buf_lock:
                        _capture_buf = [_normalize_chunk_rms(c.copy())
                                        for c in _pre_buf]
                    _capture_buf.append(_normalize_chunk_rms(chunk.copy()))
                    print("Voice start")

                elif vad_result in ('ACTIVE', 'START') and _capturing:
                    _capture_buf.append(_normalize_chunk_rms(chunk.copy()))
                    if now - _capture_start > CAPTURE_MAX_SEC:
                        print("Max capture length, sending...")
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
                        if (40 < bpm < 90 and
                                any(x in g for x in
                                    ["Electronic","Dance","Rock","Pop"])):
                            bpm *= 2
                        state.beat_tracker.add_beat(bpm, now)
                        state.bpm      = state.beat_tracker.smoothed_bpm
                        state.beat_hit = True
                    beat_debounce = now

            # Beat-phase choreography
            with state.lock:
                state.beat_tracker.predict_next_beat()
                on_beat    = state.beat_tracker.is_on_beat(window=0.06)
                esp32_free = _esp32_ready.is_set()
                free       = (now > state.voice_override_until
                              and not state.voice_active
                              and esp32_free)
                has_beats  = len(state.beat_tracker.bpm_history) >= 3
                confident  = state.beat_tracker.beat_confidence > 0.4
                beat_iv    = state.beat_tracker.beat_interval
                dance_iv   = max(2.0, beat_iv * 4)
                overdue    = (now - state.last_dance_command_time) >= dance_iv

                if on_beat and overdue and free and has_beats and confident:
                    bpm_val = state.beat_tracker.get_valid_bpm()
                    genre   = state.genre
                    if any(s in genre for s in SLOW_GENRES) or bpm_val < 100:
                        speed = "SLOW"
                    elif bpm_val < 130:
                        speed = "MEDIUM"
                    else:
                        speed = "FAST"

                    state.music_speed = speed
                    move = pick_dance(speed, genre, state.last_dance_move)
                    state.last_dance_move         = move
                    state.last_dance_command_time = now
                    state.beat_tracker.soft_clear()

                    print(f"{speed} {bpm_val:.0f} BPM [{genre}] → {move}")
                    send_to_esp32(move, priority=1)

    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════════════════════
DISPLAY_CS_PIN  = board.CE0
DISPLAY_DC_PIN  = board.D24
DISPLAY_RST_PIN = board.D25

def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(
        spi,
        cs=digitalio.DigitalInOut(DISPLAY_CS_PIN),
        dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
        rst=digitalio.DigitalInOut(DISPLAY_RST_PIN),
        rotation=90, baudrate=24000000
    )

def draw_rounded_rect(draw, xy, corner_radius, fill):
    x0, y0, x1, y1 = xy
    r = min(corner_radius, (x1-x0)//2, (y1-y0)//2)
    if r <= 0:
        draw.rectangle([x0,y0,x1,y1], fill=fill)
        return
    draw.rectangle([x0, y0+r, x1, y1-r], fill=fill)
    draw.rectangle([x0+r, y0, x1-r, y1], fill=fill)
    draw.pieslice([x0,     y0,     x0+r*2, y0+r*2], 180, 270, fill=fill)
    draw.pieslice([x1-r*2, y1-r*2, x1,     y1    ],   0,  90, fill=fill)
    draw.pieslice([x0,     y1-r*2, x0+r*2, y1    ],  90, 180, fill=fill)
    draw.pieslice([x1-r*2, y0,     x1,     y0+r*2], 270, 360, fill=fill)

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

        dt  = time.time() - cmd_t
        bg  = ((255,255,255) if dt < 0.25 else
               (30,30,80)    if dt < 1.0  else
               (10,35,15)    if va        else (0,0,0))
        img  = Image.new("RGB", (320,240), color=bg)
        draw = ImageDraw.Draw(img)

        if bpm > 0:
            draw.text((10,10), f"{bpm:.0f}", fill=(100,100,100))

        h    = eye_h
        col  = (0, 255, 255)
        cy_r = cy

        if   dt < 0.25: col, h, cy_r = (0,0,0),      int(eye_h*0.4), cy-10
        elif dt < 1.0:  col, h, cy_r = (0,191,255),   int(eye_h*0.4), cy-10
        elif va:        col, h       = (0,255,100),    int(eye_h*0.75)
        elif speed == "FAST":   col, h = (255,50,50),  eye_h+20
        elif speed == "MEDIUM": col, h = (255,150,50), eye_h+10
        elif speed == "SLOW":   col, h = (150,50,255), int(eye_h*0.6)

        ew = eye_w+10 if (beat_active and not va and dt > 1.0) else eye_w

        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True
            blink_timer = time.time()
        if is_blinking and not va and dt > 1.0:
            h = 10
            if time.time() - blink_timer > 0.15:
                is_blinking = False

        for cx in [lx, rx]:
            draw_rounded_rect(draw,
                [cx-ew//2, cy_r-h//2, cx+ew//2, cy_r+h//2],
                corner_radius=20, fill=col)

        disp.image(img)
        time.sleep(0.03)

# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════
calibrate_recognizer()
threading.Thread(target=run_yamnet_periodically, daemon=True).start()
threading.Thread(target=audio_listener,          daemon=True).start()
display_loop()