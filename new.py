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
import colorsys
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
print("🔌 Connecting to ESP32 over USB...")
try:
    esp32_serial = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
    print("✅ Successfully connected to ESP32 on /dev/ttyUSB0")
except Exception as e:
    print(f"❌ Failed to connect to ESP32. Error: {e}")
    esp32_serial = None

def send_to_esp32(command):
    if esp32_serial and esp32_serial.is_open:
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
            print(f"📡 Sent: {command}")
        except Exception as e:
            print(f"❌ Failed to send: {e}")

# ==========================================
# AUDIO CONFIG
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
# PROFESSIONAL STATE MANAGEMENT
# ==========================================
class BeatTracker:
    """Advanced beat detection and synchronization"""
    def __init__(self):
        self.bpm_history = collections.deque(maxlen=20)  # Last 20 beats
        self.beat_timestamps = collections.deque(maxlen=10)  # Last 10 beat times
        self.smoothed_bpm = 0.0
        self.beat_confidence = 0.0
        self.last_beat_time = 0.0
        self.beat_phase = 0.0
        self.beat_interval = 0.5  # seconds between beats
        
    def add_beat(self, bpm, current_time):
        """Add a detected beat with confidence calculation"""
        if len(self.beat_timestamps) > 0:
            time_since_last = current_time - self.last_beat_time
            expected_interval = 60.0 / bpm if bpm > 0 else 0.5
            
            # Confidence based on beat regularity
            if expected_interval > 0.1:
                interval_error = abs(time_since_last - expected_interval) / expected_interval
                confidence = max(0.0, 1.0 - interval_error)
            else:
                confidence = 0.5
        else:
            confidence = 0.8
        
        self.bpm_history.append(bpm)
        self.beat_timestamps.append(current_time)
        self.last_beat_time = current_time
        self.beat_confidence = confidence
        
        # Exponential moving average for smooth BPM
        if len(self.bpm_history) > 0:
            alpha = 0.3  # Smoothing factor
            self.smoothed_bpm = alpha * bpm + (1.0 - alpha) * self.smoothed_bpm
        
        # Update beat interval
        if len(self.beat_timestamps) >= 2:
            intervals = [self.beat_timestamps[i] - self.beat_timestamps[i-1] 
                        for i in range(1, len(self.beat_timestamps))]
            self.beat_interval = np.median(intervals) if intervals else 0.5
    
    def get_next_beat_time(self, current_time):
        """Predict when the next beat will occur"""
        if self.last_beat_time == 0:
            return current_time + 0.5
        time_since_beat = current_time - self.last_beat_time
        beats_ahead = int(time_since_beat / self.beat_interval) + 1
        return self.last_beat_time + (beats_ahead * self.beat_interval)
    
    def is_beat_aligned(self, current_time, tolerance=0.1):
        """Check if we're aligned with a beat"""
        next_beat = self.get_next_beat_time(current_time)
        return abs(current_time - next_beat) < tolerance
    
    def get_valid_bpm(self):
        """Return the most confident BPM estimate"""
        if len(self.bpm_history) == 0:
            return 0
        
        # Filter outliers (BPM should be reasonable: 60-180)
        valid_bpms = [b for b in self.bpm_history if 50 < b < 200]
        
        if len(valid_bpms) == 0:
            return self.smoothed_bpm
        
        return np.median(valid_bpms)


class VoiceDetector:
    """Advanced voice command detection"""
    def __init__(self):
        self.syllable_buffer = collections.deque(maxlen=30)
        self.noise_floor = 0.01
        self.voice_threshold = 0.05
        self.last_recognition_time = 0.0
        self.failed_attempts = 0
        self.max_retries = 2
        
    def update_noise_floor(self, energy):
        """Adaptive noise floor estimation"""
        alpha = 0.95
        self.noise_floor = alpha * self.noise_floor + (1.0 - alpha) * energy
    
    def is_voice_active(self, energy):
        """Detect voice activity with hysteresis"""
        return energy > (self.noise_floor + self.voice_threshold)
    
    def should_trigger_recognition(self):
        """Decide if we have enough voice evidence"""
        recent_activity = list(self.syllable_buffer)[-10:] if len(self.syllable_buffer) >= 10 else []
        if len(recent_activity) < 8:
            return False
        
        # Need at least 8 syllables in recent activity
        active_count = sum(1 for x in recent_activity if x > 0.5)
        return active_count >= 8


class RobotState:
    def __init__(self):
        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"
        self.voice_active = False
        self.command_detected_time = 0.0
        
        self.beat_tracker = BeatTracker()
        self.voice_detector = VoiceDetector()
        
        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0
        self.dance_interval = 1.0  # Seconds between dance changes
        
        self.lock = threading.Lock()


state = RobotState()

# ==========================================
# AI ENGINE
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

def say_phrase_offline(text_to_say):
    def speak_worker():
        try:
            engine = pyttsx3.init()
            engine.setProperty('rate', 145)
            engine.setProperty('volume', 1.0)
            engine.say(text_to_say)
            engine.runAndWait()
        except:
            pass
    t = threading.Thread(target=speak_worker, daemon=True)
    t.start()

def run_yamnet_periodically():
    """Genre detection thread"""
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
# ADVANCED VOICE PROCESSING
# ==========================================
def process_voice_command(audio_bytes):
    print("\n🎤 [VOICE] Speech detected! Processing...")
    retry_count = 0
    
    while retry_count <= state.voice_detector.max_retries:
        try:
            audio_data = sr.AudioData(audio_bytes, RATE, 2)
            text = recognizer.recognize_google(audio_data, language='en-US').lower()
            print(f"🎤 [VOICE] Recognized: '{text}'")
            
            matched = False
            
            # Priority 1: Movement Commands
            if "forward" in text or "advance" in text:
                send_to_esp32("WALK_FORWARD")
                say_phrase_offline("walking forward")
                matched = True
            elif "back" in text or "backward" in text or "reverse" in text:
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
            elif "stop" in text or "stand" in text or "halt" in text:
                send_to_esp32("STAND")
                say_phrase_offline("stopping now")
                matched = True
            
            # Priority 2: Dance Commands
            elif "dance" in text or "party" in text or "groove" in text:
                send_to_esp32("DANCE_CIRCLE")
                say_phrase_offline("lets party")
                matched = True
            elif "slow" in text or "acoustic" in text or "ballad" in text:
                send_to_esp32("DANCE_ROLL_SLOW")
                say_phrase_offline("entering slow mode")
                matched = True
            elif "fast" in text or "speed" in text or "rapid" in text or "quick" in text:
                send_to_esp32("DANCE_ROLL_FAST")
                say_phrase_offline("initiating high speed")
                matched = True
            elif "twist" in text:
                send_to_esp32("DANCE_TWIST")
                say_phrase_offline("doing the twist")
                matched = True
            elif "wave" in text or "hello" in text:
                send_to_esp32("DANCE_WAVE")
                say_phrase_offline("waving hello")
                matched = True
            elif "circle" in text or "spin" in text:
                send_to_esp32("DANCE_CIRCLE_2")
                say_phrase_offline("spinning around")
                matched = True
            
            if matched:
                with state.lock:
                    state.command_detected_time = time.time()
                    state.voice_override_until = time.time() + 12.0
                    state.beat_tracker.bpm_history.clear()
                break  # Success! Exit retry loop
            else:
                # Command not recognized
                if retry_count < state.voice_detector.max_retries:
                    print(f"🎤 [VOICE] Command not recognized. Retrying... ({retry_count + 1}/{state.voice_detector.max_retries})")
                    retry_count += 1
                else:
                    print("🎤 [VOICE] Command not understood")
                    break
        
        except sr.UnknownValueError:
            print(f"🎤 [VOICE] Could not understand audio. Retrying... ({retry_count + 1}/{state.voice_detector.max_retries})")
            retry_count += 1
        except sr.RequestError as e:
            print(f"🎤 [VOICE] API error: {e}")
            break
        except Exception as e:
            print(f"🎤 [VOICE] Error: {e}")
            break
    
    time.sleep(1.5)
    with state.lock:
        state.voice_active = False

# ==========================================
# PROFESSIONAL AUDIO LISTENER WITH BEAT SYNC
# ==========================================
def audio_listener():
    global audio_buffer, voice_byte_buffer
    
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)  # Slightly lower threshold
    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.25)  # More sensitive
    
    default_mic = sc.default_microphone()
    
    print(f"\n=== AI DANCER PROFESSIONAL MODE ===")
    print("Ready for voice commands and music!\n")
    
    beat_debounce = time.time()
    voice_debounce = time.time()
    
    with default_mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            raw_data = recorder.record(numframes=CHUNK)
            audio_chunk = raw_data.flatten().astype(np.float32)
            current_time = time.time()
            
            # Update buffers
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = audio_chunk
            
            int16_chunk = (audio_chunk * 32767).astype(np.int16).tobytes()
            voice_byte_buffer += int16_chunk
            if len(voice_byte_buffer) > RATE * 4 * 2:
                voice_byte_buffer = voice_byte_buffer[-(RATE * 4 * 2):]
            
            # Process voice (with better energy detection)
            vocal_audio = butter_bandpass_filter(audio_chunk)
            energy = np.sqrt(np.mean(vocal_audio ** 2))
            
            with state.lock:
                state.voice_detector.update_noise_floor(energy)
                is_voice_active = state.voice_detector.is_voice_active(energy)
            
            if is_voice_active:
                state.voice_detector.syllable_buffer.append(1.0)
            else:
                state.voice_detector.syllable_buffer.append(0.0)
            
            # VOICE TRIGGER with debounce
            if (current_time - voice_debounce > 0.5 and 
                state.voice_detector.should_trigger_recognition() and 
                not state.voice_active):
                
                with state.lock:
                    state.voice_active = True
                voice_snapshot = bytes(voice_byte_buffer)
                threading.Thread(target=process_voice_command, args=(voice_snapshot,), daemon=True).start()
                voice_debounce = current_time
            
            # BEAT DETECTION with debounce
            if aubio_tempo(audio_chunk)[0]:
                if current_time - beat_debounce > 0.2:  # 200ms debounce
                    bpm = aubio_tempo.get_bpm()
                    
                    # Smart BPM adjustment
                    with state.lock:
                        genre = state.genre
                        
                        # Reject unrealistic BPMs
                        if bpm < 30:
                            bpm *= 2
                        elif bpm > 200:
                            bpm /= 2
                        
                        # Double BPM for slow genres if it's in the lower range
                        if 40 < bpm < 90 and any(g in genre for g in ["Electronic", "Dance", "Rock", "Pop"]):
                            bpm *= 2
                        
                        # Add to beat tracker
                        state.beat_tracker.add_beat(bpm, current_time)
                        state.bpm = state.beat_tracker.smoothed_bpm
                        state.beat_hit = True
                    
                    beat_debounce = current_time
            
            # BEAT-SYNCED CHOREOGRAPHY
            with state.lock:
                current_time_check = time.time()
                
                # Check if enough time has passed since last dance command
                time_since_dance = current_time_check - state.last_dance_command_time
                is_overdue = time_since_dance >= state.dance_interval
                
                # Check voice override
                not_in_voice_mode = current_time_check > state.voice_override_until and not state.voice_active
                
                # Check if we have reliable beat data
                has_beats = len(state.beat_tracker.bpm_history) >= 3
                beat_confident = state.beat_tracker.beat_confidence > 0.4
                
                if is_overdue and not_in_voice_mode and has_beats and beat_confident:
                    avg_bpm = state.beat_tracker.get_valid_bpm()
                    current_genre = state.genre
                    
                    # Smart choreography selection
                    slow_genres = ["Acoustic", "Vocal", "Speech", "Choir", "Folk", "Singer", "Ballad", "Blues"]
                    
                    if any(g in current_genre for g in slow_genres) or avg_bpm < 100:
                        state.music_speed = "SLOW"
                        next_move = random.choice([
                            "DANCE_ROLL_SLOW", "DANCE_PEACOCK", 
                            "DANCE_WAVE", "DANCE_RIPPLE"
                        ])
                        print(f"🎼 SLOW ({avg_bpm:.0f} BPM) → {next_move}")
                    
                    elif avg_bpm < 130:
                        state.music_speed = "MEDIUM"
                        next_move = random.choice([
                            "DANCE_TWIST", "DANCE_RIPPLE_2",
                            "DANCE_CIRCLE", "DANCE_SALSA"
                        ])
                        print(f"🎵 MEDIUM ({avg_bpm:.0f} BPM) → {next_move}")
                    
                    else:
                        state.music_speed = "FAST"
                        next_move = random.choice([
                            "DANCE_ROLL_FAST", "DANCE_TWIST_2",
                            "DANCE_CIRCLE_2"
                        ])
                        print(f"🔥 FAST ({avg_bpm:.0f} BPM) → {next_move}")
                    
                    send_to_esp32(next_move)
                    state.last_dance_command_time = current_time_check
                    state.beat_tracker.bpm_history.clear()

ai_thread = threading.Thread(target=run_yamnet_periodically, daemon=True)
ai_thread.start()

audio_thread = threading.Thread(target=audio_listener, daemon=True)
audio_thread.start()

# ==========================================
# DISPLAY ENGINE
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
            bpm = state.bpm
            state.beat_hit = False
        
        time_since_cmd = time.time() - cmd_time
        
        # Dynamic background
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
        
        # BPM Display
        if bpm > 0:
            bpm_text = f"{bpm:.0f}"
            draw.text((10, 10), bpm_text, fill=(100, 100, 100), font=None)
        
        # Eyes with mood
        current_h = eye_height
        color = (0, 255, 255)
        center_y_render = center_y
        
        if time_since_cmd < 0.25:
            color, current_h, center_y_render = (0, 0, 0), int(eye_height * 0.4), center_y - 10
        elif time_since_cmd < 1.0:
            color, current_h, center_y_render = (0, 191, 255), int(eye_height * 0.4), center_y - 10
        elif voice_active:
            color, current_h = (0, 255, 100), int(eye_height * 0.75)
        elif speed == "FAST":
            color, current_h = (255, 50, 50), eye_height + 20
        elif speed == "MEDIUM":
            color, current_h = (255, 150, 50), eye_height + 10
        elif speed == "SLOW":
            color, current_h = (150, 50, 255), int(eye_height * 0.6)
        
        eye_width_render = eye_width + 10 if (beat_active and not voice_active and time_since_cmd > 1.0) else eye_width
        
        # Blinking
        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True
            blink_timer = time.time()
        if is_blinking and not voice_active and time_since_cmd > 1.0:
            current_h = 10
            if time.time() - blink_timer > 0.15:
                is_blinking = False
        
        draw_rounded_rect(draw, [left_x - eye_width_render // 2, center_y_render - current_h // 2,
                                 left_x + eye_width_render // 2, center_y_render + current_h // 2], 
                         corner_radius=20, fill=color)
        draw_rounded_rect(draw, [right_x - eye_width_render // 2, center_y_render - current_h // 2,
                                 right_x + eye_width_render // 2, center_y_render + current_h // 2], 
                         corner_radius=20, fill=color)
        
        disp.image(img)
        time.sleep(0.03)

display_loop()