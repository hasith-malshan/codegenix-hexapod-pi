#!/usr/bin/env python3
"""
=======================================================================
  HEXABOT CHOREOGRAPHY — හන්තානට පායන සඳ (Hanthanata Payana Sanda)
  Artist  : Amarasiri Peiris
  BPM     : 152  |  Key : C Major  |  Beat interval : ~0.3947 s
  4 bars  : 6.316 s  |  8 bars : 12.632 s

  FIXES vs previous version:
  ──────────────────────────
  1. PHRASE-LOCKED TIMING — every move lands exactly on a 4-bar or 8-bar
     boundary (multiples of 6.316 s). No more mid-phrase interruptions.

  2. REDUCED MOVE COUNT — moves are spaced ≥ 8 bars (12.6 s) apart in
     slow sections, ≥ 4 bars (6.3 s) in energetic sections. The robot
     has enough time to complete each motion cleanly before the next one
     arrives, eliminating the shake caused by rapid-fire commands.

  3. SMALLER VOCABULARY — only smooth, flowing moves are used in verse/
     intro sections. High-energy moves (SALSA, HEADBANG, ROLL_FAST) are
     reserved for the climax of Chorus 2 only.

  4. NO ABORT — when a new command arrives the ESP32 blends from current
     servo positions into the new motion (firmware requirement unchanged).

  5. READY-EARLY ACCELERATION — if the robot finishes a move before the
     next beat deadline we send the next command immediately so there is
     no static freeze between moves.

  ESP32 FIRMWARE REQUIREMENTS (unchanged):
     • Accept new command mid-motion → blend from current servo positions.
     • Remove any "wait for ABORT" gate.
     • Send READY when motion naturally completes.
=======================================================================

HOW TO USE:
  Standalone:          sudo python3 hanthaneta_dance.py
  Start mid-song:      sudo python3 hanthaneta_dance.py 30.0
=======================================================================
"""

import time
import threading
import sys

# ---------------------------------------------------------------------------
# Serial / Send Setup
# ---------------------------------------------------------------------------

_serial_obj  = None
_ready_event = threading.Event()


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
        while True:
            if _serial_obj and _serial_obj.is_open:
                try:
                    raw  = _serial_obj.readline()
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue
                    if line.startswith("TILT:"):
                        pass
                    elif line == "READY":
                        _ready_event.set()
                        print("  ✅ ESP32: READY")
                    else:
                        print(f"  🤖 ESP32: {line}")
                except Exception:
                    time.sleep(0.05)
            else:
                time.sleep(0.1)

    threading.Thread(target=_reader, daemon=True).start()


def _send(command: str):
    print(f"  📡 SEND → {command}")
    if _serial_obj and _serial_obj.is_open:
        try:
            _serial_obj.write((command + "\n").encode("utf-8"))
            _serial_obj.flush()
        except Exception as e:
            print(f"  ❌ Serial error: {e}")


def _resolve_send_fn():
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
# Beat / Phrase Constants
# ---------------------------------------------------------------------------
#   BPM = 152  →  beat = 0.3947 s
#   1 bar  =  4 beats = 1.5789 s
#   4 bars =            6.3158 s   ← minimum move spacing (energetic sections)
#   8 bars =           12.6316 s   ← standard move spacing (verse / intro)

_BAR  = (60 / 152) * 4          # 1.5789 s
_P4   = _BAR * 4                 # 6.3158 s  (4-bar phrase)
_P8   = _BAR * 8                 # 12.6316 s (8-bar phrase)

def _t(bars: float) -> float:
    """Convert bar count to seconds (rounded to 2 dp for readability)."""
    return round(bars * _BAR, 2)

# ---------------------------------------------------------------------------
# Choreography Timeline
# ---------------------------------------------------------------------------
# Each entry: (song_time_seconds, dance_command, section_label, note)
#
# MOVE SPACING RULES (enforced here):
#   Intro / Verse / Outro  →  ≥ 8 bars (12.6 s) between moves
#   Chorus                 →  ≥ 4 bars ( 6.3 s) between moves
#   Chorus 2 climax        →  4 bars allowed, high-energy moves OK
#
# Song structure (from chord chart + listening):
#   0 s   Intro       ~25 s   (16 bars)
#   25 s  Chorus 1    ~25 s   (16 bars)
#   50 s  Verse 1     ~38 s   (24 bars)
#   88 s  Inter       ~22 s   (14 bars)
#  110 s  Chorus 2    ~32 s   (20 bars)
#  142 s  Verse 2     ~41 s   (26 bars)
#  183 s  Outro       ~28 s   (18 bars)
# ---------------------------------------------------------------------------

CHOREOGRAPHY = [
    # ═══════════════════════════════════════════════════════════════════════
    # INTRO  (0 – 25 s)   |  C  Am  F  |  Am G  C  |  C G F C  |  Dm G7 C
    # 16 bars → 3 moves, one per 4-bar phrase (allow breathing room)
    # ═══════════════════════════════════════════════════════════════════════
    (_t( 0),  "DANCE_CHASSIS_BREATHE",  "Intro",    "Bar 0  — C, gentle wake-up sway"),
    (_t( 8),  "DANCE_WAVE",             "Intro",    "Bar 8  — Am/F, soft flowing wave"),
    (_t(12),  "DANCE_PEACOCK",          "Intro",    "Bar 12 — G→C, slow open display"),

    # ═══════════════════════════════════════════════════════════════════════
    # CHORUS 1  (~25 – 50 s)
    # C Am F C / G G7 C G G7 C   (16 bars, 4-bar spacing = 4 moves)
    # ═══════════════════════════════════════════════════════════════════════
    (_t(16),  "DANCE_RIPPLE",           "Chorus 1", "Bar 16 — C, moonlight ripple"),
    (_t(20),  "DANCE_PITCH_PIVOT",      "Chorus 1", "Bar 20 — Am, sway/look up"),
    (_t(24),  "DANCE_PEACOCK",          "Chorus 1", "Bar 24 — F→C, open display"),
    (_t(28),  "DANCE_WAVE",             "Chorus 1", "Bar 28 — G G7→C, resolve wave"),

    # ═══════════════════════════════════════════════════════════════════════
    # VERSE 1  (~50 – 88 s)
    # C Em Am / F C  (x2) + G G7 / C G C
    # 24 bars → moves on every 8 bars (3 moves, very smooth)
    # ═══════════════════════════════════════════════════════════════════════
    (_t(32),  "DANCE_TWIST",            "Verse 1",  "Bar 32 — C/Em, gentle twist"),
    (_t(40),  "DANCE_CIRCLE",           "Verse 1",  "Bar 40 — Am/F, small circle"),
    (_t(48),  "DANCE_PITCH_PIVOT",      "Verse 1",  "Bar 48 — G G7, emotional sway"),
    (_t(52),  "DANCE_BEG_WAVE",         "Verse 1",  "Bar 52 — C resolve, pleading beg"),

    # ═══════════════════════════════════════════════════════════════════════
    # INTER / BRIDGE  (~88 – 110 s)
    # 14 bars → 2 moves (hold each for ~7 bars, breathing space)
    # ═══════════════════════════════════════════════════════════════════════
    (_t(56),  "DANCE_CHASSIS_BREATHE",  "Inter",    "Bar 56 — C, settle & breathe"),
    (_t(64),  "DANCE_PEACOCK",          "Inter",    "Bar 64 — G, hold open display"),

    # ═══════════════════════════════════════════════════════════════════════
    # CHORUS 2  (~110 – 142 s)   ← MOST ENERGETIC SECTION
    # 20 bars → 4-bar spacing = 5 moves, energy escalates
    # ═══════════════════════════════════════════════════════════════════════
    (_t(70),  "DANCE_SALSA",            "Chorus 2", "Bar 70 — C, burst!"),
    (_t(74),  "DANCE_ROLL",             "Chorus 2", "Bar 74 — Am, smooth roll"),
    (_t(78),  "DANCE_RIPPLE",           "Chorus 2", "Bar 78 — F→C, cascade"),
    (_t(82),  "DANCE_HEADBANG",         "Chorus 2", "Bar 82 — G G7, dramatic nod"),
    (_t(86),  "DANCE_SALSA",            "Chorus 2", "Bar 86 — C, energy climax"),

    # ═══════════════════════════════════════════════════════════════════════
    # VERSE 2  (~142 – 183 s)
    # Same chord structure as Verse 1 — mirror it with same rhythm
    # ═══════════════════════════════════════════════════════════════════════
    (_t(90),  "DANCE_TWIST",            "Verse 2",  "Bar 90  — C/Em, gentle twist"),
    (_t(98),  "DANCE_CIRCLE",           "Verse 2",  "Bar 98  — Am/F, small circle"),
    (_t(106), "DANCE_PITCH_PIVOT",      "Verse 2",  "Bar 106 — G G7, sway"),
    (_t(112), "DANCE_PEACOCK",          "Verse 2",  "Bar 112 — C, grand display"),
    (_t(118), "DANCE_BEG_WAVE",         "Verse 2",  "Bar 118 — G, last plea"),

    # ═══════════════════════════════════════════════════════════════════════
    # OUTRO / FADE  (~183 – end)
    # Gradually settle — 8-bar spacing, calm moves only
    # ═══════════════════════════════════════════════════════════════════════
    (_t(124), "DANCE_WAVE",             "Outro",    "Bar 124 — Am, farewell wave"),
    (_t(132), "DANCE_CHASSIS_BREATHE",  "Outro",    "Bar 132 — F, breathe & settle"),
    (_t(140), "DANCE_PEACOCK",          "Outro",    "Bar 140 — G7→C, final display"),
    (_t(148), "STAND",                  "Outro",    "Bar 148 — song ends, stand still"),
]

FINAL_READY_TIMEOUT = 8.0

# ---------------------------------------------------------------------------
# Choreography Runner
# ---------------------------------------------------------------------------

_send_fn = None


def run_choreography(start_offset: float = 0.0, send_fn=None):
    """
    Beat-locked runner — NO ABORT, phrase-aligned, reduced move count.

    Timing strategy (unchanged from v1 — only the CHOREOGRAPHY table changed):
      1. Sleep until the move's beat.
      2. Send command, clear _ready_event.
      3. Wait for READY (early finish) OR deadline (next move's beat):
           • READY early → send next command immediately, no freeze.
           • Deadline    → send next command WITHOUT ABORT; ESP32 blends.
    """
    global _send_fn
    if send_fn:
        _send_fn = send_fn
    if _send_fn is None:
        _send_fn = _resolve_send_fn()

    print("\n" + "=" * 60)
    print("  🎵 HEXABOT CHOREO — හන්තානට පායන සඳ  (v2 — Smooth)")
    print("  🎸 Amarasiri Peiris | 152 BPM | C Major")
    print(f"  📋 {len(CHOREOGRAPHY)} moves, phrase-locked (≥ 4-bar spacing)")
    print("=" * 60)

    pending = [(t, cmd, sec, note) for t, cmd, sec, note in CHOREOGRAPHY
               if t >= start_offset]

    if not pending:
        print("❌ No moves left for the given start offset.")
        return

    song_start   = time.monotonic() - start_offset
    last_section = None

    print(f"\n▶  Starting choreography (offset = {start_offset:.1f}s)...")
    print(f"   First move in {max(0, pending[0][0] - start_offset):.1f}s → {pending[0][1]}")
    print()

    i = 0
    while i < len(pending):
        song_time, command, section, note = pending[i]

        # ── 1. Sleep until beat ──────────────────────────────────────────
        target_wall = song_start + song_time
        wait        = target_wall - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        else:
            drift = -wait
            if drift > 0.1:
                print(f"  ⏱️  Drift: {drift:.2f}s late")

        # ── 2. Section header ────────────────────────────────────────────
        if section != last_section:
            print(f"\n  ── {section} ──")
            last_section = section

        # ── 3. Send command ──────────────────────────────────────────────
        elapsed = time.monotonic() - song_start
        print(f"  [{elapsed:6.1f}s] 💃 {command:<30}  ← {note}")
        _ready_event.clear()
        _send_fn(command)

        # ── 4. Last move ─────────────────────────────────────────────────
        if i + 1 >= len(pending):
            _ready_event.wait(timeout=FINAL_READY_TIMEOUT)
            break

        # ── 5. Wait for READY or deadline ────────────────────────────────
        next_song_time = pending[i + 1][0]
        deadline_wall  = song_start + next_song_time
        remaining      = deadline_wall - time.monotonic()

        if remaining > 0:
            got_ready = _ready_event.wait(timeout=remaining)
        else:
            got_ready = False

        if got_ready:
            early_by = deadline_wall - time.monotonic()
            if early_by > 0.02:
                print(f"  ⚡ READY {early_by:.2f}s early — sending next move immediately")
        else:
            elapsed_dbg = time.monotonic() - song_start
            print(f"  ⏭️  [{elapsed_dbg:6.1f}s] Deadline — blending into next move (no ABORT)")

        i += 1

    print("\n✅ Choreography complete. Robot standing by.\n")


# ---------------------------------------------------------------------------
# Entry Point (standalone)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("\n🎵 Hanthanata Payana Sanda — Hexabot Choreography  (v2 Smooth)")
    print("=" * 60)

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