import sys
# Ensure sudo can find your packages
sys.path.append("/home/codegenix/.local/lib/python3.13/site-packages")

import importlib.util
import os

# --- REVISED FIX: ALLOW ROOT TO USE NORMAL USER'S AUDIO ---
# 1. Check if the socket already exists. If not, explicitly point pactl to user 1000's server
if not os.path.exists("/tmp/pulse-socket"):
    os.system("sudo -u codegenix pactl --server=unix:/run/user/1000/pulse/native load-module module-native-protocol-unix auth-anonymous=1 socket=/tmp/pulse-socket > /dev/null 2>&1")

# 2. Tell this Root script to route audio through that public socket
os.environ["PULSE_SERVER"] = "unix:/tmp/pulse-socket"

# 3. Strip the restrictive user runtime directory variable to prevent crashes
os.environ.pop("XDG_RUNTIME_DIR", None)

# 4. (Optional) Quick diagnostic check to ensure the socket was successfully created
if not os.path.exists("/tmp/pulse-socket"):
    print("⚠️ WARNING: Could not establish a bridge socket to PulseAudio at /tmp/pulse-socket.")
# ----------------------------------------------------------

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
import numpy as np
import aubio
import tensorflow as tf

# ... (the rest of your code continues normally below this) ...

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
RATE = 16000
CHUNK = 512

DISPLAY_CS_PIN = board.CE0
DISPLAY_DC_PIN = board.D24
DISPLAY_RST_PIN = board.D25

LED_PIN = 13
LED_CHANNEL = 1
NUM_LEDS = 7
LED_BRIGHTNESS = 100


class RobotState:
    def __init__(self):
        self.operating_mode = "AUTO"
        self.audio_source = "MIC"
        self.show_audio_logs = False

        self.bpm = 0.0
        self.genre = "Listening..."
        self.beat_hit = False
        self.music_speed = "IDLE"  # IDLE, SLOW, MEDIUM, FAST, VOICE
        self.voice_active = False
        self.command_detected_time = 0.0
        self.body_roll = 0.0

        self.last_dance_command_time = time.time()
        self.voice_override_until = 0.0

        # Beat tracking history
        self.bpm_history = collections.deque(maxlen=20)
        self.lock = threading.Lock()


state = RobotState()


# ==========================================
# 2. USB SERIAL (ESP32)
# ==========================================
def connect_to_esp32():
    print("\n🔌 Searching for ESP32 via USB...")
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
        print(f" ⚠️ [Simulated] -> {command}")
        return
    with _send_lock:
        if not _esp32_ready.wait(timeout=2.0): pass
        _esp32_ready.clear()
        try:
            esp32_serial.write((command + "\n").encode('utf-8'))
        except Exception:
            _esp32_ready.set()


# ==========================================
# 3. LED STRIP MATH & ANIMATION THREAD
# ==========================================
strip = PixelStrip(NUM_LEDS, LED_PIN, 800000, 10, False, LED_BRIGHTNESS, LED_CHANNEL, ws.WS2811_STRIP_GRB)
strip.begin()


def hsv(hue, sat=255, val=255):
    r, g, b = colorsys.hsv_to_rgb((hue % 256) / 256.0, sat / 255.0, val / 255.0)
    return Color(int(r * 255), int(g * 255), int(b * 255))


def beatsin(bpm, low, high, phase=0):
    angle = time.monotonic() * bpm * 2 * math.pi / 60 + phase
    position = (math.sin(angle) + 1) / 2
    return int(low + position * (high - low))


def fade_to_black_by(amount):
    scale = max(0, 255 - amount) / 255.0
    for i in range(NUM_LEDS):
        c = strip.getPixelColor(i)
        r, g, b = (c >> 16) & 0xFF, (c >> 8) & 0xFF, c & 0xFF
        strip.setPixelColor(i, Color(int(r * scale), int(g * scale), int(b * scale)))


def led_thread():
    """Runs the 16 complex LED animations seamlessly based on AI music speed."""
    frame = 0
    heat = [0] * NUM_LEDS
    while True:
        with state.lock:
            speed = state.music_speed
            va = state.voice_active
            cmd_t = state.command_detected_time

        dt = time.time() - cmd_t
        frame += 1

        # SUCCESS FLASHES (Top Priority)
        if dt < 0.25:
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(255, 255, 255))
            strip.show();
            time.sleep(0.02);
            continue
        elif dt < 1.0:
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, 50, 255))
            strip.show();
            time.sleep(0.02);
            continue

        # LISTENING MODE (Green Comet)
        if va:
            strip.setPixelColor(0, Color(0, 0, 0))  # Clear
            fade_to_black_by(60)
            pos = frame % (NUM_LEDS * 2 - 2)
            if pos >= NUM_LEDS: pos = NUM_LEDS * 2 - 2 - pos
            strip.setPixelColor(pos, Color(0, 255, 50))
            strip.show();
            time.sleep(0.05);
            continue

        # FAST MUSIC (Fire / Juggle)
        if speed == "FAST":
            # Fire Effect
            for i in range(NUM_LEDS): heat[i] = max(0, heat[i] - random.randrange(10, 35))
            for i in range(NUM_LEDS - 1, 1, -1): heat[i] = (heat[i - 1] + heat[i - 2] * 2) // 3
            if random.randrange(256) < 130:
                s = random.randrange(min(2, NUM_LEDS))
                heat[s] = min(255, heat[s] + random.randrange(160, 256))
            for i in range(NUM_LEDS):
                t = heat[i]
                ramp = (t & 0x3F) << 2
                if t > 0x80:
                    c = Color(255, 255, ramp)
                elif t > 0x40:
                    c = Color(255, ramp, 0)
                else:
                    c = Color(ramp, 0, 0)
                strip.setPixelColor(i, c)
            strip.show();
            time.sleep(0.03)

        # MEDIUM MUSIC (Rainbow / Sinelon)
        elif speed == "MEDIUM":
            fade_to_black_by(35)
            pos = beatsin(30, 0, NUM_LEDS - 1)
            strip.setPixelColor(pos, hsv(int(time.monotonic() * 50) % 256))
            strip.show();
            time.sleep(0.02)

        # SLOW MUSIC (Wave / Breathing)
        elif speed == "SLOW":
            for i in range(NUM_LEDS):
                lvl = (math.sin(frame * 0.10 - i * 0.5) + 1) / 2
                strip.setPixelColor(i, hsv(frame + i * 10, 230, int(25 + lvl * 230)))
            strip.show();
            time.sleep(0.04)

        # IDLE (Cyan Breathing)
        else:
            lvl = (math.sin(frame * 0.05) + 1) / 2
            c_val = int(10 + lvl * 80)
            for i in range(NUM_LEDS): strip.setPixelColor(i, Color(0, c_val, c_val))
            strip.show();
            time.sleep(0.03)


# ==========================================
# 4. LCD SCREEN ENGINE
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


def display_loop():
    os.system("amixer set Master 100% > /dev/null 2>&1")
    disp = init_display()

    width, height = 320, 240
    eye_w, eye_h = 70, 120
    lx, rx, cy = 90, 230, 120
    blink_timer = time.time()
    is_blinking = False

    while True:
        with state.lock:
            speed, beat_active, va, cmd_t, bpm, roll = state.music_speed, state.beat_hit, state.voice_active, state.command_detected_time, state.bpm, state.body_roll
            state.beat_hit = False

        dt = time.time() - cmd_t
        bg = (255, 255, 255) if dt < 0.25 else (30, 30, 80) if dt < 1.0 else (10, 35, 15) if va else (0, 0, 0)
        img = Image.new("RGB", (width, height), color=bg)
        draw = ImageDraw.Draw(img)

        # Telemetry Text
        draw.text((5, 5), f"BPM: {bpm:.0f} | Mode: {state.operating_mode}", fill=(100, 100, 100))

        h, col, cy_r = eye_h, (0, 255, 255), cy
        if dt < 0.25:
            col, h, cy_r = (0, 0, 0), int(eye_h * 0.4), cy - 10
        elif dt < 1.0:
            col, h, cy_r = (0, 191, 255), int(eye_h * 0.4), cy - 10
        elif va:
            col, h = (0, 255, 100), int(eye_h * 0.75)
        elif speed == "FAST":
            col, h = (255, 50, 50), eye_h + 20
        elif speed == "MEDIUM":
            col, h = (255, 150, 50), eye_h + 10
        elif speed == "SLOW":
            col, h = (150, 50, 255), int(eye_h * 0.6)

        ew = eye_w + 10 if (beat_active and not va and dt > 1.0) else eye_w

        if time.time() - blink_timer > np.random.uniform(2.0, 5.0):
            is_blinking = True;
            blink_timer = time.time()
        if is_blinking and not va and dt > 1.0:
            h = 10
            if time.time() - blink_timer > 0.15: is_blinking = False

        roll_offset = int(roll * 1.5)
        cy_left, cy_right = cy_r + roll_offset, cy_r - roll_offset

        draw_rounded_rect(draw, [lx - ew // 2, cy_left - h // 2, lx + ew // 2, cy_left + h // 2], corner_radius=20,
                          fill=col)
        draw_rounded_rect(draw, [rx - ew // 2, cy_right - h // 2, rx + ew // 2, cy_right + h // 2], corner_radius=20,
                          fill=col)

        disp.image(img)
        time.sleep(0.03)


# ==========================================
# 5. AUDIO AI & VAD ENGINE
# ==========================================
def butter_bandpass(lowcut, highcut, fs, order=4):
    b, a = butter(order, [lowcut / (0.5 * fs), highcut / (0.5 * fs)], btype='band')
    return b, a


_BP_B, _BP_A = butter_bandpass(300, 3400, RATE, order=4)


def bandpass(data): return np.ascontiguousarray(lfilter(_BP_B, _BP_A, data), dtype=np.float32)


yamnet_model = None
YAMNET_CLASSES = []


def run_yamnet_periodically():
    global yamnet_model, YAMNET_CLASSES
    print("\n⏳ [AI] Loading YAMNet AI in background (Please wait ~15 seconds)...")
    try:
        yamnet_model = hub.load('https://tfhub.dev/google/yamnet/1')
        with tf.io.gfile.GFile(yamnet_model.class_map_path().numpy().decode('utf-8')) as f:
            YAMNET_CLASSES = [row['display_name'] for row in csv.DictReader(f)]
        print("\n✅ [AI] YAMNet Model successfully loaded!")
    except Exception as e:
        print(f"\n❌ [AI] Failed to load YAMNet: {e}")

    while True:
        time.sleep(4)
        if yamnet_model is None: continue
        snap = np.copy(audio_buffer)
        scores, _, _ = yamnet_model(snap)
        top = int(np.argmax(np.mean(scores, axis=0)))
        with state.lock:
            if "CMD" not in state.genre: state.genre = YAMNET_CLASSES[top]


audio_buffer = np.zeros(RATE * 3, dtype=np.float32)
recognizer = sr.Recognizer()


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


def process_voice_command(audio_bytes):
    try:
        text = recognizer.recognize_google(sr.AudioData(audio_bytes, RATE, 2), language='en-US').lower()
        if state.show_audio_logs: print(f"🎤 [VOICE] Recognized: '{text}'")

        if state.operating_mode == "AUTO":
            # Command Priority Mapping
            if "stop" in text or "stand" in text:
                send_to_esp32("STAND");
                say_phrase_offline("stopping")
            elif "forward" in text:
                send_to_esp32("WALK_FORWARD");
                say_phrase_offline("walking forward")
            elif "back" in text:
                send_to_esp32("WALK_BACKWARD");
                say_phrase_offline("walking backward")
            elif "dance" in text:
                send_to_esp32("DANCE_CIRCLE");
                say_phrase_offline("party mode")
            else:
                with state.lock:
                    state.voice_active = False
                return

            with state.lock:
                state.command_detected_time = time.time()
                state.voice_override_until = time.time() + 15.0
                state.bpm_history.clear()
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

    if state.audio_source == "BT":
        spk = sc.default_speaker()
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    else:
        mic = sc.default_microphone()

    syllables = []
    beat_debounce = time.time()

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
            if aubio_tempo(chunk)[0] and (now - beat_debounce > 0.2):
                bpm = aubio_tempo.get_bpm()
                if 40 < bpm < 90: bpm *= 2
                if 50 < bpm < 200:
                    with state.lock: state.bpm_history.append(bpm)
                with state.lock:
                    state.beat_hit = True
                    if len(state.bpm_history) > 0:
                        state.bpm = np.median(list(state.bpm_history))
                beat_debounce = now

            if state.operating_mode == "AUTO":
                with state.lock:
                    if (now - state.last_dance_command_time) >= 3.0 and not override and not va and len(
                            state.bpm_history) >= 3:
                        avg_bpm = np.median(list(state.bpm_history))
                        if any(s in state.genre for s in ["Acoustic", "Vocal", "Speech"]) or avg_bpm < 100:
                            state.music_speed, move = "SLOW", random.choice(
                                ["DANCE_ROLL_SLOW", "DANCE_CRAWL", "DANCE_BELLY_CRAWL", "DANCE_HEADBANG",
                                 "DANCE_PEACOCK", "DANCE_WAVE", "DANCE_BEG_WAVE", "DANCE_CHASSIS_BREATHE"])
                        elif avg_bpm < 130:
                            state.music_speed, move = "MEDIUM", random.choice(
                                ["DANCE_TWIST", "DANCE_TWIST_2", "DANCE_SALSA", "DANCE_RIPPLE", "DANCE_RIPPLE_2",
                                 "DANCE_PITCH_PIVOT", "DANCE_CIRCLE", "DANCE_CIRCLE_2"])
                        else:
                            state.music_speed, move = "FAST", random.choice(
                                ["DANCE_ROLL_FAST", "DANCE_STROBE", "DANCE_PULSE", "DANCE_TWITCH", "DANCE_WORM",
                                 "DANCE_GALLOP"])

                        send_to_esp32(move)
                        state.last_dance_command_time = now
                        state.bpm_history.clear()


# ==========================================
# 6. CLI MANUAL MENU
# ==========================================
MANUAL_COMMANDS = {
    11: ("WALK_FORWARD", "Walk Forward"), 12: ("WALK_BACKWARD", "Walk Backward"), 13: ("TURN_LEFT", "Turn Left"),
    14: ("TURN_RIGHT", "Turn Right"), 15: ("STAND", "Stand / Stop"),
    21: ("DANCE_WAVE", "Wave"), 22: ("DANCE_RIPPLE", "Ripple"), 23: ("DANCE_PEACOCK", "Peacock"),
    24: ("DANCE_SALSA", "Salsa"),
    25: ("DANCE_TWIST", "Twist"), 26: ("DANCE_CIRCLE", "Circle"), 27: ("DANCE_CRAWL", "Crawl"),
    28: ("DANCE_HEADBANG", "Headbang"),
    29: ("DANCE_ROLL_FAST", "Fast Roll"), 30: ("DANCE_STROBE", "Strobe"), 31: ("DANCE_PULSE", "Pulse"),
    32: ("DANCE_GALLOP", "Gallop"),
    33: ("DANCE_BEG_WAVE", "Beg Wave"), 34: ("DANCE_CHASSIS_BREATHE", "Breathe"),
    35: ("DANCE_BELLY_CRAWL", "Belly Crawl"),
    36: ("DANCE_PITCH_PIVOT", "Pitch Pivot"), 37: ("DANCE_TWITCH", "Twitch"), 38: ("DANCE_WORM", "Worm"),
    41: ("RELAX", "SAFETY: Deactivate (Relax) All Servos")
}


def manual_testing_loop():
    print("\n" + "=" * 50 + "\n   🤖 HEXAPOD GOD-MODE CLI (MANUAL TESTING) 🤖\n" + "=" * 50)
    for k, v in MANUAL_COMMANDS.items(): print(f"  [{k:02d}] {v[1]}")
    print("\n --- LED & SCREEN MOODS ---")
    print("  [91] Idle (Cyan)  [92] Party (Rainbow/Fire)")
    print("  [93] Slow (Purple Wave) [94] Success Flash")
    print("\n  [ 0] EXIT PROGRAM\n" + "=" * 50)

    while True:
        try:
            choice = input("\nEnter command number >>> ").strip()
            if choice == '0' or choice.lower() == 'q': os._exit(0)
            if choice.isdigit() and int(choice) in MANUAL_COMMANDS:
                cmd_str = MANUAL_COMMANDS[int(choice)][0]
                send_to_esp32(cmd_str)
                with state.lock:
                    state.command_detected_time = time.time()
            elif choice == '91':
                with state.lock:
                    state.music_speed, state.voice_active = "IDLE", False
            elif choice == '92':
                with state.lock:
                    state.music_speed, state.voice_active = "FAST", False
            elif choice == '93':
                with state.lock:
                    state.music_speed, state.voice_active = "SLOW", False
            elif choice == '94':
                with state.lock:
                    state.voice_active, state.command_detected_time = False, time.time()
            else:
                print("Invalid command.")
        except KeyboardInterrupt:
            os._exit(0)


# ==========================================
# 7. MASTER BOOT
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
        state.audio_source = "MIC"

    threading.Thread(target=esp32_reader_thread, daemon=True).start()
    threading.Thread(target=run_yamnet_periodically, daemon=True).start()
    threading.Thread(target=audio_listener, daemon=True).start()
    threading.Thread(target=led_thread, daemon=True).start()
    threading.Thread(target=display_loop, daemon=True).start()

    if state.operating_mode == "AUTO":
        print("\n✅ Auto Mode Running. Press Ctrl+C to exit.")
        while True: time.sleep(1)
    else:
        time.sleep(1.0)
        manual_testing_loop()