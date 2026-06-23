#!/usr/bin/env python3
"""
=======================================================================
  HEXABOT CHOREOGRAPHY — හන්තානට පායන සඳ (Hanthanata Payana Sanda)
  Artist  : Amarasiri Peiris
  BPM     : 152  |  Key : C Major  |  Beat interval : ~0.395 s
=======================================================================

HOW TO USE:

  OPTION A — Run from inside hexabot_os.py (RECOMMENDED):
    At the bottom of hexabot_os.py, add:
        from hanthaneta_dance import run_choreography
        run_choreography()

  OPTION B — Run standalone (no hexabot_os.py needed):
        sudo python3 hanthaneta_dance.py

  OPTION C — Start mid-song (e.g. 30 seconds in):
        sudo python3 hanthaneta_dance.py 30.0

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
import math

# ---------------------------------------------------------------------------
# Serial / Send Setup
# ---------------------------------------------------------------------------
# Try to reuse the already-open connection from hexabot_os.py.
# If this script is run standalone, we open our own serial connection.
# IMU (TILT) data from ESP32 is simply ignored — it does not block anything.
# ---------------------------------------------------------------------------

_send_fn = None       # Will hold the send_to_esp32 function we'll use
_serial_obj = None    # Only used in standalone mode

def _init_standalone():
    """Open serial in standalone mode, with no dependency on hexabot_os.py."""
    global _serial_obj

    import serial
    import logging

    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hexabot.log")
    logging.basicConfig(filename=log_path, level=logging.INFO,
                        format='%(asctime)s | %(message)s', datefmt='%H:%M:%S')

    for port in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0"):
        try:
            _serial_obj = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Connected to ESP32 on {port}")
            break
        except Exception:
            continue

    if _serial_obj is None:
        print("⚠️  ESP32 not found — SIMULATION MODE (commands printed only).")

    # Background reader: drains ESP32 output (TILT spam etc.) without blocking
    def _drain_esp32():
        while True:
            if _serial_obj and _serial_obj.is_open:
                try:
                    line = _serial_obj.readline().decode("utf-8", errors="ignore").strip()
                    # Silently drop TILT lines; print everything else
                    if line and not line.startswith("TILT:"):
                        print(f"  🤖 ESP32: {line}")
                except Exception:
                    time.sleep(0.1)
            else:
                time.sleep(0.1)

    threading.Thread(target=_drain_esp32, daemon=True).start()

    def _send(command: str):
        print(f"  📡 SEND → {command}")
        if _serial_obj and _serial_obj.is_open:
            try:
                _serial_obj.write((command + "\n").encode("utf-8"))
                _serial_obj.flush()
            except Exception as e:
                print(f"  ❌ Serial error: {e}")

    return _send


def _resolve_send_fn():
    """
    Return the best available send_to_esp32 function.
    Priority:
      1. hexabot_os.py is the __main__ module  → reuse its send_to_esp32
      2. hexabot_os was imported elsewhere      → import from it
      3. Standalone                             → open our own serial
    """
    # Case 1: launched via hexabot_os.py
    import sys
    main_mod = sys.modules.get("__main__")
    if main_mod and hasattr(main_mod, "send_to_esp32"):
        print("✅ Reusing send_to_esp32 from running Hexabot OS.")
        return main_mod.send_to_esp32

    # Case 2: hexabot_os was imported as a module
    hexabot = sys.modules.get("hexabot_os")
    if hexabot and hasattr(hexabot, "send_to_esp32"):
        print("✅ Reusing send_to_esp32 from imported hexabot_os module.")
        return hexabot.send_to_esp32

    # Case 3: standalone
    print("ℹ️  Running in STANDALONE mode.")
    return _init_standalone()


# ---------------------------------------------------------------------------
# Choreography Timeline
# ---------------------------------------------------------------------------
# Each entry: (song_time_seconds, dance_command, section_label, note)
#
# Beat interval at 152 BPM = 0.395 s
# ---------------------------------------------------------------------------

CHOREOGRAPHY = [
    # ── INTRO (0:00 – 0:20) ─────────────────────────────────────────────
    (0.0,   "DANCE_CHASSIS_BREATHE",  "Intro",     "Wake up — gentle sway on C"),
    (3.5,   "DANCE_BEG_WAVE",         "Intro",     "Curious moon-gazing wave on Am"),
    (6.5,   "DANCE_WAVE",             "Intro",     "Gentle ripple on F"),
    (9.5,   "DANCE_CHASSIS_BREATHE",  "Intro",     "Rest, breathe on G"),
    (12.5,  "DANCE_PEACOCK",          "Intro",     "Proud slow display on Am→G"),
    (16.0,  "DANCE_WAVE",             "Intro",     "Flow into pre-chorus on Dm→G7"),

    # ── CHORUS 1 (0:20 – 0:52) ──────────────────────────────────────────
    (20.0,  "DANCE_RIPPLE",           "Chorus 1",  "Moonlight ripple — C"),
    (22.5,  "DANCE_PITCH_PIVOT",      "Chorus 1",  "Swaying look up to the moon — Am"),
    (25.5,  "DANCE_PEACOCK",          "Chorus 1",  "Open up, display — F"),
    (28.5,  "DANCE_RIPPLE",           "Chorus 1",  "Cascade back — C"),
    (31.5,  "DANCE_ROLL",             "Chorus 1",  "Gentle roll on G"),
    (34.5,  "DANCE_PITCH_PIVOT",      "Chorus 1",  "Lean and return — G7"),
    (37.5,  "DANCE_WAVE",             "Chorus 1",  "Chorus resolve — C"),
    (40.5,  "DANCE_PEACOCK",          "Chorus 1",  "Full display — G"),
    (43.5,  "DANCE_RIPPLE",           "Chorus 1",  "Ripple through — G7"),
    (46.5,  "DANCE_CHASSIS_BREATHE",  "Chorus 1",  "Breathe out — C resolve"),
    (50.0,  "DANCE_WAVE",             "Chorus 1",  "Bridge into verse"),

    # ── VERSE 1 (0:52 – 1:30) ───────────────────────────────────────────
    (52.0,  "DANCE_TWIST",            "Verse 1",   "Anduru lala — C, light twist"),
    (55.5,  "DANCE_CIRCLE",           "Verse 1",   "Wahina kala — Em, small circle"),
    (59.0,  "DANCE_RIPPLE_2",         "Verse 1",   "Sarasawi bima — Am, ripple 2"),
    (62.5,  "DANCE_WAVE",             "Verse 1",   "Themenna — F→C, gentle wave"),
    (66.5,  "DANCE_TWIST",            "Verse 1",   "Repeat — C"),
    (70.0,  "DANCE_CIRCLE",           "Verse 1",   "Em again — light spin"),
    (73.5,  "DANCE_RIPPLE",           "Verse 1",   "Am flows"),
    (77.0,  "DANCE_PITCH_PIVOT",      "Verse 1",   "Kude yatin — G→G7, look up"),
    (81.0,  "DANCE_BEG_WAVE",         "Verse 1",   "Epa thaniya — C→G, pleading beg"),
    (85.0,  "DANCE_PEACOCK",          "Verse 1",   "Denenna — C resolve, open display"),
    (89.0,  "DANCE_CHASSIS_BREATHE",  "Verse 1",   "Settle before inter"),

    # ── INTER / BRIDGE (1:30 – 1:50) ────────────────────────────────────
    (92.0,  "DANCE_WAVE",             "Inter",     "Inter C"),
    (96.0,  "DANCE_BEG_WAVE",         "Inter",     "Inter Am"),
    (100.0, "DANCE_CHASSIS_BREATHE",  "Inter",     "Inter F — breathe"),
    (104.0, "DANCE_PEACOCK",          "Inter",     "Inter G — hold display"),
    (108.0, "DANCE_RIPPLE",           "Inter",     "Dm→G7 — ripple leading to chorus"),

    # ── CHORUS 2 (1:50 – 2:22) ──────────────────────────────────────────
    (110.0, "DANCE_SALSA",            "Chorus 2",  "Bigger! C — salsa burst"),
    (113.0, "DANCE_PITCH_PIVOT",      "Chorus 2",  "Am — dramatic sway"),
    (116.5, "DANCE_ROLL",             "Chorus 2",  "F — smooth roll"),
    (119.5, "DANCE_RIPPLE",           "Chorus 2",  "C — cascade"),
    (122.5, "DANCE_HEADBANG",         "Chorus 2",  "G — emotional head nod"),
    (125.5, "DANCE_SALSA",            "Chorus 2",  "G7 — energy salsa"),
    (128.5, "DANCE_PEACOCK",          "Chorus 2",  "C — full proud display"),
    (131.5, "DANCE_TWIST",            "Chorus 2",  "G — spinning twist"),
    (134.5, "DANCE_ROLL_FAST",        "Chorus 2",  "G7 — quick spin"),
    (137.5, "DANCE_RIPPLE",           "Chorus 2",  "C — waterfall ripple"),
    (141.0, "DANCE_SALSA",            "Chorus 2",  "High energy — bridge to verse 2"),

    # ── VERSE 2 (2:22 – 3:00) ───────────────────────────────────────────
    (142.0, "DANCE_TWIST",            "Verse 2",   "Latha madulu — C"),
    (145.5, "DANCE_CIRCLE",           "Verse 2",   "Atha wanawi — Em, circle"),
    (149.0, "DANCE_RIPPLE_2",         "Verse 2",   "Epa ahaka — Am"),
    (152.5, "DANCE_WAVE",             "Verse 2",   "Balanna — F→C"),
    (156.5, "DANCE_TWIST",            "Verse 2",   "Repeat — C"),
    (160.0, "DANCE_CIRCLE",           "Verse 2",   "Em"),
    (163.5, "DANCE_PITCH_PIVOT",      "Verse 2",   "Maa geana — G, emotional sway"),
    (167.5, "DANCE_HEADBANG",         "Verse 2",   "Mathakaya guli — G7, nodding"),
    (171.5, "DANCE_PEACOCK",          "Verse 2",   "Maha weal — C, grand display"),
    (175.5, "DANCE_SALSA",            "Verse 2",   "Iyata — G, rising"),
    (179.5, "DANCE_RIPPLE",           "Verse 2",   "Damanna — C, flowing resolve"),

    # ── OUTRO / FADE (3:00 – end) ────────────────────────────────────────
    (183.0, "DANCE_CHASSIS_BREATHE",  "Outro",     "Settle — C"),
    (187.0, "DANCE_WAVE",             "Outro",     "Farewell wave — Am"),
    (191.0, "DANCE_BEG_WAVE",         "Outro",     "Last moonlit beg — F"),
    (196.0, "DANCE_PEACOCK",          "Outro",     "Final open display — G7→C"),
    (201.0, "DANCE_CHASSIS_BREATHE",  "Outro",     "Breathe and rest"),
    (208.0, "STAND",                  "Outro",     "Song ends — stand still"),
]


# ---------------------------------------------------------------------------
# Choreography Runner
# ---------------------------------------------------------------------------
def run_choreography(start_offset: float = 0.0, send_fn=None):
    """
    Runs the predefined choreography timeline.

    Args:
        start_offset: Seconds already elapsed in the song (to skip ahead).
        send_fn:      Optional custom send function. If None, auto-detected.
    """
    global _send_fn
    if send_fn:
        _send_fn = send_fn
    if _send_fn is None:
        _send_fn = _resolve_send_fn()

    print("\n" + "=" * 60)
    print("  🎵 HEXABOT CHOREO — හන්තානට පායන සඳ")
    print("  🎸 Amarasiri Peiris | 152 BPM | C Major")
    print("=" * 60)

    pending = [(t, cmd, sec, note) for t, cmd, sec, note in CHOREOGRAPHY
               if t >= start_offset]

    if not pending:
        print("❌ No moves left for the given start offset.")
        return

    start_time = time.monotonic() - start_offset
    last_section = None

    print(f"\n▶  Starting choreography (offset = {start_offset:.1f}s)...")
    print(f"   First move in {pending[0][0] - start_offset:.1f}s → {pending[0][1]}\n")

    for song_time, command, section, note in pending:
        target_time = start_time + song_time
        sleep_duration = target_time - time.monotonic()
        if sleep_duration > 0:
            time.sleep(sleep_duration)

        if section != last_section:
            print(f"\n  ── {section} ──")
            last_section = section

        elapsed = time.monotonic() - start_time
        print(f"  [{elapsed:6.1f}s] 💃 {command:<30}  ← {note}")
        _send_fn(command)

    print("\n✅ Choreography complete. Robot standing by.\n")


# ---------------------------------------------------------------------------
# Entry Point (standalone mode)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n🎵 Hanthanata Payana Sanda — Hexabot Choreography")
    print("=" * 54)

    offset = 0.0
    if len(sys.argv) > 1:
        try:
            offset = float(sys.argv[1])
            print(f"⏩ Starting from {offset:.1f}s into the song")
        except ValueError:
            pass

    # Resolve serial connection before asking user to press Enter
    # so any "ESP32 not found" warnings appear before the countdown.
    _send_fn = _resolve_send_fn()

    print("\n  Start the song NOW, then press Enter...")
    try:
        input()
    except KeyboardInterrupt:
        sys.exit(0)

    try:
        run_choreography(start_offset=offset, send_fn=_send_fn)
    except KeyboardInterrupt:
        print("\n⏹️  Choreography interrupted.")
        _send_fn("STAND")