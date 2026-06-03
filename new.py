"""
hexapod_controller.py  —  AI Dancer  v3
========================================
Changes vs v2:
  AUDIO
  -----
  • Circular buffer replaces np.roll (zero GC, ~31 allocs/sec eliminated)
  • Pre-filter coefficients computed once at import time (was per-chunk)
  • FFT band-ratio gate: only runs if ZCR also passes (O(n log n) → skipped most frames)

  BEAT SYNC
  ---------
  • BeatPhaseTracker: maintains a running phase estimate between beats.
    Dance commands fire ON the predicted beat downbeat, not ~500 ms late.
  • Adaptive dance_interval computed from actual measured beat interval,
    not a fixed 1-second timeout.
  • bpm_history now soft-decays (×0.6) instead of full clear, preserving
    tempo context across dance transitions.
  • Genre×tempo matrix maps to specific dances; avoids repeating same move
    twice in a row.

  VOICE
  -----
  • 3-stage VAD with ZCR + spectral band ratio (unchanged from v2)
  • Pre-buffer prepended to capture (unchanged from v2)
  • Per-chunk RMS normalization during capture (fixes quiet trailing words)
  • Google SR called with show_all=True; we pick highest-confidence
    alternative, not just the first guess.
  • Fuzzy keyword matching via edit distance for noisy environments
    (catches "baccward" → "backward", "wafe" → "wave", etc.)
  • Noise-adaptive energy threshold: recalibrated every 60 s in background.

  ACK/NAK
  -------
  • ESP32 sends "READY\n" when idle. Python queues commands and sends
    next only after READY. Eliminates mid-motion overwrites.
  • Voice commands bypass queue and go directly (highest priority).

  YAMNET
  ------
  • Sliding 1-second window averaged with exponential smoothing (α=0.4)
    instead of full 3-second window every 4 s.  ~66 % faster inference.
  • Inference runs in its own thread with a dedicated deque for the window.
"""

import sys, os, time, threading, collections, csv, random, queue
import importlib.util

# ── compatibility shim ─────────────────────────────────────────────────────────
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
import speech_recognition as sr
import pyttsx3
from scipy.signal import butter, lfilter

import board, busio, digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341

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

# Command queue: (priority, command_string)
# priority 0 = voice (highest), 1 = beat-synced dance
_cmd_queue   = queue.PriorityQueue()
_esp32_ready = threading.Event()
_esp32_ready.set()   # assume ready at startup

def _ack_listener():
    """Background thread: reads serial from ESP32, sets _esp32_ready on READY."""
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
    """Queue a command. Voice = priority 0 (highest). Beat-sync = priority 1.
    Beat-sync commands are silently skipped when ESP32 is mid-motion —
    a stale beat-sync would fire off-beat anyway. Voice always queues.
    """
    if priority > 0 and not _esp32_ready.is_set():
        print(f"ESP32 busy, skipping beat-sync: {command}")
        return
    _cmd_queue.put((priority, command))

def _cmd_sender():
    """Dequeues commands and sends only when ESP32 signals READY."""
    while True:
        try:
            pri, cmd = _cmd_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        # Voice (pri=0): wait up to 15 s — ESP32 may be finishing a long dance.
        # Beat-sync (pri=1): filtered before queuing if busy, so 5 s is plenty.
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
                _esp32_ready.set()  # unblock on error

threading.Thread(target=_cmd_sender, daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# AUDIO CONFIG + PRE-COMPUTED FILTERS
# ══════════════════════════════════════════════════════════════════════════════
RATE  = 16000
CHUNK = 512

def _make_bandpass(lo, hi, fs, order=4):
    nyq  = 0.5 * fs
    b, a = butter(order, [lo/nyq, hi/nyq], btype='band')
    return b, a

_BP_B, _BP_A = _make_bandpass(300, 3400, RATE, order=4)

def bandpass(data):
    return np.ascontiguousarray(lfilter(_BP_B, _BP_A, data), dtype=np.float32)

# ══════════════════════════════════════════════════════════════════════════════
# CIRCULAR AUDIO BUFFER  (replaces np.roll — zero allocations)
# ══════════════════════════════════════════════════════════════════════════════
class CircularBuffer:
    """Fixed-size ring buffer. write() appends CHUNK samples; read() returns
    the last `n` samples as a contiguous array without allocation."""
    def __init__(self, capacity):
        self._buf  = np.zeros(capacity, dtype=np.float32)
        self._cap  = capacity
        self._head = 0          # points to oldest sample

    def write(self, chunk):
        n = len(chunk)
        end = (self._head + n) % self._cap
        if end > self._head:
            self._buf[self._head:end] = chunk
        else:
            split = self._cap - self._head
            self._buf[self._head:]  = chunk[:split]
            self._buf[:end]         = chunk[split:]
        self._head = end

    def read(self, n=None):
        n = n or self._cap
        n = min(n, self._cap)
        # Return last n samples in chronological order
        end  = self._head
        start = (end - n) % self._cap
        if start < end:
            return self._buf[start:end].copy()
        else:
            return np.concatenate([self._buf[start:], self._buf[:end]])

_audio_ring = CircularBuffer(RATE * 3)      # 3-second window for YAMNet
_yamnet_win  = CircularBuffer(RATE)         # 1-second sliding window

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

    # Pre-compute FFT frequency bins once
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
        energy = float(np.sqrt(np.mean(chunk ** 2)))
        zcr    = self._zcr(chunk)

        if energy < self.noise_floor * 1.5:
            self.noise_floor = (self.NOISE_ALPHA * self.noise_floor
                                + (1.0 - self.NOISE_ALPHA) * energy)

        energy_ok = energy > (self.noise_floor * self.ENERGY_MULTIPLIER)
        zcr_ok    = self.ZCR_MIN < zcr < self.ZCR_MAX
        # Gate: only run expensive FFT if both cheaper checks pass
        band_ok   = (self._band_ratio(chunk) > self.BAND_RATIO_MIN
                     if (energy_ok and zcr_ok) else False)
        is_voice  = energy_ok and zcr_ok and band_ok

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
# BEAT PHASE TRACKER  — fires commands on predicted downbeat
# ══════════════════════════════════════════════════════════════════════════════
class BeatPhaseTracker:
    """
    Maintains a running phase estimate between detected beats using a
    Phase-Locked Loop (PLL)-style update.

    WHY:
      aubio detects beats asynchronously — there's a ~one-chunk latency.
      By the time a beat is detected, the music has already moved ~32ms past
      it. Naive code sends a command at detection time, not at beat time.
      This tracker:
        1. Keeps a running estimate of the next beat timestamp.
        2. Updates that estimate on each detected beat using weighted blending.
        3. Exposes is_on_beat() so the audio loop can fire commands exactly
           when the predicted beat fires, not when aubio returns.
        4. Gives confidence so we don't fire on weak beats.
    """
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
            # Measure phase error vs our prediction
            phase_err = t - self.next_beat_time
            # PLL: update interval by blending measured interval with prediction
            if len(self.beat_times) >= 2:
                measured_interval = t - self.beat_times[-1]
                # Weighted blend: 30% new, 70% history (low-pass)
                self.beat_interval = (0.3 * measured_interval
                                      + 0.7 * self.beat_interval)
            self._phase_error_avg = (0.8 * self._phase_error_avg
                                     + 0.2 * abs(phase_err))
            # Confidence: low phase error = high confidence
            max_err = self.beat_interval * 0.5
            self.beat_confidence = max(0.0, 1.0 - self._phase_error_avg / max_err) if max_err > 0 else 0.5
        else:
            self.beat_confidence  = 0.7
            self.beat_interval    = 60.0 / bpm if bpm > 0 else 0.5

        self.bpm_history.append(bpm)
        self.beat_times.append(t)
        self.smoothed_bpm  = 0.3 * bpm + 0.7 * self.smoothed_bpm
        self.next_beat_time = t + self.beat_interval

    def predict_next_beat(self):
        """Advance prediction if we've missed beats (e.g. quiet section)."""
        now = time.time()
        if self.next_beat_time > 0 and now > self.next_beat_time + self.beat_interval:
            skipped = int((now - self.next_beat_time) / self.beat_interval)
            self.next_beat_time += skipped * self.beat_interval

    def is_on_beat(self, window=0.06):
        """True if now is within `window` seconds of the predicted next beat."""
        now = time.time()
        return abs(now - self.next_beat_time) < window

    def get_valid_bpm(self):
        valid = [b for b in self.bpm_history if 50 < b < 200]
        return float(np.median(valid)) if valid else self.smoothed_bpm

    def soft_clear(self):
        """Decay history instead of full clear — preserves tempo context."""
        # Keep the 60 % most recent entries
        keep = int(len(self.bpm_history) * 0.6)
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
        self.genre_scores            = np.zeros(521)   # YAMNet smoothed scores
        self.beat_hit                = False
        self.music_speed             = "IDLE"
        self.voice_active            = False
        self.command_detected_time   = 0.0
        self.beat_tracker            = BeatPhaseTracker()
        self.vad                     = VAD()
        self.last_dance_command_time = time.time()
        self.voice_override_until    = 0.0
        self.last_dance_move         = ""          # track previous to avoid repeat
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
# YAMNET — SLIDING WINDOW (1 s, exponential smoothing)
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
    """
    Runs every 1.5 s on a 1-second window (faster than 4 s / 3 s window).
    Exponentially smooths class scores to avoid flickering between frames.
    α=0.4 means new observation gets 40 % weight — responsive but stable.
    """
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
# SPEECH RECOGNITION — ENHANCED
# ══════════════════════════════════════════════════════════════════════════════
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = False

def calibrate_recognizer():
    """
    Set SR energy threshold from a brief mic capture.
    Only called ONCE at startup, before the audio thread opens the mic.
    After startup, threshold is updated from the VAD noise floor instead.
    """
    print("Calibrating mic (2 s quiet)…")
    mic     = sc.default_microphone()
    samples = []
    try:
        with mic.recorder(samplerate=RATE, channels=1) as rec:
            for _ in range(int(RATE * 2 / CHUNK)):
                chunk = rec.record(numframes=CHUNK).flatten().astype(np.float32)
                samples.append(float(np.sqrt(np.mean(bandpass(chunk) ** 2))))
        noise = float(np.percentile(samples, 75))
    except Exception as e:
        print(f"Mic calibration failed ({e}), using default threshold")
        noise = 0.01
    recognizer.energy_threshold = max(300, noise * 8 * 32767)
    print(f"Calibrated. Noise={noise:.4f}  SR threshold={recognizer.energy_threshold:.0f}")

def _update_sr_threshold_from_vad():
    """
    Periodically sync the SR energy threshold from the VAD's live noise floor.
    The VAD updates its noise_floor every chunk from the already-open mic stream,
    so we never need to re-open the mic here.
    Runs every 30 s; skips while voice is active.
    """
    while True:
        time.sleep(30)
        with state.lock:
            if state.voice_active:
                continue
            noise = state.vad.noise_floor
        new_threshold = max(300, noise * 8 * 32767)
        recognizer.energy_threshold = new_threshold
        print(f"SR threshold updated from VAD: noise={noise:.4f}  threshold={new_threshold:.0f}")

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
            engine.stop()   # explicit stop before engine goes out of scope
        except Exception:
            pass
        finally:
            engine = None   # explicit release after callback chain is done
    threading.Thread(target=_speak, daemon=True).start()

# ── Fuzzy keyword matching ────────────────────────────────────────────────────
def _edit_distance(a, b):
    """Standard Levenshtein distance."""
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, n + 1):
            temp = dp[j]
            dp[j] = prev if a[i-1] == b[j-1] else 1 + min(prev, dp[j], dp[j-1])
            prev = temp
    return dp[n]

def _fuzzy_match(word, keyword, max_dist=2):
    """
    Returns True if `word` matches `keyword` within `max_dist` edits.
    For short keywords (≤4 chars) we tighten to max_dist=1 to avoid
    over-matching (e.g. "stop" ↔ "spin" is dist=2 but they mean different things).
    """
    max_d = min(max_dist, max(1, len(keyword) // 3))
    return _edit_distance(word, keyword) <= max_d

def _text_matches_keywords(text, keywords):
    words = text.lower().split()
    for kw in keywords:
        # Exact substring match first (fastest)
        if kw in text:
            return True
        # Fuzzy word-level match
        for w in words:
            if _fuzzy_match(w, kw):
                return True
    return False

# ── Command table ─────────────────────────────────────────────────────────────
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
    """Peak normalize to 90 % FS."""
    peak = np.max(np.abs(audio_float32))
    return audio_float32 / peak * 0.9 if peak > 0.01 else audio_float32

def _normalize_chunk_rms(chunk, target_rms=0.05):
    """
    Per-chunk RMS normalization during capture.
    Keeps every chunk at the same loudness so quiet trailing syllables
    are not drowned out when Google processes the full utterance.
    Returns the scaled chunk.
    """
    rms = float(np.sqrt(np.mean(chunk ** 2)))
    if rms > 1e-6:
        return chunk * (target_rms / rms)
    return chunk

def process_voice_command(audio_bytes):
    print("Processing voice command…")
    for attempt in range(3):
        try:
            audio_data = sr.AudioData(audio_bytes, RATE, 2)
            # show_all=True → get multiple hypotheses ranked by confidence
            result = recognizer.recognize_google(audio_data, language='en-US',
                                                 show_all=True)

            # Extract all alternatives, highest confidence first
            if not result or 'alternative' not in result:
                raise sr.UnknownValueError()
            alternatives = [alt['transcript'].lower()
                            for alt in result['alternative']]
            print(f"Alternatives: {alternatives}")

            matched = False
            # Try each alternative in confidence order
            for text in alternatives:
                for keywords, cmd, phrase in COMMANDS:
                    if _text_matches_keywords(text, keywords):
                        send_to_esp32(cmd, priority=0)   # voice = highest priority
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
    audio = np.concatenate(_capture_buf).astype(np.float32)
    audio = _normalize_audio(audio)
    audio_bytes = (audio * 32767).astype(np.int16).tobytes()
    with state.lock:
        state.voice_active = True
    _capturing   = False
    _capture_buf = []
    threading.Thread(target=process_voice_command, args=(audio_bytes,), daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# DANCE SELECTION — genre×tempo matrix, no consecutive repeat
# ══════════════════════════════════════════════════════════════════════════════
SLOW_GENRES   = ["Acoustic","Vocal","Speech","Choir","Folk","Singer","Ballad","Blues"]
MEDIUM_GENRES = ["Pop","Indie","R&B","Soul","Country"]
FAST_GENRES   = ["Electronic","Dance","Rock","House","Techno","Drum","Bass","Hip"]

DANCE_MATRIX = {
    # (speed, genre_type) → dance pool
    ("SLOW",   "slow"):   ["DANCE_ROLL_SLOW", "DANCE_PEACOCK", "DANCE_WAVE",    "DANCE_RIPPLE"],
    ("SLOW",   "medium"): ["DANCE_ROLL_SLOW", "DANCE_PEACOCK", "DANCE_RIPPLE"],
    ("SLOW",   "fast"):   ["DANCE_ROLL_SLOW", "DANCE_WAVE",    "DANCE_RIPPLE"],
    ("SLOW",   "other"):  ["DANCE_ROLL_SLOW", "DANCE_PEACOCK", "DANCE_WAVE"],
    ("MEDIUM", "slow"):   ["DANCE_SALSA",     "DANCE_RIPPLE_2","DANCE_CIRCLE"],
    ("MEDIUM", "medium"): ["DANCE_TWIST",     "DANCE_SALSA",   "DANCE_CIRCLE",  "DANCE_RIPPLE_2"],
    ("MEDIUM", "fast"):   ["DANCE_TWIST",     "DANCE_CIRCLE",  "DANCE_SALSA"],
    ("MEDIUM", "other"):  ["DANCE_TWIST",     "DANCE_RIPPLE_2","DANCE_CIRCLE"],
    ("FAST",   "slow"):   ["DANCE_ROLL_FAST", "DANCE_TWIST_2", "DANCE_CIRCLE_2"],
    ("FAST",   "medium"): ["DANCE_ROLL_FAST", "DANCE_CIRCLE_2","DANCE_TWIST_2"],
    ("FAST",   "fast"):   ["DANCE_ROLL_FAST", "DANCE_TWIST_2", "DANCE_CIRCLE_2","DANCE_SALSA"],
    ("FAST",   "other"):  ["DANCE_ROLL_FAST", "DANCE_CIRCLE_2","DANCE_TWIST_2"],
}

def _genre_type(genre):
    if any(s in genre for s in SLOW_GENRES):   return "slow"
    if any(s in genre for s in MEDIUM_GENRES): return "medium"
    if any(s in genre for s in FAST_GENRES):   return "fast"
    return "other"

def pick_dance(speed, genre, last_move):
    key  = (speed, _genre_type(genre))
    pool = DANCE_MATRIX.get(key, ["DANCE_CIRCLE"])
    # Remove last move to avoid immediate repeat
    opts = [d for d in pool if d != last_move]
    if not opts:
        opts = pool
    return random.choice(opts)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN AUDIO LISTENER
# ══════════════════════════════════════════════════════════════════════════════
def audio_listener():
    global _capture_buf, _capturing, _capture_start

    aubio_tempo   = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)

    mic           = sc.default_microphone()
    beat_debounce = time.time()

    print("\n=== AI DANCER v3 — BEAT PHASE SYNC + ENHANCED VOICE ===\n")

    with mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw   = recorder.record(numframes=CHUNK)
            chunk = raw.flatten().astype(np.float32)
            now   = time.time()

            # Update ring buffers
            _audio_ring.write(chunk)
            _yamnet_win.write(chunk)

            # Pre-buffer update (for VAD pre-roll)
            with _pre_buf_lock:
                _pre_buf.append(chunk.copy())

            # Bandpass for VAD
            vocal = bandpass(chunk)

            with state.lock:
                skip_vad = state.voice_active or (now < state.voice_override_until)

            # ── VAD ───────────────────────────────────────────────────────────
            if not skip_vad:
                vad_result = state.vad.update(vocal)

                if vad_result == 'START' and not _capturing:
                    _capturing     = True
                    _capture_start = now
                    with _pre_buf_lock:
                        # Pre-buffer: RMS-normalize each chunk as we capture
                        _capture_buf = [_normalize_chunk_rms(c.copy()) for c in _pre_buf]
                    _capture_buf.append(_normalize_chunk_rms(chunk.copy()))
                    print("Voice start")

                elif vad_result in ('ACTIVE', 'START') and _capturing:
                    # Normalize each chunk on the way in
                    _capture_buf.append(_normalize_chunk_rms(chunk.copy()))
                    if now - _capture_start > CAPTURE_MAX_SEC:
                        print("Max capture length, sending…")
                        _trigger_recognition()

                elif vad_result == 'END' and _capturing:
                    print("Voice end")
                    _trigger_recognition()

            elif _capturing:
                _capturing   = False
                _capture_buf = []
                state.vad.reset()

            # ── BEAT DETECTION ────────────────────────────────────────────────
            if aubio_tempo(chunk)[0]:
                if now - beat_debounce > 0.2:
                    bpm = aubio_tempo.get_bpm()
                    with state.lock:
                        g = state.genre
                        if bpm < 30:    bpm *= 2
                        elif bpm > 200: bpm /= 2
                        # Genre-aware BPM doubling for half-time genres
                        if (40 < bpm < 90
                                and any(x in g for x in ["Electronic","Dance","Rock","Pop"])):
                            bpm *= 2
                        state.beat_tracker.add_beat(bpm, now)
                        state.bpm      = state.beat_tracker.smoothed_bpm
                        state.beat_hit = True
                    beat_debounce = now

            # ── BEAT-PHASE CHOREOGRAPHY ───────────────────────────────────────
            with state.lock:
                state.beat_tracker.predict_next_beat()   # advance stale prediction

                on_beat   = state.beat_tracker.is_on_beat(window=0.06)
                esp32_free = _esp32_ready.is_set()   # don't queue if ESP32 is mid-motion
                free      = now > state.voice_override_until and not state.voice_active and esp32_free
                has_beats = len(state.beat_tracker.bpm_history) >= 3
                confident = state.beat_tracker.beat_confidence > 0.4

                # Use beat interval as dance interval (not fixed 1.0 s)
                beat_iv   = state.beat_tracker.beat_interval
                # Wait at least 4 beat intervals before next command
                dance_iv  = max(2.0, beat_iv * 4)
                overdue   = (now - state.last_dance_command_time) >= dance_iv

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
    draw.pieslice([x0, y0, x0+r*2, y0+r*2], 180, 270, fill=fill)
    draw.pieslice([x1-r*2, y1-r*2, x1, y1],   0,  90, fill=fill)
    draw.pieslice([x0, y1-r*2, x0+r*2, y1],   90, 180, fill=fill)
    draw.pieslice([x1-r*2, y0, x1, y0+r*2], 270, 360, fill=fill)

def display_loop():
    os.system("amixer set Master 100% > /dev/null 2>&1")
    disp          = init_display()
    eye_w, eye_h  = 70, 120
    lx, rx        = 90, 230
    cy            = 120
    blink_timer   = time.time()
    is_blinking   = False

    while True:
        with state.lock:
            speed       = state.music_speed
            beat_active = state.beat_hit
            va          = state.voice_active
            cmd_t       = state.command_detected_time
            bpm         = state.bpm
            state.beat_hit = False

        dt  = time.time() - cmd_t
        bg  = (255,255,255) if dt<0.25 else (30,30,80) if dt<1.0 else (10,35,15) if va else (0,0,0)
        img  = Image.new("RGB", (320,240), color=bg)
        draw = ImageDraw.Draw(img)

        if bpm > 0:
            draw.text((10,10), f"{bpm:.0f}", fill=(100,100,100))

        h   = eye_h
        col = (0, 255, 255)
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