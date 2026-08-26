#!/usr/bin/env python3
"""tools/render_button_select_options.py — auditions replacement options for
the "button select" pair from HOTPOT_ARCHITECTURE_v3.md §15.2: `dwell_tick`
(the progress cue, one rung of "rising pitch ladder, 4 steps", fired every
300ms while a diner holds a dwell) and `dwell_fire` (the "clean confirm
chime" that fires once the dwell completes). Same stdlib-only approach as
tools/render_sfx.py, kept separate so it doesn't disturb that file's fixed
seed / full-set output.

Renders several candidate variants, each as its own dwell_tick.wav +
dwell_fire.wav, PLUS a demo.wav per variant that simulates a real dwell.
Variants 1-5 are timbre reskins of one additive-sine engine; 6-9 are a
from-scratch rethink, each on a different synthesis technique entirely
(Karplus-Strong plucked string, resonant-filtered noise, FM synthesis,
and a hybrid of the two percussive ones) so the audition actually spans
different *kinds* of sound, not just different frequencies of the same
kind:
four ticks at AudioBus's own rung->speed mapping (ofApp.cpp: speed =
clamp(0.9 + 0.12*(rung-1), 0.9, 1.5)), 300ms apart, followed by the fire.
That's what a diner actually hears, not just the isolated clips.

Run from anywhere:
    python tools/render_button_select_options.py

Writes into tools/sfx_audition_button_select/<variant>/.
"""

from __future__ import annotations

import math
import random
import wave
from pathlib import Path
from typing import List

SAMPLE_RATE = 44100


def _here() -> Path:
    return Path(__file__).resolve().parent


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
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    dt = 1.0 / SAMPLE_RATE
    alpha = dt / (rc + dt)
    out = []
    y = 0.0
    for x in samples:
        y = y + alpha * (x - y)
        out.append(y)
    return out


def _white_noise(seconds: float, rng: random.Random) -> List[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(_n(seconds))]


def _env_exp_decay(n: int, decay_per_sec: float, attack_s: float = 0.002) -> List[float]:
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
              amp: float, attack_s: float = 0.002, harmonic2: float = 0.0,
              harmonic3: float = 0.0) -> List[float]:
    n = _n(seconds)
    env = _env_exp_decay(n, decay_per_sec, attack_s)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        v = math.sin(2 * math.pi * freq * t)
        if harmonic2:
            v += harmonic2 * math.sin(2 * math.pi * freq * 2 * t)
        if harmonic3:
            v += harmonic3 * math.sin(2 * math.pi * freq * 3 * t)
        out.append(amp * env[i] * v)
    return out


def _square_ish_hit(freq: float, seconds: float, decay_per_sec: float,
                     amp: float, attack_s: float = 0.001) -> List[float]:
    """Sum of odd harmonics — a cheap square-wave approximation for the
    "digital" variant, distinct in timbre from the sine-based ones."""
    n = _n(seconds)
    env = _env_exp_decay(n, decay_per_sec, attack_s)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        v = (math.sin(2 * math.pi * freq * t)
             + 0.33 * math.sin(2 * math.pi * freq * 3 * t)
             + 0.2 * math.sin(2 * math.pi * freq * 5 * t))
        out.append(amp * env[i] * v)
    return out


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


def _resample_speed(clip: List[float], speed: float) -> List[float]:
    """Mimics ofSoundPlayer::setSpeed — linear-interpolated resample, so a
    demo sequence hears the same pitch-per-rung shift AudioBus applies on
    the rig (ofApp.cpp's rung->speed map)."""
    if speed == 1.0 or not clip:
        return list(clip)
    out_n = max(1, int(len(clip) / speed))
    out = []
    for i in range(out_n):
        src = i * speed
        i0 = int(src)
        frac = src - i0
        if i0 + 1 < len(clip):
            out.append(clip[i0] * (1 - frac) + clip[i0 + 1] * frac)
        else:
            out.append(clip[min(i0, len(clip) - 1)])
    return out


def _svf_bandpass(samples: List[float], freq: float, q: float) -> List[float]:
    """Chamberlin state-variable filter, bandpass output only — a resonant
    filter (not an oscillator) used to make noise ring at a pitch, the
    core trick behind the ratchet/hybrid variants below."""
    f = 2 * math.sin(math.pi * min(freq, SAMPLE_RATE / 4) / SAMPLE_RATE)
    qinv = 1.0 / q
    low = 0.0
    band = 0.0
    out = []
    for x in samples:
        high = x - low - qinv * band
        band += f * high
        low += f * band
        out.append(band)
    return out


def _ratchet_click(freq: float, rng: random.Random, q: float = 18.0,
                    amp: float = 0.5, burst_s: float = 0.006,
                    tail_s: float = 0.05, decay_per_sec: float = 60.0) -> List[float]:
    """A short noise impulse rung through a resonant bandpass — reads as a
    mechanical pawl/ratchet striking a tuned tooth, not a tone. Genuinely
    percussive rather than "percussive-shaped sine", which is what
    HOTPOT_ARCHITECTURE_v3.md §15.2 actually asks for."""
    n = _n(tail_s)
    burst_n = _n(burst_s)
    impulse = [rng.uniform(-1.0, 1.0) if i < burst_n else 0.0 for i in range(n)]
    ring = _svf_bandpass(impulse, freq, q)
    env = _env_exp_decay(n, decay_per_sec, attack_s=0.0005)
    peak = max((abs(v) for v in ring), default=1.0) or 1.0
    return [amp * env[i] * ring[i] / peak for i in range(n)]


def _karplus_strong(freq: float, seconds: float, decay: float, amp: float,
                     rng: random.Random) -> List[float]:
    """Karplus-Strong plucked string — a delay line seeded with noise and
    fed back through a lowpass average, physically modelling a struck/
    plucked object rather than approximating one with additive sine. Its
    own noise-burst attack + natural decay is inherently "percussive,
    mid-frequency, non-fatiguing on repeat" without any envelope tuning."""
    n_str = max(2, int(SAMPLE_RATE / freq))
    buf = [rng.uniform(-1.0, 1.0) for _ in range(n_str)]
    n = _n(seconds)
    out = []
    idx = 0
    for _ in range(n):
        out.append(buf[idx])
        nxt = buf[(idx + 1) % n_str]
        buf[idx] = decay * 0.5 * (buf[idx] + nxt)
        idx = (idx + 1) % n_str
    peak = max((abs(v) for v in out), default=1.0) or 1.0
    return [amp * v / peak for v in out]


def _fm_hit(carrier: float, mod_ratio: float, index: float, seconds: float,
            decay_per_sec: float, amp: float, attack_s: float = 0.002) -> List[float]:
    """True FM (modulator bends the carrier's phase, not summed as a
    harmonic) with an inharmonic mod_ratio — the classic DX-style struck-
    metal/bell/tine timbre, distinct in character from every additive-sine
    or square-ish variant tried so far. Index tracks the amp envelope so
    the hit opens bright and settles warm, like a real struck tine."""
    n = _n(seconds)
    env = _env_exp_decay(n, decay_per_sec, attack_s)
    out = []
    for i in range(n):
        t = i / SAMPLE_RATE
        mod = index * env[i] * math.sin(2 * math.pi * carrier * mod_ratio * t)
        v = math.sin(2 * math.pi * carrier * t + mod)
        out.append(amp * env[i] * v)
    return out


def _wood_tok(freq: float, rng: random.Random, amp: float = 0.5,
              seconds: float = 0.14, decay: float = 28.0) -> List[float]:
    body = _sine_hit(freq, seconds, decay_per_sec=decay, amp=amp)
    click = [v * 0.15 for v in _lowpass(_white_noise(0.01, rng), 4000)]
    return _overlay_at(body, click, 0.0)


def _rung_speed(rung: int) -> float:
    # ofApp.cpp's own mapping — kept identical so the demo previews match
    # what AudioBus will actually play on the rig.
    return max(0.9, min(1.5, 0.9 + 0.12 * (rung - 1)))


def _demo_sequence(tick: List[float], fire: List[float]) -> List[float]:
    """Four rungs 300ms apart (doc §15.2's dwell cadence), each played at
    AudioBus's own rung->speed, then the fire chime after a short beat."""
    out: List[float] = []
    t = 0.0
    for rung in range(1, 5):
        out = _overlay_at(_pad_to(out, t), _resample_speed(tick, _rung_speed(rung)), t)
        t += 0.30
    fire_start = t + 0.15
    out = _overlay_at(_pad_to(out, fire_start), fire, fire_start)
    return out


# ---------------------------------------------------------------------------
# Variants. Each returns (dwell_tick, dwell_fire).
# ---------------------------------------------------------------------------

def variant_current(rng: random.Random):
    """Baseline — today's render_sfx.py output, for A/B reference."""
    tick = _sine_hit(1800, 0.04, decay_per_sec=160, amp=0.30)
    a = _sine_hit(900, 0.16, decay_per_sec=10, amp=0.35)
    b = _sine_hit(1350, 0.20, decay_per_sec=9, amp=0.35)
    fire = _overlay_at(_pad_to(a, 0.06), b, 0.06)
    return tick, fire


def variant_soft_bell(rng: random.Random):
    """Rounder, glassier — a bell-like partial on the tick, a soft
    fifth-interval resolve on fire. Gentler than the baseline's plain sine."""
    tick = _sine_hit(2200, 0.05, decay_per_sec=130, amp=0.24, harmonic2=0.18)
    a = _sine_hit(660, 0.22, decay_per_sec=8, amp=0.30, harmonic2=0.15)
    b = _sine_hit(990, 0.26, decay_per_sec=7, amp=0.30, harmonic2=0.15)
    fire = _overlay_at(_pad_to(a, 0.08), b, 0.08)
    return tick, fire


def variant_wood_knock(rng: random.Random):
    """Matches the tactile "wooden tok" family used elsewhere in the set
    (pick_confirm/putback) — a percussive tick, and a firmer double-knock
    for the confirm so the whole dwell reads as one consistent material."""
    tick = _wood_tok(700, rng, amp=0.22, seconds=0.05, decay=90)
    hit1 = _wood_tok(260, rng, amp=0.45, seconds=0.12, decay=30)
    hit2 = _wood_tok(340, rng, amp=0.45, seconds=0.14, decay=26)
    fire = _overlay_at(_pad_to(hit1, 0.07), hit2, 0.07)
    return tick, fire


def variant_digital_blip(rng: random.Random):
    """Brighter, more "electronic" — square-ish blips instead of pure
    sine, a quick two-note upward arpeggio for the confirm."""
    tick = _square_ish_hit(1900, 0.035, decay_per_sec=200, amp=0.20)
    a = _square_ish_hit(1200, 0.10, decay_per_sec=35, amp=0.28)
    b = _square_ish_hit(1600, 0.14, decay_per_sec=22, amp=0.28)
    fire = _overlay_at(_pad_to(a, 0.05), b, 0.05)
    return tick, fire


def variant_marimba(rng: random.Random):
    """Mid-frequency struck-tone character — doc §15.2 favours mid
    frequencies over highs/lows for cutting through hall noise at 500mm.
    Tick is a single mallet hit; fire is a quick three-note resolve."""
    tick = _sine_hit(1200, 0.05, decay_per_sec=110, amp=0.28, harmonic3=0.12)
    notes = [_sine_hit(f, 0.16, decay_per_sec=18, amp=0.32, harmonic3=0.1)
             for f in (523.25, 659.25, 784.0)]
    fire = _pad_to(notes[0], 0.0)
    fire = _overlay_at(fire, notes[1], 0.05)
    fire = _overlay_at(fire, notes[2], 0.10)
    return tick, fire


def variant_plucked_string(rng: random.Random):
    """Complete rethink #1 — physically modelled (Karplus-Strong) instead
    of additive sine. Tick is one short pluck; fire is a root+fifth pluck
    chord, both mid-frequency per doc §15.2, both naturally soft-edged so
    200 repetitions read as an instrument, not a beep."""
    tick = _karplus_strong(700, seconds=0.14, decay=0.994, amp=0.30, rng=rng)
    root = _karplus_strong(523.25, seconds=0.30, decay=0.997, amp=0.32, rng=rng)
    fifth = _karplus_strong(784.0, seconds=0.30, decay=0.997, amp=0.28, rng=rng)
    fire = _overlay_at(_pad_to(root, 0.0), fifth, 0.03)
    return tick, fire


def variant_ratchet_click(rng: random.Random):
    """Complete rethink #2 — resonant-filtered noise, no oscillator at
    all. Reads as a mechanical pawl/ratchet catching a tooth: genuinely
    percussive (doc §15.2's own word) rather than a sine shaped to sound
    percussive. Fire is a lower "catch" click immediately followed by a
    longer resonant "latch" settling, like a mechanism locking home."""
    tick = _ratchet_click(1500, rng, q=20.0, amp=0.5, burst_s=0.005,
                           tail_s=0.045, decay_per_sec=90)
    catch = _ratchet_click(900, rng, q=14.0, amp=0.55, burst_s=0.006,
                            tail_s=0.05, decay_per_sec=70)
    latch = _ratchet_click(520, rng, q=22.0, amp=0.5, burst_s=0.008,
                            tail_s=0.16, decay_per_sec=22)
    fire = _overlay_at(_pad_to(catch, 0.05), latch, 0.05)
    return tick, fire


def variant_fm_bell(rng: random.Random):
    """Complete rethink #3 — FM synthesis, inharmonic partials (a struck-
    tine/bell character no additive-sine or square variant here can
    produce). Tick is one bright short strike; fire is two FM strikes
    resolving upward, index decaying with each envelope for a natural
    bright-to-warm settle."""
    tick = _fm_hit(1300, mod_ratio=1.4, index=3.5, seconds=0.05,
                    decay_per_sec=170, amp=0.22)
    a = _fm_hit(700, mod_ratio=1.4, index=4.0, seconds=0.18,
                decay_per_sec=16, amp=0.30)
    b = _fm_hit(1050, mod_ratio=1.4, index=3.0, seconds=0.22,
                decay_per_sec=13, amp=0.30)
    fire = _overlay_at(_pad_to(a, 0.07), b, 0.07)
    return tick, fire


def variant_hybrid_pluck(rng: random.Random):
    """Complete rethink #4 — a ratchet-click transient fused onto a
    Karplus-Strong body: the percussive "attack" doc §15.2 asks for, plus
    enough pitched body that a rising 4-step ladder still reads as
    music and not just four identical clicks. Fire pairs the same hit
    with a plucked fifth for a two-part resolve."""
    click = _ratchet_click(1800, rng, q=16.0, amp=0.35, burst_s=0.003,
                            tail_s=0.02, decay_per_sec=140)
    body = _karplus_strong(750, seconds=0.10, decay=0.992, amp=0.28, rng=rng)
    tick = _overlay_at(_pad_to(click, 0.0), body, 0.0)

    click2 = _ratchet_click(1400, rng, q=16.0, amp=0.35, burst_s=0.004,
                             tail_s=0.03, decay_per_sec=110)
    root = _karplus_strong(587.33, seconds=0.20, decay=0.995, amp=0.30, rng=rng)
    fifth = _karplus_strong(880.0, seconds=0.20, decay=0.995, amp=0.26, rng=rng)
    fire = _overlay_at(_pad_to(click2, 0.0), root, 0.0)
    fire = _overlay_at(fire, fifth, 0.04)
    return tick, fire


VARIANTS = {
    "1_current_baseline": variant_current,
    "2_soft_bell": variant_soft_bell,
    "3_wood_knock": variant_wood_knock,
    "4_digital_blip": variant_digital_blip,
    "5_marimba": variant_marimba,
    "6_plucked_string": variant_plucked_string,
    "7_ratchet_click": variant_ratchet_click,
    "8_fm_bell": variant_fm_bell,
    "9_hybrid_pluck": variant_hybrid_pluck,
}


def main() -> None:
    out_root = _here() / "sfx_audition_button_select"
    rng = random.Random(20260826)
    for name, make in VARIANTS.items():
        tick, fire = make(rng)
        out_dir = out_root / name
        _write_wav(out_dir / "dwell_tick.wav", tick)
        _write_wav(out_dir / "dwell_fire.wav", fire)
        _write_wav(out_dir / "demo_full_dwell.wav", _demo_sequence(tick, fire))
        print(f"wrote {name}/ (tick {len(tick)/SAMPLE_RATE:.2f}s, "
              f"fire {len(fire)/SAMPLE_RATE:.2f}s)")


if __name__ == "__main__":
    main()
