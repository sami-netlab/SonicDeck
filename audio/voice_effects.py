"""Local voice-effect DSP chain for SonicDeck's microphone section.

Pure numpy + scipy processing (no extra DSP dependencies beyond the Smart Mic
stack). The engine is stateful per stream and designed to run inside a
real-time audio callback: every operation is bounded, state is reset-able,
and process() can never raise into the audio thread — on internal failure the
block passes through dry.

v1.0 quality rework:
- pitch shifter uses smaller grains at 75% overlap (less warble/smearing)
- human-style presets add formant-style EQ (shelf/peaking) so they no longer
  sound like bare resampled "chipmunk/deep" pitch shifts
- subtle chorus masks granular artifacts on pitched voice presets
- each preset carries an out_gain so switching presets never jumps volume
- output stage has a soft-knee limiter: no clipping, no sudden level jumps
- wet=0 returns the signal bit-exact dry before any stateful op runs

Chain order per preset:
    pitch shift -> ring mod -> vibrato -> filters -> shelf/peaking EQ
    -> saturation -> chorus -> echo -> reverb -> wet/dry -> out_gain
    -> soft-knee limiter
"""

import math

import numpy as np
from scipy.signal import lfilter

# Preset ids in UI order. Each spec holds the *full-strength* parameters;
# the intensity control scales how strongly each op is applied.
PRESET_IDS = [
    "clean", "deep", "warm", "bright", "radio", "telephone",
    "robot", "monster", "tiny", "scifi", "echo", "reverb",
]

PRESETS = {
    # Clean: untouched voice.
    "clean":     {"out_gain": 1.0},
    # Deep Male: modest pitch drop + warmth + chest resonance, not cartoon.
    "deep":      {"pitch": 0.82, "hp": 55, "lp": 3800, "lowshelf": (180.0, 2.5),
                  "drive": 0.06, "chorus": (6.0, 0.10), "out_gain": 1.05},
    # Soft / Warm: no pitch change, smooth and rounded.
    "warm":      {"hp": 75, "lp": 7800, "lowshelf": (200.0, 2.5),
                  "peaking": (3000.0, -1.0, 1.2), "drive": 0.06, "out_gain": 1.0},
    # Bright: slight lift + presence, natural gender-neutral brightness.
    "bright":    {"pitch": 1.04, "hp": 110, "peaking": (3300.0, 2.0, 1.1),
                  "lp": 11000, "chorus": (5.0, 0.05), "out_gain": 0.98},
    # Radio Host: tight EQ + gentle saturation + presence.
    "radio":     {"hp": 180, "lp": 6500, "peaking": (2500.0, 2.5, 1.0),
                  "drive": 0.16, "out_gain": 1.0},
    # Telephone: classic narrow band.
    "telephone": {"hp": 500, "lp": 2400, "drive": 0.5, "out_gain": 1.0},
    # Robot: intentional ring-mod character.
    "robot":     {"ring_hz": 55, "ring_mix": 0.62, "hp": 120, "lp": 6500,
                  "drive": 0.10, "out_gain": 0.98},
    # Monster / Dark: big drop + chest + a little dirt and space.
    "monster":   {"pitch": 0.62, "lowshelf": (150.0, 3.0), "drive": 0.25,
                  "lp": 2400, "reverb_mix": 0.18, "out_gain": 0.9},
    # Tiny / High: strong lift, kept as bright small voice.
    "tiny":      {"pitch": 1.42, "hp": 160, "chorus": (6.0, 0.05), "out_gain": 1.12},
    # Sci-Fi: pitch + ring mod + wobble + space.
    "scifi":     {"pitch": 1.18, "ring_hz": 360, "ring_mix": 0.30,
                  "vib_hz": 5.5, "vib_depth": 0.0025, "reverb_mix": 0.20,
                  "out_gain": 0.98},
    "echo":      {"echo_ms": 220, "echo_fb": 0.36, "echo_mix": 0.34, "out_gain": 1.0},
    "reverb":    {"reverb_mix": 0.40, "out_gain": 1.0},
}
# Backward compatibility aliases
PRESETS["helium"] = PRESETS["tiny"]
PRESETS["alien"] = PRESETS["scifi"]
PRESETS["comic"] = {"pitch": 1.24, "vib_hz": 7.5, "vib_depth": 0.006, "hp": 150, "out_gain": 0.9}
PRESETS["villain"] = {"pitch": 0.72, "drive": 0.22, "lp": 3200, "reverb_mix": 0.35, "out_gain": 0.95}

_GRAIN = 768          # pitch-shift grain size (samples)
_HOP = _GRAIN // 4    # 75% overlap: much smoother crossfades than 50%
_OLA_NORM = 2.0       # Hann COLA sum at 75% overlap
_COMB_DELAYS = (997, 1317, 1741, 2083)  # Schroeder comb delays @ 48 kHz


def _smoothstep_mix(intensity: float) -> float:
    """Map the 0..1 intensity slider onto op strength, keeping some character
    audible even at low intensity."""
    return 0.4 + 0.6 * max(0.0, min(1.0, intensity))


def _biquad_band(freq: float, gain_db: float, q: float, sr: int, kind: str) -> tuple[list[float], list[float]]:
    """Cookbook shelf/peaking biquad coefficients."""
    a = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * freq / sr
    cos_w = math.cos(w0)
    sin_w = math.sin(w0)

    if kind == "peaking":
        alpha = sin_w / (2.0 * q)
        b0 = 1.0 + alpha * a
        b1 = -2.0 * cos_w
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cos_w
        a2 = 1.0 - alpha / a
    else:  # low shelf
        alpha = sin_w / 2.0 * math.sqrt((a + 1.0 / a) * (2.0 - 1.0) + 2.0)
        two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha
        b0 = a * ((a + 1.0) - (a - 1.0) * cos_w + two_sqrt_a_alpha)
        b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos_w)
        b2 = a * ((a + 1.0) - (a - 1.0) * cos_w - two_sqrt_a_alpha)
        a0 = (a + 1.0) + (a - 1.0) * cos_w + two_sqrt_a_alpha
        a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos_w)
        a2 = (a + 1.0) + (a - 1.0) * cos_w - two_sqrt_a_alpha
    return [b0 / a0, b1 / a0, b2 / a0], [1.0, a1 / a0, a2 / a0]


def _onepole_hp_bands(hz: float, sr: int) -> tuple[list[float], list[float]]:
    c = 1.0 - math.exp(-2.0 * math.pi * hz / sr)
    c = min(0.9995, max(0.0005, c))
    return [1.0 - c, -(1.0 - c)], [1.0, -(1.0 - c)]


class VoiceEffectEngine:
    """Stateful, block-based voice effect processor (mono float32)."""

    def __init__(self, sample_rate: int = 48000):
        self.enabled = False
        self.preset = "clean"
        self.intensity = 0.8   # 0..1
        self.wet = 0.9         # 0..1 wet/dry mix (1.0 = fully processed)
        self._sample_rate = 48000
        self._spec = PRESETS["clean"]
        self._grain_win = np.hanning(_GRAIN).astype(np.float32)
        self.reset(sample_rate)

    # ------------------------------------------------------------------ setup

    def set_sample_rate(self, sample_rate: int) -> None:
        if sample_rate and sample_rate != self._sample_rate:
            self.reset(sample_rate)

    def set_preset(self, preset: str) -> None:
        if preset not in PRESETS:
            preset = "clean"
        if preset != self.preset:
            self.preset = preset
            self._spec = PRESETS[preset]
            self.reset()

    def reset(self, sample_rate: int | None = None) -> None:
        """Clear all time-state (safe to call from any thread between blocks)."""
        if sample_rate:
            self._sample_rate = int(sample_rate)
        sr = self._sample_rate
        self._spec = PRESETS.get(self.preset, PRESETS["clean"])

        # Pitch-shift history (absolute-indexed input buffer) and pre-rolled
        # output buffer: output lags input by L = grain*ratio + margin samples
        # so every emitted block is fully overlap-added and no grain ever
        # reads input that has not arrived yet (removes periodic dips on
        # pitch-up presets).
        self._px_buf = np.zeros(0, dtype=np.float32)
        self._px_start = 0        # absolute input index of _px_buf[0]
        ratio = float(self._spec.get("pitch", 1.0) or 1.0)
        self._px_latency = int(_GRAIN * max(0.4, min(2.2, ratio))) + _HOP * 2 + 32
        self._px_out = np.zeros(self._px_latency, dtype=np.float32)
        self._px_out_start = -self._px_latency  # absolute output index of _px_out[0]
        self._px_k = 0            # next grain index to place
        # A dual-read-head delay shifter is used for pitch-up effects.  Its
        # complementary sine-squared crossfades cannot create the cancelling
        # grain seams that made Tiny/High periodically disappear.
        self._up_phase = 0.0
        self._up_delay = max(512, int(1024 * sr / 48000.0))
        self._up_history = np.zeros(self._up_delay + 4, dtype=np.float32)

        # Filter states (scipy direct-form II transposed).
        self._hp_zi = [np.zeros(1), np.zeros(1)]   # stage 1, stage 2 (cascaded)
        self._lp_zi = np.zeros(1)
        self._fx_biquad_zi = [np.zeros(2), np.zeros(2)]  # lowshelf, peaking
        self._out_hp_zi = np.zeros(1)

        # Ring-mod / vibrato / chorus phase.
        self._ring_phase = 0.0
        self._vib_phase = 0.0
        self._vib_buf = np.zeros(0, dtype=np.float32)
        self._vib_start = 0
        self._cho_phase = 0.0
        self._cho_buf = np.zeros(0, dtype=np.float32)
        self._cho_start = 0

        # Echo delay line.
        echo_ms = float(self._spec.get("echo_ms", 220))
        d = max(16, int(sr * echo_ms / 1000.0))
        self._echo_buf = np.zeros(d, dtype=np.float32)
        self._echo_pos = 0

        # Reverb comb banks.
        scale = sr / 48000.0
        self._comb_bufs = [np.zeros(max(8, int(n * scale)), dtype=np.float32)
                           for n in _COMB_DELAYS]
        self._comb_pos = [0] * len(self._comb_bufs)

    # ------------------------------------------------------------- processing

    @property
    def latency_samples(self) -> int:
        """Fixed latency used by the legacy downward granular shifter."""
        ratio = float(self._spec.get("pitch", 1.0))
        return self._px_latency if self.enabled and ratio < 1.0 else 0

    def process(self, block: np.ndarray) -> np.ndarray:
        """Process one mono float32 block. Never raises; never changes length."""
        if not self.enabled:
            return block
        try:
            x = np.asarray(block, dtype=np.float32).reshape(-1)
            if x.size == 0:
                return block
            # True dry path: before wet/dry mixing, no stateful op may touch x.
            if self.wet <= 0.0:
                return x
            dry = x.copy()
            spec = self._spec
            inten = _smoothstep_mix(self.intensity)
            sr = self._sample_rate

            ratio = float(spec.get("pitch", 1.0))
            if ratio != 1.0:
                r_eff = 1.0 + (ratio - 1.0) * (0.35 + 0.65 * self.intensity)
                x = self._pitch_shift(x, r_eff)

            ring_hz = spec.get("ring_hz")
            if ring_hz:
                x = self._ring_mod(x, float(ring_hz),
                                   float(spec.get("ring_mix", 0.6)) * inten, sr)

            vib_hz = spec.get("vib_hz")
            if vib_hz:
                x = self._vibrato(x, float(vib_hz),
                                  float(spec.get("vib_depth", 0.004)) * inten, sr)

            hp = spec.get("hp")
            if hp:
                x = self._highpass(x, float(hp))
            lp = spec.get("lp")
            if lp:
                x = self._lowpass(x, float(lp))

            shelf = spec.get("lowshelf")
            if shelf:
                x = self._biquad(x, shelf[0], shelf[1], 0.9, 0, "low")
            peak = spec.get("peaking")
            if peak:
                x = self._biquad(x, peak[0], peak[1], peak[2], 1, "peaking")

            drive = spec.get("drive")
            if drive:
                k = 1.0 + 5.0 * float(drive) * inten
                x = np.tanh(k * x) / math.tanh(k)

            chorus = spec.get("chorus")
            if chorus:
                x = self._chorus(x, float(chorus[0]), float(chorus[1]) * inten, sr)

            echo_mix = spec.get("echo_mix")
            if echo_mix:
                x = self._echo(x, float(spec.get("echo_fb", 0.4)),
                               float(echo_mix) * (0.35 + 0.65 * self.intensity))

            rv = spec.get("reverb_mix")
            if rv:
                x = self._reverb(x, float(rv) * (0.35 + 0.65 * self.intensity))

            wet = max(0.0, min(1.0, self.wet))
            out = wet * x + (1.0 - wet) * dry
            out = out * float(spec.get("out_gain", 1.0))

            # DC block on the processed path, then soft-knee limiter: no
            # clipping and no sudden volume jumps between presets.
            out = self._highpass(out, 35.0, zi_slot=2)
            out = self._soft_limit(out)
            out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
            np.clip(out, -1.0, 1.0, out=out)
            return out.astype(np.float32, copy=False)
        except Exception:
            try:
                self.reset()
            except Exception:
                pass
            return block

    # ------------------------------------------------------------ primitives

    def _pitch_shift(self, x: np.ndarray, ratio: float) -> np.ndarray:
        """Granular pitch shifter: grains read the input at hop _HOP and span
        G*ratio input samples resampled into G output samples, Hann-windowed
        with 75% overlap (COLA-normalized). Small grains + heavy overlap keep
        the artifacts far less warbly than a 50%-overlap design. The output
        stream lags the input by the grain latency, so grains are always
        fully covered by arrived input. Input consumption is 1:1, so state
        stays bounded."""
        if ratio > 1.0:
            return self._pitch_up(x, ratio)

        n = x.size
        buf = np.concatenate([self._px_buf, x]) if self._px_buf.size else \
            np.asarray(x, dtype=np.float32).copy()
        base = self._px_start
        in_total = base + buf.size
        win = self._grain_win
        j = np.arange(_GRAIN)
        span = int(_GRAIN * ratio) + 2

        # Place every grain whose input window has fully arrived.
        last_k = (in_total - span) // _HOP
        if last_k >= self._px_k:
            for k in range(self._px_k, int(last_k) + 1):
                pos = k * _HOP + j * ratio          # absolute input positions (float)
                i0 = pos.astype(np.int64)
                frac = (pos - i0).astype(np.float32)
                grain = (buf[i0 - base] * (1.0 - frac) + buf[i0 + 1 - base] * frac) * win
                o_off = k * _HOP - self._px_out_start
                if self._px_out.size < o_off + _GRAIN:
                    self._px_out = np.concatenate(
                        [self._px_out, np.zeros(o_off + _GRAIN - self._px_out.size, np.float32)])
                self._px_out[o_off:o_off + _GRAIN] += grain
            self._px_k = int(last_k) + 1

        # Emit exactly n fully overlap-added samples (pre-roll guarantees it).
        out = self._px_out[:n] * (_OLA_NORM ** -1)
        if out.size < n:  # only conceivable during the initial fill
            out = np.concatenate([out, np.zeros(n - out.size, np.float32)])
        self._px_out = self._px_out[n:]
        self._px_out_start += n

        # Trim input history no future grain will read (reads only move fwd).
        keep_abs = max(0, self._px_k * _HOP - 8)
        if keep_abs > base:
            cut = min(keep_abs - base, buf.size)
            buf = buf[cut:]
            base += cut
        self._px_buf, self._px_start = buf, base
        return out.astype(np.float32, copy=False)

    def _pitch_up(self, x: np.ndarray, ratio: float) -> np.ndarray:
        """Causal two-head variable-delay pitch-up with seamless crossfades."""
        n = x.size
        delay_span = float(self._up_delay)
        history = self._up_history
        buf = np.concatenate([history, np.asarray(x, dtype=np.float32)])
        current = history.size + np.arange(n, dtype=np.float64)
        step = max(0.0, ratio - 1.0) / delay_span
        phase1 = (self._up_phase + step * np.arange(n, dtype=np.float64)) % 1.0
        phase2 = (phase1 + 0.5) % 1.0

        def tap(phase: np.ndarray) -> np.ndarray:
            delay = 4.0 + (1.0 - phase) * delay_span
            pos = current - delay
            i0 = np.floor(pos).astype(np.int64)
            frac = (pos - i0).astype(np.float32)
            return buf[i0] * (1.0 - frac) + buf[i0 + 1] * frac

        # The weights sum exactly to one; each read head is silent at its own
        # delay-wrap discontinuity.
        w1 = np.sin(np.pi * phase1) ** 2
        w2 = np.sin(np.pi * phase2) ** 2
        out = tap(phase1) * w1 + tap(phase2) * w2
        self._up_phase = (self._up_phase + step * n) % 1.0
        self._up_history = buf[-(self._up_delay + 4):].copy()
        return out.astype(np.float32, copy=False)

    def _ring_mod(self, x: np.ndarray, freq: float, mix: float, sr: int) -> np.ndarray:
        n = x.size
        phase = (self._ring_phase + np.arange(n) * (2.0 * math.pi * freq / sr))
        self._ring_phase = (self._ring_phase + n * 2.0 * math.pi * freq / sr) % (2.0 * math.pi)
        carrier = np.cos(phase, dtype=np.float32)
        return (x * (1.0 - mix) + x * carrier * mix).astype(np.float32, copy=False)

    def _vibrato(self, x: np.ndarray, freq: float, depth: float, sr: int) -> np.ndarray:
        buf = np.concatenate([self._vib_buf, x]) if self._vib_buf.size else \
            np.asarray(x, dtype=np.float32).copy()
        base = self._vib_start
        n = x.size
        t = np.arange(self._vib_phase, self._vib_phase + n)
        self._vib_phase += n
        delay = depth * sr * (0.5 + 0.5 * np.sin(2.0 * math.pi * freq * t / sr))
        delay = delay + 4.0                                # keep read >= 4 samples back
        pos = (base + buf.size - n) + np.arange(n) - delay  # absolute read positions
        i0 = pos.astype(np.int64)
        i0 = np.clip(i0, base, base + buf.size - 2)
        frac = (pos - i0).astype(np.float32)
        out = buf[i0 - base] * (1.0 - frac) + buf[i0 + 1 - base] * frac

        keep_abs = max(0, base + buf.size - n - int(depth * sr * 4) - 8)
        if keep_abs > base:
            cut = min(keep_abs - base, buf.size)
            buf = buf[cut:]
            base += cut
        self._vib_buf, self._vib_start = buf, base
        return out.astype(np.float32, copy=False)

    def _chorus(self, x: np.ndarray, rate_hz: float, mix: float, sr: int) -> np.ndarray:
        """Two-voice chorus (quadrature LFOs) that masks granular pitch
        artifacts and adds a subtle doubling body. Max latency ~12 ms."""
        base_delay = 0.008 * sr
        depth = 0.004 * sr
        buf = np.concatenate([self._cho_buf, x]) if self._cho_buf.size else \
            np.asarray(x, dtype=np.float32).copy()
        base = self._cho_start
        n = x.size
        t = np.arange(self._cho_phase, self._cho_phase + n)
        self._cho_phase += n

        base_pos = (base + buf.size - n) + np.arange(n)
        out = x.copy()
        for phase_off in (0.0, math.pi):
            delay = base_delay + depth * (0.5 + 0.5 * np.sin(2.0 * math.pi * rate_hz * t / sr + phase_off))
            pos = base_pos - delay
            i0 = np.clip(pos.astype(np.int64), base, base + buf.size - 2)
            frac = (pos - i0).astype(np.float32)
            tap = buf[i0 - base] * (1.0 - frac) + buf[i0 + 1 - base] * frac
            out = out * (1.0 - mix * 0.5) + tap * (mix * 0.5)

        keep_abs = max(0, base + buf.size - n - int((base_delay + depth) * 2) - 8)
        if keep_abs > base:
            cut = min(keep_abs - base, buf.size)
            buf = buf[cut:]
            base += cut
        self._cho_buf, self._cho_start = buf, base
        return out.astype(np.float32, copy=False)

    def _highpass(self, x: np.ndarray, hz: float, zi_slot: int = 0) -> np.ndarray:
        b, a = _onepole_hp_bands(hz, self._sample_rate)
        if zi_slot == 0:
            y, zi = lfilter(b, a, x, zi=self._hp_zi[0])
            self._hp_zi[0] = zi
        elif zi_slot == 1:
            y, zi = lfilter(b, a, x, zi=self._hp_zi[1])
            self._hp_zi[1] = zi
        else:
            y, zi = lfilter(b, a, x, zi=self._out_hp_zi)
            self._out_hp_zi = zi
        return y

    def _lowpass(self, x: np.ndarray, hz: float) -> np.ndarray:
        coef = 1.0 - math.exp(-2.0 * math.pi * hz / self._sample_rate)
        coef = min(1.0, max(0.0005, coef))
        y, self._lp_zi = lfilter([coef], [1.0, -(1.0 - coef)], x, zi=self._lp_zi)
        return y

    def _biquad(self, x: np.ndarray, freq: float, gain_db: float, q: float, slot: int, kind: str) -> np.ndarray:
        if abs(gain_db) < 0.1:
            return x
        b, a = _biquad_band(freq, gain_db, q, self._sample_rate, kind)
        y, zi = lfilter(b, a, x, zi=self._fx_biquad_zi[slot])
        self._fx_biquad_zi[slot] = zi
        return y

    def _echo(self, x: np.ndarray, feedback: float, mix: float) -> np.ndarray:
        buf = self._echo_buf
        pos = self._echo_pos
        d = buf.size
        y = np.empty_like(x)
        for i in range(x.size):
            v = x[i]
            dly = buf[pos]
            y[i] = v + mix * dly
            buf[pos] = v + feedback * dly
            pos = pos + 1 if pos + 1 < d else 0
        self._echo_pos = pos
        return y

    def _reverb(self, x: np.ndarray, mix: float) -> np.ndarray:
        wet_acc = np.zeros_like(x)
        fb = 0.72 + 0.08 * mix
        for buf_ref in range(len(self._comb_bufs)):
            buf = self._comb_bufs[buf_ref]
            pos = self._comb_pos[buf_ref]
            d = buf.size
            acc = np.zeros_like(x)
            for i in range(x.size):
                yv = x[i] + fb * buf[pos]
                buf[pos] = yv
                pos = pos + 1 if pos + 1 < d else 0
                acc[i] = yv
            self._comb_pos[buf_ref] = pos
            wet_acc += acc
        wet_acc *= 0.22
        return (x * (1.0 - 0.5 * mix) + wet_acc * mix).astype(np.float32, copy=False)

    @staticmethod
    def _soft_limit(out: np.ndarray, knee_start: float = 0.8) -> np.ndarray:
        """Piecewise soft-knee limiter: linear below knee_start, tanh-compressed
        above, asymptotically reaching 1.0. Prevents digital clipping while
        leaving normal speech untouched."""
        absy = np.abs(out)
        over = absy > knee_start
        if over.any():
            a = absy[over]
            scaled = knee_start + (1.0 - knee_start) * np.tanh((a - knee_start) / (1.0 - knee_start))
            out[over] = out[over] * (scaled / a)
        return out
