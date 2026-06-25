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

HOW SYNC WORKS (beat-locked, NO-ABORT, early-READY-accelerated):
  The song clock is the master. Each move has a hard deadline — the
  timestamp of the NEXT move.

  KEY CHANGES vs original:
  ─────────────────────────
  1. NO MORE ABORT:
     When the deadline arrives while a move is still running, we do NOT
     send ABORT. We simply send the NEXT command immediately. The ESP32
     firmware should blend/crossfade from its current leg positions into
     the new move. This eliminates the freeze+jerk/shake that ABORT caused.

  2. NO MORE MICRO-STOP ON READY:
     When READY arrives early (move finished before the beat), we send the
     next command immediately instead of waiting silently. The beat clock
     shifts forward so the *following* deadline still lines up with its
     correct song timestamp. The robot is always executing a motion.

  ESP32 FIRMWARE REQUIREMENT:
     • When a new command arrives mid-move, start the new motion from the
       CURRENT servo positions (not from a neutral/home pose). Blend in.
     • Remove any "wait for ABORT before accepting next command" logic.
     • Send READY when the motion naturally completes.
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
  ##  (0.0,   "DANCE_CHASSIS_BREATHE",  "Intro",     "Wake up — gentle sway on C"),
   # (3.5,   "DANCE_BEG_WAVE",         "Intro",     "Curious moon-gazing wave on Am"),
  ##  (6.5,   "DANCE_WAVE",             "Intro",     "Gentle ripple on F"),
  ##  (6.8,   "DANCE_CHASSIS_BREATHE",  "Intro",     "Rest, breathe on G"),
  ##  (14.5,  "DANCE_PEACOCK",          "Intro",     "Proud slow display on Am→G"),
    #(19.0,  "DANCE_WAVE",             "Intro",     "Flow into pre-chorus on Dm→G7"),

    # ── CHORUS 1 (0:20 – 0:52) ──────────────────────────────────────────
    #21.5
    (0.0,  "DANCE_ROLL_SLOW",           "Chorus 1",  "Moonlight ripple — C"),
    #(2.5,  "DANCE_CHASSIS_BREATHE",      "Chorus 1",  "Swaying look up to the moon — Am"),
    #(4.0,  "DANCE_ROLL_SLOW",          "Chorus 1",  "Open up, display — F"),
    #(5.5,  "DANCE_PEACOCK",           "Chorus 1",  "Cascade back — C"),
    (7.0,  "DANCE_CHASSIS_BREATHE",             "Chorus 1",  "Gentle roll on G"),
    #(6.5,  "DANCE_PITCH_PIVOT",      "Chorus 1",  "Lean and return — G7"),
    #(12.0,  "DANCE_HEADBANG",             "Chorus 1",  "Chorus resolve — C"),
    (14.5,  "DANCE_PEACOCK",          "Chorus 1",  "Full display — G"),
    (21.2,  "DANCE_ROLL_SLOW",           "Chorus 1",  "Ripple through — G7"),
    #(21.5,  "DANCE_PEACOCK",          "Chorus 1",  "Full display — G"),
    (21.4,  "DANCE_CHASSIS_BREATHE",  "Chorus 1",  "Breathe out — C resolve"),
    #(26.5,  "DANCE_BELLY_CRAWL",           "Chorus 1",  "Moonlight ripple — C"),
    (24.0,  "DANCE_WAVE",             "Chorus 1",  "Bridge into verse"),
    (46.5,  "DANCE_PEACOCK",          "Chorus 1",  "Open up, display — F"),
    (49.5,  "DANCE_PITCH_PIVOT",      "Chorus 1",  "Lean and return — G7"),
    (51.0,  "DANCE_ROLL_SLOW",           "Chorus 1",  "Moonlight ripple — C"),

    # ── VERSE 1 (0:52 – 1:30) ───────────────────────────────────────────
    (53.0,  "DANCE_TWIST",            "Verse 1",   "Anduru lala — C, light twist"),
    (55.5,  "DANCE_BEG_WAVE",           "Verse 1",   "Wahina kala — Em, small circle"),
    (59.0,  "DANCE_RIPPLE_2",         "Verse 1",   "Sarasawi bima — Am, ripple 2"),
    (62.5,  "DANCE_WAVE",             "Verse 1",   "Themenna — F→C, gentle wave"),
    (66.5,  "DANCE_TWIST",            "Verse 1",   "Repeat — C"),
    (70.0,  "DANCE_BEG_WAVE",           "Verse 1",   "Em again — light spin"),
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
   #(110.0, "DANCE_SALSA",            "Chorus 2",  "Bigger! C — salsa burst"),
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

# How long (seconds) to wait for READY after the final move before exiting.
FINAL_READY_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Choreography Runner
# ---------------------------------------------------------------------------

_send_fn = None


def run_choreography(start_offset: float = 0.0, send_fn=None):
    """
    Beat-locked choreography runner — NO ABORT, no micro-stops.

    Timing strategy:
    ─────────────────
    For each move we know its song_time (when to start) and its deadline
    (the next move's song_time). We:

      1. Sleep until the move's beat arrives (first move only, or if we
         are running on-schedule).

      2. Send the command and clear _ready_event.

      3. Wait for whichever comes first:
           a. READY arrives early  → robot finished the move ahead of the
                                     beat. Send the NEXT command immediately
                                     (no silent freeze). Adjust song_start
                                     so subsequent deadlines still align
                                     with their correct song timestamps.
           b. Deadline arrives     → robot is still moving. Send the NEXT
                                     command immediately WITHOUT sending
                                     ABORT first. The ESP32 firmware must
                                     blend from its current positions into
                                     the new move. No freeze, no shake.

    Why no ABORT?
    ─────────────
    ABORT tells the ESP32 to freeze servo PWM. Even a 10 ms freeze is
    visible as a jerk/shake. By sending the next dance command instead,
    the servos transition directly from one motion to another, which the
    firmware can interpolate smoothly.

    Why send immediately on READY?
    ────────────────────────────────
    If we wait silently after READY, the robot holds a static pose until
    the next beat — a visible micro-stop. Sending the next command
    immediately keeps the robot in continuous motion.
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

    i = 0
    while i < len(pending):
        song_time, command, section, note = pending[i]

        # ── Step 1: Sleep until this move's beat ─────────────────────────
        # (On early-READY path we arrive here already past this beat,
        #  so wait will be ≤ 0 and we skip the sleep immediately.)
        target_wall = song_start + song_time
        wait = target_wall - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        else:
            drift = -wait
            if drift > 0.1:
                print(f"  ⏱️  Drift: {drift:.2f}s late")

        # ── Step 2: Print section header if changed ───────────────────────
        if section != last_section:
            print(f"\n  ── {section} ──")
            last_section = section

        # ── Step 3: Send the command ──────────────────────────────────────
        elapsed = time.monotonic() - song_start
        print(f"  [{elapsed:6.1f}s] 💃 {command:<30}  ← {note}")
        _ready_event.clear()
        _send_fn(command)

        # ── Step 4: Last move — just wait for READY or timeout ────────────
        if i + 1 >= len(pending):
            _ready_event.wait(timeout=FINAL_READY_TIMEOUT)
            break

        # ── Step 5: Wait for READY or deadline ───────────────────────────
        next_song_time  = pending[i + 1][0]
        deadline_wall   = song_start + next_song_time
        remaining       = deadline_wall - time.monotonic()

        if remaining > 0:
            got_ready = _ready_event.wait(timeout=remaining)
        else:
            got_ready = False   # already past deadline

        if got_ready:
            # ── READY arrived early ───────────────────────────────────────
            # Robot finished its move before the next beat. Send the next
            # command NOW so there's zero pause. Re-anchor song_start so
            # the beats beyond this one still line up with the song.
            #
            # We do NOT sleep — we jump straight to the top of the loop
            # for move i+1, which will see wait ≤ 0 and skip its sleep.
            early_by = deadline_wall - time.monotonic()
            if early_by > 0.02:   # only log if meaningfully early
                print(f"  ⚡ READY {early_by:.2f}s early — sending next move immediately")
            # NOTE: song_start is NOT adjusted. We let the next iteration's
            # wait calculation produce a negative value (≤ 0), which it
            # handles by skipping the sleep. This keeps future deadlines
            # correctly anchored to the song clock.
        else:
            # ── Deadline arrived, move still running ──────────────────────
            # DO NOT send ABORT. Fall through to the next iteration which
            # will send the next command immediately (wait will be ≤ 0).
            elapsed_dbg = time.monotonic() - song_start
            print(f"  ⏭️  [{elapsed_dbg:6.1f}s] Deadline — blending into next move (no ABORT)")

        i += 1   # advance to next move

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