import sys
# --- ADD THIS LINE TO FIX THE SUDO PATH ISSUE ---
sys.path.append("/home/codegenix/.local/lib/python3.13/site-packages")

import importlib.util
import os
os.environ["TFHUB_CACHE_DIR"] = "./ai_model_cache"

# --- The "Smart" Python 3.13 Hack ---
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None
sys.modules['imp'] = FakeImp()
# ------------------------------------

import serial
import soundcard as sc

import importlib.util
import os
os.environ["TFHUB_CACHE_DIR"] = "./ai_model_cache"
import random
import collections
import threading
import time
import csv
import math
import colorsys


# --- The "Smart" Python 3.13 Hack ---
class FakeImp:
    @staticmethod
    def find_module(name):
        if importlib.util.find_spec(name) is None:
            raise ImportError(f"No module named {name}")
        return None


sys.modules['imp'] = FakeImp()
# ------------------------------------

import serial
import soundcard as sc
import numpy as np
import aubio
import tensorflow as tf
import tensorflow_hub as hub
import speech_recognition as sr
import pyttsx3
from scipy.signal import butter, lfilter

# Display Libraries
import board
import busio
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341 as ili9341

# LED Libraries
from rpi_ws281x import PixelStrip, Color, ws


# ==========================================
# 1. GLOBAL STATE & CONFIGURATION
# ==========================================
class RobotState:
    def __init__(self):
        # Modes
        self.operating_mode = "AUTO"  # "AUTO" or "MANUAL"
        self.audio_source = "MIC"  # "MIC" or "BT"
        self.show_audio_logs = False

        # AI & Telemetry
        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"
        self.voice_active = False
        self.command_detected_time = 0.0
        self.body_roll = 0.0

        # Timers
        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0
        self.lock = threading.Lock()


state = RobotState()

RATE = 16000
CHUNK = 512

DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25

LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
LED_BRIGHTNESS = 100


# ==========================================
# 2. USB SERIAL CONNECTION (AUTO-DETECT)
# ==========================================
def connect_to_esp32():
    print("\n🔌 Searching for ESP32 via USB...")
    # Scans common Linux USB Serial ports
    for port in ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/serial0']:
        try:
            s = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Successfully connected to ESP32 on {port}")
            return s
        except Exception:
            continue
    print("❌ Failed to find ESP32 on USB. Manual mode will simulate commands.")
    return None


esp32_serial = connect_to_esp32()
_esp32_ready = threading.Event()
_esp32_ready.set()
_send_lock = threading.Lock()


def esp32_reader_thread():
    while True:
        if esp32_serial and esp32_serial.is_open:
            try:
                line = esp32_serial.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith("TILT:"):
                    try:
                        with state.lock:
                            state.body_roll = float(line.split(":")[1])
                    except ValueError:
                        pass
                elif line == "READY":
                    _esp32_ready.set()
            except Exception:
                time.sleep(0.1)
        else:
            time.sleep(0.1)


def send_to_esp32(command):
    if not (esp32_serial and esp32_serial.is_open):
        print(f" ⚠️ [Simulated USB] -> {command}")
        return
    with _send_lock:
        if not _esp32_ready.wait(timeout=3.0):
            pass  # Force send if timeout
        _esp32_ready.clear()
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
        except Exception as e:
            _esp32_ready.set()


# ==========================================
# 3. LED & DISPLAY ENGINES
# ==========================================
def init_display():
    spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI)
    return ili9341.ILI9341(spi, cs=digitalio.DigitalInOut(DISPLAY_CS_PIN), dc=digitalio.DigitalInOut(DISPLAY_DC_PIN),
                           rst=digitalio.DigitalInOut(DISPLAY_RST_PIN), rotation=90, baudrate=24000000)


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


def visuals_loop():
    """Handles both the ILI9341 Screen and the WS2812B LEDs synchronously"""
    os.system("amixer set Master 100% > /dev/null 2>&1")
    disp = init_display()

    # Init LEDs
    strip = PixelStrip(NUM_LEDS, LED_PIN, 800000, 10, False, LED_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB)
    strip.begin()

    def fill_leds(c):
        for i in range(NUM_LEDS): strip.setPixelColor(i, c)

    width, height = 320, 240
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
            roll = state.body_roll
            state.beat_hit = False

        dt = time.time() - cmd_t
        now_ms = int(time.time() * 1000)

        # 1. VISUAL STATE MACHINE (Shared for both Screen & LEDs)
        if dt < 0.25:
            # FLASH WHITE
            bg, col, h, cy_r = (255, 255, 255), (0, 0, 0), int(eye_h * 0.4), cy - 10
            fill_leds(Color(255, 255, 255))
        elif dt < 1.0:
            # GLOW BLUE
            bg, col, h, cy_r = (30, 30, 80), (0, 191, 255), int(eye_h * 0.4), cy - 10
            fill_leds(Color(0, 50, 255))
        elif va:
            # LISTENING GREEN
            bg, col, h, cy_r = (10, 35, 15), (0, 255, 100), int(eye_h * 0.75), cy
            lvl = (math.sin(now_ms * 0.005) + 1) / 2
            fill_leds(Color(0, int(50 + lvl * 200), 0))
        elif speed == "DANCE" or speed == "FAST":
            # PARTY RAINBOW / RED
            bg, cy_r = (0, 0, 0), cy
            hue = (time.time() * 2) % 1.0
            r_val, g_val, b_val = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            col, h = (int(r_val * 255), int(g_val * 255), int(b_val * 255)), eye_h + 15
            for i in range(NUM_LEDS):
                lhue = ((i * 20 + now_ms // 5) % 256) / 256.0
                lr, lg, lb = colorsys.hsv_to_rgb(lhue, 1.0, 1.0)
                strip.setPixelColor(i, Color(int(lr * 255), int(lg * 255), int(lb * 255)))
        elif speed == "SLOW":
            # SLOW PURPLE
            bg, col, h, cy_r = (0, 0, 0), (150, 50, 255), int(eye_h * 0.6), cy
            for i in range(NUM_LEDS):
                lvl = (math.sin((now_ms * 0.003) - (i * 0.5)) + 1) / 2
                strip.setPixelColor(i, Color(int(150 * lvl), 0, int(255 * lvl)))
        else:
            # IDLE CYAN
            bg, col, h, cy_r = (0, 0, 0), (0, 255, 255), eye_h, cy
            lvl = (math.sin(now_ms * 0.002) + 1) / 2
            fill_leds(Color(0, int(20 + lvl * 100), int(20 + lvl * 100)))

        # 2. SCREEN DRAWING
        img = Image.new("RGB", (width, height), color=bg)
        draw = ImageDraw.Draw(img)

        # Telemetry Text
        draw.text((5, 5), f"BPM: {bpm:.0f} | Mode: {state.operating_mode}", fill=(100, 100, 100))

        # Blinking & Pulsing
        ew = eye_w + 10 if (beat_active and not va and dt > 1.0) else eye_w
        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True;
            blink_timer = time.time()
        if is_blinking and not va and dt > 1.0:
            h = 10
            if time.time() - blink_timer > 0.15: is_blinking = False

        # IMU Tilt Math
        roll_offset = int(roll * 1.5)
        cy_left, cy_right = cy_r + roll_offset, cy_r - roll_offset

        draw_rounded_rect(draw, [lx - ew // 2, cy_left - h // 2, lx + ew // 2, cy_left + h // 2], corner_radius=20,
                          fill=col)
        draw_rounded_rect(draw, [rx - ew // 2, cy_right - h // 2, rx + ew // 2, cy_right + h // 2], corner_radius=20,
                          fill=col)

        disp.image(img)
        strip.show()
        time.sleep(0.03)


# ==========================================
# 4. AUDIO AI & VAD ENGINE
# ==========================================
def butter_bandpass(lowcut, highcut, fs, order=4):
    b, a = butter(order, [lowcut / (0.5 * fs), highcut / (0.5 * fs)], btype='band')
    return b, a


_BP_B, _BP_A = butter_bandpass(300, 3400, RATE, order=4)


def bandpass(data): return np.ascontiguousarray(lfilter(_BP_B, _BP_A, data), dtype=np.float32)


yamnet_model = None
YAMNET_CLASSES = []


def init_ai():
    global yamnet_model, YAMNET_CLASSES
    print("Loading YAMNet Audio AI...")
    yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
    with tf.io.gfile.GFile(yamnet_model.class_map_path().numpy().decode('utf-8')) as f:
        YAMNET_CLASSES = [row['display_name'] for row in csv.DictReader(f)]


audio_buffer = np.zeros(RATE * 3, dtype=np.float32)
recognizer = sr.Recognizer()

COMMANDS = [
    (["forward", "advance"], "WALK_FORWARD", "walking forward"),
    (["backward", "back"], "WALK_BACKWARD", "walking backward"),
    (["left"], "TURN_LEFT", "turning left"),
    (["right"], "TURN_RIGHT", "turning right"),
    (["stop", "stand"], "STAND", "stopping"),
    (["dance", "party"], "DANCE_CIRCLE", "lets party"),
    (["slow"], "DANCE_ROLL_SLOW", "slow mode"),
    (["fast"], "DANCE_ROLL_FAST", "high speed"),
]


def say_phrase_offline(text):
    def _speak():
        try:
            e = pyttsx3.init();
            e.setProperty('rate', 145);
            e.say(text);
            e.runAndWait()
        except:
            pass

    threading.Thread(target=_speak, daemon=True).start()


def run_yamnet_periodically():
    while True:
        time.sleep(4)
        if yamnet_model is None: continue
        snap = np.copy(audio_buffer)
        scores, _, _ = yamnet_model(snap)
        top = int(np.argmax(np.mean(scores, axis=0)))
        with state.lock:
            state.genre = YAMNET_CLASSES[top]


def process_voice_command(audio_bytes):
    try:
        text = recognizer.recognize_google(sr.AudioData(audio_bytes, RATE, 2), language='en-US').lower()
        if state.show_audio_logs: print(f"🎤 [VOICE] Recognized: '{text}'")

        if state.operating_mode == "AUTO":
            for keywords, cmd, phrase in COMMANDS:
                if any(kw in text for kw in keywords):
                    send_to_esp32(cmd)
                    say_phrase_offline(phrase)
                    with state.lock:
                        state.command_detected_time = time.time()
                        state.voice_override_until = time.time() + 15.0
                    break
    except Exception:
        pass
    with state.lock:
        state.voice_active = False


def audio_listener():
    global audio_buffer
    aubio_tempo = aubio.tempo("specflux", 1024, CHUNK, RATE)
    aubio_tempo.set_threshold(0.5)
    aubio_syllable = aubio.onset("mkl", 1024, CHUNK, RATE)
    aubio_syllable.set_threshold(0.3)

    # Dynamic Source Routing
    if state.audio_source == "BT":
        spk = sc.default_speaker()
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    else:
        mic = sc.default_microphone()

    syllables = []
    beat_history = []

    with mic.recorder(samplerate=RATE, channels=1) as recorder:
        while True:
            chunk = recorder.record(numframes=CHUNK).flatten().astype(np.float32)
            now = time.time()
            audio_buffer = np.roll(audio_buffer, -CHUNK)
            audio_buffer[-CHUNK:] = chunk

            # VAD / Voice Trigger
            if aubio_syllable(bandpass(chunk))[0]: syllables.append(now)
            syllables = [t for t in syllables if now - t <= 3.0]

            with state.lock:
                va = state.voice_active
                override = now < state.voice_override_until

            if len(syllables) > 8 and not va and not override:
                with state.lock: state.voice_active = True
                audio_bytes = (np.concatenate([audio_buffer[-RATE * 4:]]) * 32767).astype(np.int16).tobytes()
                threading.Thread(target=process_voice_command, args=(audio_bytes,), daemon=True).start()
                syllables.clear()

            # Beat Tracking
            is_beat = aubio_tempo(chunk)[0]
            if is_beat:
                bpm = aubio_tempo.get_bpm()
                if 40 < bpm < 90: bpm *= 2
                if 50 < bpm < 200: beat_history.append(bpm)
                with state.lock:
                    state.beat_hit = True
                    state.bpm = np.median(beat_history) if beat_history else bpm

            # Telemetry Log Check
            if is_beat and state.show_audio_logs:
                print(f"🎵 [AUDIO] BPM: {state.bpm:.1f} | Genre: {state.genre:15.15} | Syllables: {len(syllables)}/3s")

            # Auto Dance Orchestrator (Only active in AUTO mode)
            if state.operating_mode == "AUTO":
                with state.lock:
                    if (now - state.last_dance_command_time) >= 3.0 and not override and not va and len(
                            beat_history) >= 3:
                        avg_bpm = np.median(beat_history)
                        if any(s in state.genre for s in ["Acoustic", "Vocal", "Speech"]) or avg_bpm < 100:
                            state.music_speed, move = "SLOW", random.choice(["DANCE_ROLL_SLOW", "DANCE_CRAWL"])
                        elif avg_bpm < 130:
                            state.music_speed, move = "MEDIUM", random.choice(["DANCE_TWIST", "DANCE_SALSA"])
                        else:
                            state.music_speed, move = "FAST", random.choice(["DANCE_ROLL_FAST", "DANCE_PULSE"])

                        send_to_esp32(move)
                        state.last_dance_command_time = now
                        beat_history.clear()


# ==========================================
# 5. CLI MANUAL TESTING MENU
# ==========================================
def print_menu():
    print("\n" + "=" * 55)
    print("   🤖 HEXAPOD GOD-MODE CLI (MANUAL TESTING) 🤖")
    print("=" * 55)
    print(" --- MOVEMENTS ---")
    print("  [11] Walk Forward   [12] Walk Back   [13] Turn L")
    print("  [14] Turn Right     [15] STOP/STAND")
    print(" --- DANCES ---")
    print("  [21] Wave           [22] Peacock     [23] Twist")
    print("  [24] Salsa          [25] Fast Roll   [26] Slow Roll")
    print(" --- LED & SCREEN PATTERNS ---")
    print("  [31] Pattern: Idle (Cyan Breathe)")
    print("  [32] Pattern: Party (Rainbow Pulse)")
    print("  [33] Pattern: Slow (Purple Sway)")
    print("  [34] Pattern: Listening (Green Focus)")
    print("  [35] Pattern: Success Flash (White -> Blue)")
    print(" --- SYSTEM ---")
    print("  [41] Toggle Audio AI Telemetry Logs")
    print("  [51] Test Specific Leg (LEG_POS Command)")
    print("  [0]  EXIT PROGRAM")
    print("=" * 55)


def manual_testing_loop():
    print_menu()
    while True:
        try:
            choice = input("\nEnter command number >>> ").strip()
            if choice == '0': os._exit(0)

            # Moves
            if choice == '11':
                send_to_esp32("WALK_FORWARD")
            elif choice == '12':
                send_to_esp32("WALK_BACKWARD")
            elif choice == '13':
                send_to_esp32("TURN_LEFT")
            elif choice == '14':
                send_to_esp32("TURN_RIGHT")
            elif choice == '15':
                send_to_esp32("STAND"); state.music_speed = "IDLE"

            # Dances
            elif choice == '21':
                send_to_esp32("DANCE_WAVE"); state.music_speed = "SLOW"
            elif choice == '22':
                send_to_esp32("DANCE_PEACOCK"); state.music_speed = "SLOW"
            elif choice == '23':
                send_to_esp32("DANCE_TWIST"); state.music_speed = "MEDIUM"
            elif choice == '24':
                send_to_esp32("DANCE_SALSA"); state.music_speed = "MEDIUM"
            elif choice == '25':
                send_to_esp32("DANCE_ROLL_FAST"); state.music_speed = "FAST"
            elif choice == '26':
                send_to_esp32("DANCE_ROLL_SLOW"); state.music_speed = "SLOW"

            # LEDs/Screen Overrides
            elif choice == '31':
                with state.lock:
                    state.music_speed, state.voice_active = "IDLE", False
            elif choice == '32':
                with state.lock:
                    state.music_speed, state.voice_active = "DANCE", False
            elif choice == '33':
                with state.lock:
                    state.music_speed, state.voice_active = "SLOW", False
            elif choice == '34':
                with state.lock:
                    state.voice_active = True
            elif choice == '35':
                with state.lock:
                    state.voice_active, state.command_detected_time = False, time.time()

            # System
            elif choice == '41':
                state.show_audio_logs = not state.show_audio_logs
                print(f"📡 Audio Logs turned {'ON' if state.show_audio_logs else 'OFF'}")
            elif choice == '51':
                x = input("Enter Leg (0-5) and X,Y,Z coords (e.g. 0:80,0,-40) >>> ")
                send_to_esp32(f"LEG_POS:{x}")
            elif choice.lower() == 'm':
                print_menu()
            else:
                print("Unknown command. Type 'm' to see menu.")
        except KeyboardInterrupt:
            os._exit(0)


# ==========================================
# 6. MASTER BOOT SEQUENCE
# ==========================================
if __name__ == "__main__":
    print("\n" + "=" * 50 + "\n      🤖 CODEGENIX HEXABOT OS 🤖\n" + "=" * 50)
    print(" Select Operating Mode:")
    print("  [1] AUTO MODE (Full Autonomous AI Dancer)")
    print("  [2] MANUAL MODE (CLI Control via SSH)\n" + "=" * 50)

    try:
        mode_select = input(">>> ").strip()
    except KeyboardInterrupt:
        os._exit(0)

    if mode_select == '1':
        state.operating_mode = "AUTO"
        state.show_audio_logs = True
        print("\n Select Audio Source:")
        print("  [1] Physical Microphone (Room Sound + Voice)")
        print("  [2] Internal Bluetooth (Spotify/YouTube Loopback)")
        src_select = input(">>> ").strip()
        state.audio_source = "BT" if src_select == '2' else "MIC"
    else:
        state.operating_mode = "MANUAL"
        state.audio_source = "MIC"  # Always run Mic in background for logs
        state.show_audio_logs = False

    # Start universal threads
    init_ai()
    threading.Thread(target=esp32_reader_thread, daemon=True).start()
    threading.Thread(target=run_yamnet_periodically, daemon=True).start()
    threading.Thread(target=audio_listener, daemon=True).start()
    threading.Thread(target=visuals_loop, daemon=True).start()

    if state.operating_mode == "AUTO":
        print("\n✅ Auto Mode Running. Press Ctrl+C to exit.")
        while True: time.sleep(1)
    else:
        time.sleep(1)  # Let threads boot before drawing CLI
        manual_testing_loop()