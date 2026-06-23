#!/usr/bin/env python3
"""
=======================================================================
  HEXABOT CHOREOGRAPHY — හන්තානට පායන සඳ (Hanthanata Payana Sanda)
  Artist  : Amarasiri Peiris
  BPM     : 152  |  Key : C Major  |  Beat interval : ~0.395 s
=======================================================================

HOW TO USE:
  OPTION A — Standalone:
        sudo python3 hanthaneta_dance.py

  OPTION B — Start mid-song (e.g. 30 seconds in):
        sudo python3 hanthaneta_dance.py 30.0

HOW SYNC WORKS (beat-locked, ABORT-on-deadline):
  The song clock is the master. Each move has a hard deadline — the
  timestamp of the NEXT move. When that deadline arrives, we send
  ABORT so the ESP32 exits its current motion immediately, then send
  the next command right away. The robot resumes from its current leg
  positions, giving a smooth mid-motion transition instead of a stall.

  If the ESP32 sends READY before the deadline (move finished early),
  we skip the remaining wait and proceed to the next beat early — so
  short moves don't leave the robot frozen.

  The result: moves are always beat-locked, never pile up in the serial
  buffer, and late-running moves are gracefully cut off.
=======================================================================
"""

import time
import threading
import sys

# ---------------------------------------------------------------------------
# Serial / Send Setup
# ---------------------------------------------------------------------------

_serial_obj = None
_ready_event = threading.Event()   # set when ESP32 sends "READY"


def _init_standalone():
    global _serial_obj

    import serial

    for port in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/serial0"):
        try:
            _serial_obj = serial.Serial(port, 115200, timeout=1)
            print(f"✅ Connected to ESP32 on {port}")
            break
        except Exception:
            continue

    if _serial_obj is None:
        print("⚠️  ESP32 not found — SIMULATION MODE (commands printed only).")
        return

    def _reader():
        """
        Reads all ESP32 output continuously.
        - TILT lines  → silently dropped (IMU noise)
        - READY       → sets _ready_event so choreography can proceed early
        - anything else → printed for debugging
        """
        while True:
            if _serial_obj and _serial_obj.is_open:
                try:
                    raw = _serial_obj.readline()
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    if line.startswith("TILT:"):
                        pass  # drop silently
                    elif line == "READY":
                        _ready_event.set()
                        print(f"  ✅ ESP32: READY")
                    else:
                        print(f"  🤖 ESP32: {line}")
                except Exception:
                    time.sleep(0.05)
            else:
                time.sleep(0.1)

    threading.Thread(target=_reader, daemon=True).start()


def _send(command: str):
    """Send a command to ESP32, or print it in simulation mode."""
    print(f"  📡 SEND → {command}")
    if _serial_obj and _serial_obj.is_open:
        try:
            _serial_obj.write((command + "\n").encode("utf-8"))
            _serial_obj.flush()
        except Exception as e:
            print(f"  ❌ Serial error: {e}")


def _resolve_send_fn():
    """
    If hexabot_os.py is already running, reuse its serial + reader.
    Otherwise open our own connection in standalone mode.
    """
    main_mod = sys.modules.get("__main__")
    if main_mod and hasattr(main_mod, "send_to_esp32"):
        print("✅ Reusing send_to_esp32 from running Hexabot OS.")
        return main_mod.send_to_esp32

    hexabot = sys.modules.get("hexabot_os")
    if hexabot and hasattr(hexabot, "send_to_esp32"):
        print("✅ Reusing send_to_esp32 from imported hexabot_os module.")
        return hexabot.send_to_esp32

    print("ℹ️  Running in STANDALONE mode.")
    _init_standalone()
    return _send


# ---------------------------------------------------------------------------
# Choreography Timeline
# ---------------------------------------------------------------------------
# Each entry: (song_time_seconds, dance_command, section_label, note)
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
# The command sent to the ESP32 to cleanly interrupt an in-progress move.
# The ESP32 firmware must handle this by stopping servo motion immediately
# and holding legs at their current positions (no snap-to-neutral).
# ---------------------------------------------------------------------------
ABORT_COMMAND = "ABORT"

# How long (seconds) to wait for READY after the final move before exiting.
FINAL_READY_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Choreography Runner
# ---------------------------------------------------------------------------

_send_fn = None


def _wait_for_beat(target_wall: float) -> bool:
    """
    Block until the song clock reaches target_wall, OR until the ESP32
    signals READY (move finished early) — whichever comes first.

    Returns True  if we exited because READY arrived early.
    Returns False if we exited because the deadline arrived.
    """
    remaining = target_wall - time.monotonic()
    if remaining <= 0:
        return False  # already past deadline

    # _ready_event was cleared just before the previous command was sent.
    # If it fires now it means the robot finished that move ahead of schedule.
    got_ready = _ready_event.wait(timeout=remaining)
    return got_ready


def run_choreography(start_offset: float = 0.0, send_fn=None):
    """
    Beat-locked choreography runner with ABORT-on-deadline.

    Timing strategy:
      For each move we know its start beat (song_time) and its deadline
      (the next move's song_time). We:
        1. Clear _ready_event and send the command.
        2. Wait until either:
             a. READY arrives (move done early → proceed to beat-wait), or
             b. The deadline arrives (move still running → send ABORT first).
        3. If we hit the deadline while the move is still running, send ABORT
           so the ESP32 halts servo motion at current positions, then fall
           straight through to step 4 without any additional wait.
        4. Wait for the exact beat timestamp (no-op if deadline already passed).
        5. Send the next command.

    This guarantees:
      - Commands are always sent at their correct song timestamps.
      - A slow move is gracefully cut off, never blocking the next beat.
      - A fast move doesn't leave the robot frozen — it resumes immediately
        after READY.
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

    # Anchor the song clock
    song_start = time.monotonic() - start_offset
    last_section = None

    print(f"\n▶  Starting choreography (offset = {start_offset:.1f}s)...")
    print(f"   First move in {max(0, pending[0][0] - start_offset):.1f}s → {pending[0][1]}")
    print()

    for i, (song_time, command, section, note) in enumerate(pending):

        # ── Step 1: Wait for the correct beat ───────────────────────────
        # On the very first move there's no previous move to abort, so we
        # just sleep until the beat arrives.
        target_wall = song_start + song_time
        wait = target_wall - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        else:
            drift = -wait
            if drift > 0.1:
                print(f"  ⏱️  Drift: {drift:.2f}s late")

        # ── Step 2: Print section header if changed ──────────────────────
        if section != last_section:
            print(f"\n  ── {section} ──")
            last_section = section

        # ── Step 3: Send the command ─────────────────────────────────────
        elapsed = time.monotonic() - song_start
        print(f"  [{elapsed:6.1f}s] 💃 {command:<30}  ← {note}")
        _ready_event.clear()   # arm for this move's READY signal
        _send_fn(command)

        # ── Step 4: Determine this move's deadline ───────────────────────
        # The deadline is the next move's beat. If this is the last move,
        # use a generous timeout instead.
        if i + 1 < len(pending):
            next_song_time = pending[i + 1][0]
            deadline_wall  = song_start + next_song_time
        else:
            # Final move — wait for READY or a long timeout
            _ready_event.wait(timeout=FINAL_READY_TIMEOUT)
            break

        # ── Step 5: Wait for READY or deadline ───────────────────────────
        # Whichever comes first:
        #   • READY early  → move done, we loop and sleep until next beat
        #   • Deadline hit → move still running, send ABORT then loop
        remaining = deadline_wall - time.monotonic()
        if remaining > 0:
            got_ready = _ready_event.wait(timeout=remaining)
            if not got_ready:
                # Deadline arrived while ESP32 is still moving — abort cleanly
                elapsed_dbg = time.monotonic() - song_start
                print(f"  ✂️  [{elapsed_dbg:6.1f}s] Deadline — sending {ABORT_COMMAND}")
                _send_fn(ABORT_COMMAND)
                # Clear any stale READY that arrives during/after abort
                _ready_event.clear()
        # else: we're already past the deadline (very slow machine) — don't
        # bother aborting, just loop straight to the next command.

    print("\n✅ Choreography complete. Robot standing by.\n")


# ---------------------------------------------------------------------------
# Entry Point (standalone)
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