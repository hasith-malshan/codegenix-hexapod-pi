#!/usr/bin/env python3
"""
=======================================================================
  HEXABOT CHOREOGRAPHY — හන්තානට පායන සඳ (Hanthanata Payana Sanda)
  Artist  : Amarasiri Peiris
  BPM     : 152  |  Key : C Major  |  Beat interval : ~0.395 s
=======================================================================

HOW TO USE:
  1. Start playing the song.
  2. Run this script immediately (within 1 second of playback start).
  3. The script manages its own timing — no extra sync needed.

  Run:  sudo python3 hanthaneta_dance.py

  This script reuses your existing send_to_esp32() and esp32_serial
  from the main hexabot_os.py. Either:
    (a) Import and run this from hexabot_os.py, OR
    (b) Run standalone — it will open its own serial connection.

SONG STRUCTURE (timestamps are approximate at 152 BPM):
  0:00  Intro          — soft, swaying
  0:20  Chorus (1st)   — emotional peak, flowing
  0:52  Verse 1        — gentle exploration
  1:30  Inter          — soft break
  1:50  Chorus (2nd)   — bigger, more expressive
  2:22  Verse 2        — build toward ending
  3:00  Outro/Fade     — winding down
=======================================================================
"""

import time
import threading
import sys
import os

# ---------------------------------------------------------------------------
# Serial Setup (standalone mode — skip if running inside hexabot_os.py)
# ---------------------------------------------------------------------------
try:
    # Try to reuse the already-open connection from hexabot_os
    from __main__ import send_to_esp32, esp32_serial, state, log_event
    STANDALONE = False
    print("✅ Running inside Hexabot OS — reusing serial connection.")
except ImportError:
    STANDALONE = True
    import serial
    import logging

    log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hexabot.log")
    logging.basicConfig(filename=log_file_path, level=logging.INFO,
                        format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')

    def log_event(msg):
        logging.info(msg)

    _serial = None
    for _port in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0"):
        try:
            _serial = serial.Serial(_port, 115200, timeout=1)
            print(f"✅ Connected to ESP32 on {_port}")
            break
        except Exception:
            continue
    if _serial is None:
        print("⚠️  ESP32 not found — commands will be printed only (simulation mode).")

    # Minimal READY handshake for standalone mode
    _ready_event = threading.Event()
    _pending_command = [None]
    _lock = threading.Lock()

    def _reader():
        while True:
            if _serial and _serial.is_open:
                try:
                    line = _serial.readline().decode("utf-8", errors="ignore").strip()
                    if line == "READY":
                        _ready_event.set()
                    elif line:
                        print(f"  🤖 ESP32: {line}")
                except Exception:
                    time.sleep(0.1)
            else:
                time.sleep(0.1)

    threading.Thread(target=_reader, daemon=True).start()

    def send_to_esp32(command: str):
        print(f"  📡 SEND → {command}")
        log_event(f"📡 [CHOREO SENT]: {command}")
        if _serial and _serial.is_open:
            try:
                _serial.write((command + "\n").encode("utf-8"))
                _serial.flush()
            except Exception as e:
                print(f"  ❌ Serial error: {e}")


# ---------------------------------------------------------------------------
# Choreography Timeline
# ---------------------------------------------------------------------------
# Each entry: (song_time_seconds, dance_command, section_label, note)
#
# Beat interval at 152 BPM = 0.395 s
# A typical dance move on the hexapod takes 1.5–3.0 beats (0.6–1.2 s)
# We schedule a new command roughly every 2–4 beats = every 0.8–1.6 s
# Emotional/dynamic changes follow chord changes in the song.
# ---------------------------------------------------------------------------

CHOREOGRAPHY = [
    # ── INTRO (0:00 – 0:20) ─────────────────────────────────────────────
    # Soft, swaying. Start still, ease into motion.
    (0.0,   "DANCE_CHASSIS_BREATHE",  "Intro",     "Wake up — gentle sway on C"),
    (3.5,   "DANCE_BEG_WAVE",          "Intro",     "Curious moon-gazing wave on Am"),
    (6.5,   "DANCE_WAVE",              "Intro",     "Gentle ripple on F"),
    (9.5,   "DANCE_CHASSIS_BREATHE",  "Intro",     "Rest, breathe on G"),
    (12.5,  "DANCE_PEACOCK",           "Intro",     "Proud slow display on Am→G"),
    (16.0,  "DANCE_WAVE",              "Intro",     "Flow into pre-chorus on Dm→G7"),

    # ── CHORUS 1 (0:20 – 0:52) ──────────────────────────────────────────
    # Emotional peak — flowing, expressive, moderate energy
    (20.0,  "DANCE_RIPPLE",            "Chorus 1",  "Moonlight ripple — C"),
    (22.5,  "DANCE_PITCH_PIVOT",       "Chorus 1",  "Swaying look up to the moon — Am"),
    (25.5,  "DANCE_PEACOCK",           "Chorus 1",  "Open up, display — F"),
    (28.5,  "DANCE_RIPPLE",            "Chorus 1",  "Cascade back — C"),
    (31.5,  "DANCE_ROLL",              "Chorus 1",  "Gentle roll on G"),
    (34.5,  "DANCE_PITCH_PIVOT",       "Chorus 1",  "Lean and return — G7"),
    (37.5,  "DANCE_WAVE",              "Chorus 1",  "Chorus resolve — C"),
    (40.5,  "DANCE_PEACOCK",           "Chorus 1",  "Full display — G"),
    (43.5,  "DANCE_RIPPLE",            "Chorus 1",  "Ripple through — G7"),
    (46.5,  "DANCE_CHASSIS_BREATHE",  "Chorus 1",  "Breathe out — C resolve"),
    (50.0,  "DANCE_WAVE",              "Chorus 1",  "Bridge into verse"),

    # ── VERSE 1 (0:52 – 1:30) ───────────────────────────────────────────
    # Storytelling — lighter, more playful exploration
    (52.0,  "DANCE_TWIST",             "Verse 1",   "Anduru lala — C, light twist"),
    (55.5,  "DANCE_CIRCLE",            "Verse 1",   "Wahina kala — Em, small circle"),
    (59.0,  "DANCE_RIPPLE_2",          "Verse 1",   "Sarasawi bima — Am, ripple 2"),
    (62.5,  "DANCE_WAVE",              "Verse 1",   "Themenna — F→C, gentle wave"),
    (66.5,  "DANCE_TWIST",             "Verse 1",   "Repeat — C"),
    (70.0,  "DANCE_CIRCLE",            "Verse 1",   "Em again — light spin"),
    (73.5,  "DANCE_RIPPLE",            "Verse 1",   "Am flows"),
    (77.0,  "DANCE_PITCH_PIVOT",       "Verse 1",   "Kude yatin — G→G7, look up"),
    (81.0,  "DANCE_BEG_WAVE",          "Verse 1",   "Epa thaniya — C→G, pleading beg"),
    (85.0,  "DANCE_PEACOCK",           "Verse 1",   "Denenna — C resolve, open display"),
    (89.0,  "DANCE_CHASSIS_BREATHE",  "Verse 1",   "Settle before inter"),

    # ── INTER / BRIDGE (1:30 – 1:50) ────────────────────────────────────
    # Quiet instrumental — robot goes soft and contemplative
    (92.0,  "DANCE_WAVE",              "Inter",     "Inter C"),
    (96.0,  "DANCE_BEG_WAVE",          "Inter",     "Inter Am"),
    (100.0, "DANCE_CHASSIS_BREATHE",  "Inter",     "Inter F — breathe"),
    (104.0, "DANCE_PEACOCK",           "Inter",     "Inter G — hold display"),
    (108.0, "DANCE_RIPPLE",            "Inter",     "Dm→G7 — ripple leading to chorus"),

    # ── CHORUS 2 (1:50 – 2:22) ──────────────────────────────────────────
    # Same melody but MORE expressive — bigger moves, more energy
    (110.0, "DANCE_SALSA",             "Chorus 2",  "Bigger! C — salsa burst"),
    (113.0, "DANCE_PITCH_PIVOT",       "Chorus 2",  "Am — dramatic sway"),
    (116.5, "DANCE_ROLL",              "Chorus 2",  "F — smooth roll"),
    (119.5, "DANCE_RIPPLE",            "Chorus 2",  "C — cascade"),
    (122.5, "DANCE_HEADBANG",          "Chorus 2",  "G — emotional head nod"),
    (125.5, "DANCE_SALSA",             "Chorus 2",  "G7 — energy salsa"),
    (128.5, "DANCE_PEACOCK",           "Chorus 2",  "C — full proud display"),
    (131.5, "DANCE_TWIST",             "Chorus 2",  "G — spinning twist"),
    (134.5, "DANCE_ROLL_FAST",         "Chorus 2",  "G7 — quick spin"),
    (137.5, "DANCE_RIPPLE",            "Chorus 2",  "C — waterfall ripple"),
    (141.0, "DANCE_SALSA",             "Chorus 2",  "High energy — bridge to verse 2"),

    # ── VERSE 2 (2:22 – 3:00) ───────────────────────────────────────────
    # Building toward the emotional ending
    (142.0, "DANCE_TWIST",             "Verse 2",   "Latha madulu — C"),
    (145.5, "DANCE_CIRCLE",            "Verse 2",   "Atha wanawi — Em, circle"),
    (149.0, "DANCE_RIPPLE_2",          "Verse 2",   "Epa ahaka — Am"),
    (152.5, "DANCE_WAVE",              "Verse 2",   "Balanna — F→C"),
    (156.5, "DANCE_TWIST",             "Verse 2",   "Repeat — C"),
    (160.0, "DANCE_CIRCLE",            "Verse 2",   "Em"),
    (163.5, "DANCE_PITCH_PIVOT",       "Verse 2",   "Maa geana — G, emotional sway"),
    (167.5, "DANCE_HEADBANG",          "Verse 2",   "Mathakaya guli — G7, nodding"),
    (171.5, "DANCE_PEACOCK",           "Verse 2",   "Maha weal — C, grand display"),
    (175.5, "DANCE_SALSA",             "Verse 2",   "Iyata — G, rising"),
    (179.5, "DANCE_RIPPLE",            "Verse 2",   "Damanna — C, flowing resolve"),

    # ── OUTRO / FADE (3:00 – end) ────────────────────────────────────────
    # Winding down — return to stillness
    (183.0, "DANCE_CHASSIS_BREATHE",  "Outro",     "Settle — C"),
    (187.0, "DANCE_WAVE",              "Outro",     "Farewell wave — Am"),
    (191.0, "DANCE_BEG_WAVE",          "Outro",     "Last moonlit beg — F"),
    (196.0, "DANCE_PEACOCK",           "Outro",     "Final open display — G7→C"),
    (201.0, "DANCE_CHASSIS_BREATHE",  "Outro",     "Breathe and rest"),
    (208.0, "STAND",                   "Outro",     "Song ends — stand still"),
]


# ---------------------------------------------------------------------------
# Choreography Runner
# ---------------------------------------------------------------------------
def run_choreography(start_offset: float = 0.0):
    """
    Runs the predefined choreography timeline.

    Args:
        start_offset: If you're starting mid-song (e.g. already 30s in),
                      pass 30.0 here to skip to the right section.
    """
    print("\n" + "="*60)
    print("  🎵 HEXABOT CHOREO — හන්තානට පායන සඳ")
    print("  🎸 Amarasiri Peiris | 152 BPM | C Major")
    print("="*60)

    # Filter moves that haven't happened yet
    pending = [(t, cmd, sec, note) for t, cmd, sec, note in CHOREOGRAPHY
               if t >= start_offset]

    if not pending:
        print("❌ No moves left for the given start offset.")
        return

    start_time = time.monotonic() - start_offset
    last_section = None

    print(f"\n▶  Starting choreography (offset = {start_offset:.1f}s)...")
    print(f"   First move in {pending[0][0] - start_offset:.1f} seconds: {pending[0][1]}\n")

    for song_time, command, section, note in pending:
        # Wait until it's time for this move
        target_time = start_time + song_time
        sleep_duration = target_time - time.monotonic()
        if sleep_duration > 0:
            time.sleep(sleep_duration)

        # Print section header when it changes
        if section != last_section:
            print(f"\n  ── {section} ──")
            last_section = section

        elapsed = time.monotonic() - start_time
        print(f"  [{elapsed:6.1f}s] 💃 {command:<30}  ← {note}")

        send_to_esp32(command)
        log_event(f"🎭 [CHOREO] t={elapsed:.1f}s | {command} | {section} | {note}")

    print("\n✅ Choreography complete. Robot standing by.\n")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n🎵 Hanthanata Payana Sanda — Hexabot Choreography")
    print("="*54)

    offset = 0.0
    if len(sys.argv) > 1:
        try:
            offset = float(sys.argv[1])
            print(f"⏩ Starting from {offset:.1f}s into the song")
        except ValueError:
            pass

    print("\n  Start the song NOW, then press Enter...")
    try:
        input()
    except KeyboardInterrupt:
        sys.exit(0)

    try:
        run_choreography(start_offset=offset)
    except KeyboardInterrupt:
        print("\n⏹️  Choreography interrupted.")
        send_to_esp32("STAND")