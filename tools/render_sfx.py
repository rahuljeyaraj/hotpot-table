#!/usr/bin/env python3
"""tools/render_sfx.py — renders doc HOTPOT_ARCHITECTURE_v3.md section 15.2's
sound set as WAV files, procedurally, with no external audio dependency
(stdlib `wave` + `math`/`random` only, same "no runtime dependency" spirit
as tools/render_tts.py section 16.3 will follow for voice).

**These are placeholder programmer-art, not a final mix.** They exist so
AudioBus (of/hotpot-table/src/AudioBus.cpp) has something to load and the
event wiring can be heard end-to-end on the rig before a real sound
designer's recordings replace them file-for-file — same id, same
`bin/data/audio/<id>.wav` path, nothing else to change on either side of
the wire.

Run from anywhere:
    python tools/render_sfx.py

Writes into of/hotpot-table/bin/data/audio/, next to this file's own
repo root (found by walking up for a directory containing "of").
"""

from __future__ import annotations

import math
import random
import wave
from pathlib import Path
from typing import List

SAMPLE_RATE = 44100


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "of" / "hotpot-table" / "bin" / "data").is_dir():
            return candidate
    raise SystemExit("render_sfx: could not find of/hotpot-table/bin/data "
                      "above this file — run from inside the repo")


def _write_wav(path: Path, samples: List[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = bytearray()
    for s in samples:
        v = max(-1.0, min(1.0, s))
        clipped += int(v * 32767).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(clipped))


def _n(seconds: float) -> int:
    return max(1, int(seconds * SAMPLE_RATE))


def _lowpass(samples: List[float], cutoff_hz: float) -> List[float]:
    """One-pole IIR lowpass — enough to turn white noise into something
    that reads as "soft"/"liquid" rather than static, without pulling in
    a DSP library for a handful of placeholder clips.
    """
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    dt = 1.0 / SAMPLE_RATE
    alpha = dt / (rc + dt)
    out = []
    y = 0.0
    for x in samples:
        y = y + alpha * (x - y)
        out.append(y)
    return out


def _highpass_diff(samples: List[float]) -> List[float]:
    """First difference — a crude highpass that gives noise the brighter,
    scratchier edge `spice_select`'s "sizzle" wants, distinct from
    `broth_select`'s lowpassed "liquid" noise.
    """
    out = [0.0] * len(samples)
    prev = 0.0
    for i, x in enumerate(samples):
        out[i] = x - prev
        prev = x
    return out


def _white_noise(seconds: float, rng: random.Random) -> List[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(_n(seconds))]


def _env_exp_decay(n: int, decay_per_sec: float, attack_s: float = 0.002) -> List[float]:
    """Fast linear attack into an exponential decay — the shape almost
    every percussive hit in this set is built from (a tick, a knock, a
    thud all differ mainly in fundamental frequency and decay rate, not
    in envelope shape).
    """
    attack_n = _n(attack_s)
    out = []
    for i in range(n):
        if i < attack_n:
            out.append(i / attack_n)
        else:
            t = (i - attack_n) / SAMPLE_RATE
            out.append(math.exp(-decay_per_sec * t))
    return out


def _sine_hit(freq: float, seconds: float, decay_per_sec: float,
              amp: float, attack_s: float = 0.002) -> List[float]:
    n = _n(seconds)
    env = _env_exp_decay(n, decay_per_sec, attack_s)
    return [amp * env[i] * math.sin(2 * math.pi * freq * i / SAMPLE_RATE)
            for i in range(n)]


def _mix(*layers: List[float]) -> List[float]:
    n = max(len(l) for l in layers)
    out = [0.0] * n
    for layer in layers:
        for i, v in enumerate(layer):
            out[i] += v
    return out


def _concat(*clips: List[float], gap_s: float = 0.0) -> List[float]:
    gap = [0.0] * _n(gap_s) if gap_s > 0 else []
    out: List[float] = []
    for i, clip in enumerate(clips):
        out.extend(clip)
        if i < len(clips) - 1:
            out.extend(gap)
    return out


def _pad_to(clip: List[float], seconds: float) -> List[float]:
    n = _n(seconds)
    if len(clip) >= n:
        return clip
    return clip + [0.0] * (n - len(clip))


def _overlay_at(base: List[float], layer: List[float], at_s: float) -> List[float]:
    start = _n(at_s)
    out = list(base)
    if len(out) < start + len(layer):
        out.extend([0.0] * (start + len(layer) - len(out)))
    for i, v in enumerate(layer):
        out[start + i] += v
    return out


# ---------------------------------------------------------------------------
# doc section 15.2's set, one function per id.
# ---------------------------------------------------------------------------

def make_hover(rng: random.Random) -> List[float]:
    """"very soft tick, -18 dB" — a short, quiet high click."""
    return _sine_hit(3000, 0.05, decay_per_sec=140, amp=0.10)


def make_dwell_tick(rng: random.Random) -> List[float]:
    """"rising pitch ladder, 4 steps" — the ladder itself is played by
    AudioBus varying playback speed per rung (ofApp.cpp reads the wire's
    `rung` field); this is one clean rung's own tick.
    """
    return _sine_hit(1800, 0.04, decay_per_sec=160, amp=0.30)


def make_dwell_fire(rng: random.Random) -> List[float]:
    """"clean confirm chime" — two notes resolving upward."""
    a = _sine_hit(900, 0.16, decay_per_sec=10, amp=0.35)
    b = _sine_hit(1350, 0.20, decay_per_sec=9, amp=0.35)
    return _overlay_at(_pad_to(a, 0.06), b, 0.06)


_NOTE_RATIO = 2 ** (2 / 12)  # a whole tone / major second, "one note"


def _ceramic_tone(freq_scale: float, rng: random.Random) -> List[float]:
    """One note of the `pick_confirm`/`putback` chime — an inharmonic
    two-partial hit (a porcelain-bowl clink) plus a brief lowpassed noise
    click. `freq_scale` moves both partials together so the envelope and
    0.20s duration stay fixed and only the pitch moves — a real note
    change, not the clip played back faster.
    """
    tone = _mix(
        _sine_hit(660 * freq_scale, 0.20, decay_per_sec=34, amp=0.45),
        _sine_hit(1520 * freq_scale, 0.10, decay_per_sec=60, amp=0.14))
    click = [0.18 * v * math.exp(-90 * i / SAMPLE_RATE)
             for i, v in enumerate(_lowpass(_white_noise(0.025, rng), 900))]
    return _overlay_at(tone, click, 0.0)


def make_pick_confirm(rng: random.Random) -> List[float]:
    """Developer spec (2026-08-26, after auditioning four candidate
    families on the rig): the ceramic chime, second note a whole tone
    ABOVE the first — pitch shift by grams (ofApp.cpp) still applies on
    top of this at playback, same as every id here.
    """
    base = _ceramic_tone(1.0, rng)
    higher = _ceramic_tone(_NOTE_RATIO, rng)
    return _concat(base, higher, gap_s=0.035)


def make_putback(rng: random.Random) -> List[float]:
    """The same chime, second note a whole tone BELOW the first — no
    longer `pick_confirm` played backwards (that read as the same event
    as the pick; a rising vs. falling two-note phrase does not).
    """
    base = _ceramic_tone(1.0, rng)
    lower = _ceramic_tone(1.0 / _NOTE_RATIO, rng)
    return _concat(base, lower, gap_s=0.035)


def make_total_tick(rng: random.Random) -> List[float]:
    """"tiny click per digit roll" — higher and drier than `hover` so the
    two are never confused at 500mm.
    """
    return _sine_hit(2400, 0.03, decay_per_sec=220, amp=0.22)


def make_mode_setting(rng: random.Random) -> List[float]:
    """"two-tone descending" — entering setting mode."""
    a = _sine_hit(700, 0.14, decay_per_sec=14, amp=0.35)
    b = _sine_hit(500, 0.18, decay_per_sec=11, amp=0.35)
    return _concat(a, b, gap_s=0.02)


def make_mode_serving(rng: random.Random) -> List[float]:
    """"two-tone ascending" — the exact mirror of `mode_setting`."""
    a = _sine_hit(500, 0.14, decay_per_sec=14, amp=0.35)
    b = _sine_hit(700, 0.18, decay_per_sec=11, amp=0.35)
    return _concat(a, b, gap_s=0.02)


def make_broth_select(rng: random.Random) -> List[float]:
    """"a soft ladle-in-liquid sound" — lowpassed noise swell plus a low
    sine wobble for body.
    """
    n = _n(0.35)
    noise = _lowpass(_white_noise(0.35, rng), 900)
    env = []
    attack_n, total = _n(0.08), n
    for i in range(total):
        if i < attack_n:
            env.append(i / attack_n)
        else:
            env.append(math.exp(-6.0 * (i - attack_n) / SAMPLE_RATE))
    swell = [0.28 * env[i] * noise[i] for i in range(n)]
    wobble = [0.06 * env[i] * math.sin(2 * math.pi * 140 * i / SAMPLE_RATE)
              for i in range(n)]
    return _mix(swell, wobble)


def make_spice_select(rng: random.Random) -> List[float]:
    """"short sizzle" — brighter, drier noise than broth_select."""
    n = _n(0.18)
    noise = _highpass_diff(_white_noise(0.18, rng))
    env = _env_exp_decay(n, decay_per_sec=16, attack_s=0.005)
    return [0.55 * env[i] * noise[i] for i in range(n)]


def make_order_done(rng: random.Random) -> List[float]:
    """"warm three-note resolve" — a plain major triad, root to fifth."""
    notes = [_sine_hit(f, 0.22, decay_per_sec=7, amp=0.4)
             for f in (523.25, 659.25, 784.0)]
    out = _pad_to(notes[0], 0.0)
    out = _overlay_at(out, notes[1], 0.10)
    out = _overlay_at(out, notes[2], 0.20)
    return out


def make_error(rng: random.Random) -> List[float]:
    """"soft double thud, never a harsh buzzer" — two round low hits,
    sine only, no noise transient (that would read as a buzz).
    """
    hit = _sine_hit(150, 0.10, decay_per_sec=26, amp=0.45)
    return _concat(hit, hit, gap_s=0.09)


def make_attract(rng: random.Random) -> List[float]:
    """"idle loop, every 30s, almost inaudible simmer bed, loopable" — a
    long, very quiet lowpassed-noise bed with a slow amplitude wobble.
    Loop-seam clicks are faded out rather than solved properly (a real
    seamless loop wants a genuine crossfade against the file's own tail,
    which is a mixing job, not this placeholder's) — acceptable for a bed
    this quiet.
    """
    seconds = 6.0
    n = _n(seconds)
    noise = _lowpass(_white_noise(seconds, rng), 300)
    fade_n = _n(0.03)
    out = []
    for i in range(n):
        lfo = 0.6 + 0.4 * math.sin(2 * math.pi * 0.25 * i / SAMPLE_RATE)
        fade = 1.0
        if i < fade_n:
            fade = i / fade_n
        elif i > n - fade_n:
            fade = (n - i) / fade_n
        out.append(0.05 * lfo * fade * noise[i])
    return out


SOUNDS = {
    "hover": make_hover,
    "dwell_tick": make_dwell_tick,
    "dwell_fire": make_dwell_fire,
    "pick_confirm": make_pick_confirm,
    "putback": make_putback,
    "total_tick": make_total_tick,
    "mode_setting": make_mode_setting,
    "mode_serving": make_mode_serving,
    "broth_select": make_broth_select,
    "spice_select": make_spice_select,
    "order_done": make_order_done,
    "error": make_error,
    "attract": make_attract,
}


def main() -> None:
    out_dir = _repo_root() / "of" / "hotpot-table" / "bin" / "data" / "audio"
    # Fixed seed: reproducible output is worth more than variety for a
    # placeholder set nobody is meant to notice repeats in yet.
    rng = random.Random(20260826)
    for sound_id, make in SOUNDS.items():
        samples = make(rng)
        _write_wav(out_dir / f"{sound_id}.wav", samples)
        print(f"wrote {sound_id}.wav ({len(samples) / SAMPLE_RATE:.2f}s)")


if __name__ == "__main__":
    main()
