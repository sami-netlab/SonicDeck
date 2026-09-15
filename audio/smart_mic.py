"""Local, offline Smart Mic (Voice Cleanup) DSP engine for SonicDeck.

Professional real-time microphone cleanup without cloud APIs, internet or
external downloads. v1.0 audio rework: true streaming STFT noise suppression
(overlap-add across blocks with decision-directed noise tracking), a gate with
hold-time + hysteresis + residual floor so word endings survive, millisecond
compressor timing per profile, a smooth per-sample limiter, and a lightweight
de-esser. All IIR filtering and envelope followers are vectorized with
scipy.signal.lfilter for very low CPU use.

Processing chain:
    Raw Input
    -> Rumble/DC high-pass
    -> 50/60 Hz hum notches
    -> Streaming spectral noise suppression (fan/hiss/AC, adaptive profile)
    -> Voice gate (hysteresis + hold, no clipped word starts/ends)
    -> 3-band speech EQ
    -> lightweight de-esser
    -> soft-knee compressor (per-profile attack/release/makeup)
    -> smooth safety limiter (no pumping, no clipping)
"""

import math
from typing import Any, Dict

import numpy as np
from scipy.signal import lfilter


SMART_MIC_PROFILES = ["natural", "clean", "isolation", "studio"]

PROFILE_CONFIGS: Dict[str, Dict[str, Any]] = {
    # Natural: minimal processing, keep the voice untouched.
    "natural": {
        "hp_hz": 70.0,
        "suppression_strength": 0.25,
        "suppression_floor": 0.38,
        "gate_threshold_db": -52.0,
        "gate_release_ms": 220.0,
        "gate_hold_ms": 200.0,
        "gate_floor_db": -8.0,
        "hum_filter": True,
        "eq_low_gain_db": 0.5,
        "eq_mid_gain_db": 1.0,
        "eq_high_gain_db": 0.5,
        "deess_amount": 0.15,
        "comp_threshold_db": -20.0,
        "comp_ratio": 2.0,
        "comp_attack_ms": 15.0,
        "comp_release_ms": 140.0,
        "comp_makeup_db": 1.0,
    },
    # Clean: balanced everyday use.
    "clean": {
        "hp_hz": 75.0,
        "suppression_strength": 0.48,
        "suppression_floor": 0.20,
        "gate_threshold_db": -46.0,
        "gate_release_ms": 200.0,
        "gate_hold_ms": 180.0,
        "gate_floor_db": -12.0,
        "hum_filter": True,
        "eq_low_gain_db": 0.5,
        "eq_mid_gain_db": 2.5,
        "eq_high_gain_db": 1.5,
        "deess_amount": 0.25,
        "comp_threshold_db": -18.0,
        "comp_ratio": 3.0,
        "comp_attack_ms": 12.0,
        "comp_release_ms": 120.0,
        "comp_makeup_db": 1.5,
    },
    # Isolation: strongest safe cleanup for noisy rooms.
    "isolation": {
        "hp_hz": 80.0,
        "suppression_strength": 0.62,
        "suppression_floor": 0.18,
        "gate_threshold_db": -40.0,
        "gate_release_ms": 180.0,
        "gate_hold_ms": 180.0,
        "gate_floor_db": -18.0,
        "hum_filter": True,
        "eq_low_gain_db": -1.5,
        "eq_mid_gain_db": 3.5,
        "eq_high_gain_db": 1.0,
        "deess_amount": 0.2,
        "comp_threshold_db": -16.0,
        "comp_ratio": 4.5,
        "comp_attack_ms": 8.0,
        "comp_release_ms": 100.0,
        "comp_makeup_db": 2.0,
    },
    # Studio Broadcast: warm, controlled, compressed radio voice.
    "studio": {
        "hp_hz": 70.0,
        "suppression_strength": 0.42,
        "suppression_floor": 0.18,
        "gate_threshold_db": -48.0,
        "gate_release_ms": 240.0,
        "gate_hold_ms": 220.0,
        "gate_floor_db": -10.0,
        "hum_filter": True,
        "eq_low_gain_db": 2.5,
        "eq_mid_gain_db": 2.0,
        "eq_high_gain_db": 1.5,
        "deess_amount": 0.35,
        "comp_threshold_db": -17.0,
        "comp_ratio": 3.5,
        "comp_attack_ms": 18.0,
        "comp_release_ms": 160.0,
        "comp_makeup_db": 2.5,
    },
}


def _onepole_hp_bands(hz: float, sr: int) -> tuple[list[float], list[float]]:
    """Transfer function of a one-pole high-pass (y = x - lowpass(x))."""
    c = 1.0 - math.exp(-2.0 * math.pi * hz / sr)
    c = min(0.9995, max(0.0005, c))
    return [1.0 - c, -(1.0 - c)], [1.0, -(1.0 - c)]


class SmartMicEngine:
    """Real-time, block-based offline voice cleanup processor."""

    def __init__(self, sample_rate: int = 48000):
        self.enabled = False
        self.profile = "clean"
        self._sample_rate = 48000
        self.hum_filter_enabled = True
        self.gate_enabled = True
        self.eq_enabled = True
        self.compressor_enabled = True
        self.limiter_enabled = True

        self.reset(sample_rate)

    def set_sample_rate(self, sample_rate: int) -> None:
        if sample_rate and sample_rate != self._sample_rate:
            self.reset(sample_rate)

    def set_profile(self, profile: str) -> None:
        if profile not in PROFILE_CONFIGS:
            profile = "clean"
        self.profile = profile

    @property
    def latency_samples(self) -> int:
        """Fixed streaming delay introduced by the overlap-add suppressor."""
        if not self.enabled:
            return 0
        cfg = PROFILE_CONFIGS.get(self.profile, PROFILE_CONFIGS["clean"])
        return self._ns_latency if float(cfg.get("suppression_strength", 0.0)) > 0.05 else 0

    def reset(self, sample_rate: int | None = None) -> None:
        if sample_rate:
            self._sample_rate = int(sample_rate)
        sr = self._sample_rate

        # Filter state vectors (scipy lfilter direct-form II transposed).
        self._hp_b, self._hp_a = _onepole_hp_bands(75.0, sr)
        self._hp_zi = np.zeros(1)
        self._hp_zi2 = np.zeros(1)
        self._notch_zi = [np.zeros(2), np.zeros(2)]  # 50 Hz, 60 Hz
        self._eq_zi = [np.zeros(2), np.zeros(2), np.zeros(2)]
        self._ds_band_zi = np.zeros(1)

        # Streaming STFT noise suppression state.
        self._ns_frame = 512
        self._ns_hop = 256
        self._ns_window = np.hanning(self._ns_frame).astype(np.float32)
        # Output runs `latency` samples behind the input so every emitted block
        # is fully overlap-added (no incomplete-frame artifacts). The buffer is
        # pre-rolled with silence covering absolute time [-latency, 0).
        self._ns_latency = 2048
        self._ns_in = np.zeros(0, dtype=np.float32)      # pending input samples
        self._ns_in_start = 0                             # absolute index of _ns_in[0]
        self._ns_pos = 0                                  # absolute index of next frame
        self._ns_out = np.zeros(self._ns_latency, dtype=np.float32)
        self._ns_weight = np.zeros(self._ns_latency, dtype=np.float32)
        self._ns_out_start = -self._ns_latency            # absolute index of _ns_out[0]
        self._ns_noise_mag = None                         # per-bin noise magnitude
        self._ns_prev_gain = None
        self._ns_frame_count = 0

        # Gate state.
        self._gate_gain = 1.0
        self._gate_hold = 0

        # Compressor / limiter envelope followers are stateless lfilters fed by
        # their own persistent states.
        self._comp_env_zi = np.zeros(1)
        self._comp_slow_zi = np.zeros(1)
        self._lim_env_zi = np.zeros(1)
        self._lim_slow_zi = np.zeros(1)
        self._ds_env_zi = np.zeros(1)
        self._ds_slow_zi = np.zeros(1)

        # EQ coefficients depend on the sample rate only; cache them.
        self._eq_cache: Dict[str, Any] = {}

    def process(self, block: np.ndarray) -> np.ndarray:
        """Process one mono float32 block. Real-time safe, never raises."""
        if not self.enabled:
            return block

        try:
            x = np.asarray(block, dtype=np.float32).reshape(-1)
            if x.size == 0:
                return block

            cfg = PROFILE_CONFIGS.get(self.profile, PROFILE_CONFIGS["clean"])

            # 1. Rumble / DC high-pass.
            x = self._apply_highpass(x, float(cfg.get("hp_hz", 75.0)))

            # 2. 50/60 Hz hum notches.
            if self.hum_filter_enabled and cfg.get("hum_filter", True):
                x = self._apply_notch_filter(x, 50.0, 0)
                x = self._apply_notch_filter(x, 60.0, 1)

            # 3. Streaming spectral noise suppression.
            strength = float(cfg.get("suppression_strength", 0.70))
            if strength > 0.05:
                x = self._apply_noise_suppression(
                    x, strength, float(cfg.get("suppression_floor", max(0.08, 0.55 - 0.60 * strength))))

            # 4. Voice gate with hysteresis + hold.
            if self.gate_enabled:
                x = self._apply_gate(x, cfg)

            # 5. Speech EQ.
            if self.eq_enabled:
                x = self._apply_speech_eq(
                    x,
                    low_db=float(cfg.get("eq_low_gain_db", 1.0)),
                    mid_db=float(cfg.get("eq_mid_gain_db", 2.5)),
                    high_db=float(cfg.get("eq_high_gain_db", 2.0)),
                )

            # 6. Lightweight de-esser.
            deess = float(cfg.get("deess_amount", 0.0))
            if deess > 0.02:
                x = self._apply_deesser(x, deess)

            # 7. Soft-knee compressor.
            if self.compressor_enabled:
                x = self._apply_compressor(x, cfg)

            # 8. Smooth safety limiter.
            if self.limiter_enabled:
                x = self._apply_limiter(x, peak_limit=0.95)

            x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
            np.clip(x, -0.995, 0.995, out=x)
            return x.astype(np.float32, copy=False)
        except Exception:
            return block

    # ------------------------------------------------------------- DSP filters

    def _apply_highpass(self, x: np.ndarray, hz: float) -> np.ndarray:
        """Two cascaded one-pole high-passes (12 dB/oct) removing rumble/DC."""
        b, a = _onepole_hp_bands(hz, self._sample_rate)
        self._hp_b, self._hp_a = b, a
        y, self._hp_zi = lfilter(b, a, x, zi=self._hp_zi)
        y, self._hp_zi2 = lfilter(b, a, y, zi=self._hp_zi2)
        return y

    def _notch_coeffs(self, freq: float) -> tuple[list[float], list[float]]:
        """2nd-order IIR notch coefficients targeting powerline hum."""
        sr = self._sample_rate
        q = 12.0
        w0 = 2.0 * math.pi * freq / sr
        alpha = math.sin(w0) / (2.0 * q)
        cos_w0 = math.cos(w0)

        a0 = 1.0 + alpha
        b = [1.0 / a0, (-2.0 * cos_w0) / a0, 1.0 / a0]
        a = [1.0, (-2.0 * cos_w0) / a0, (1.0 - alpha) / a0]
        return b, a

    def _apply_notch_filter(self, x: np.ndarray, freq: float, slot: int) -> np.ndarray:
        b, a = self._notch_coeffs(freq)
        y, zi = lfilter(b, a, x, zi=self._notch_zi[slot])
        self._notch_zi[slot] = zi
        return y

    # ------------------------------------------------- noise suppression (STFT)

    def _apply_noise_suppression(self, x: np.ndarray, strength: float,
                                 gain_floor: float | None = None) -> np.ndarray:
        """Streaming spectral subtraction with decision-directed noise tracking.

        Frames of 512 samples at 50% overlap are windowed, suppressed with a
        temporally smoothed Wiener-style gain and overlap-added back. The
        output stream lags the input by `_ns_latency` samples so every emitted
        block contains only fully overlap-added audio, and any input block
        size is handled correctly.
        """
        n = x.size
        frame, hop = self._ns_frame, self._ns_hop
        win = self._ns_window

        if self._ns_in.size:
            self._ns_in = np.concatenate([self._ns_in, x])
        else:
            self._ns_in = np.asarray(x, dtype=np.float32).copy()
        in_total = self._ns_in_start + self._ns_in.size

        # Keep a generous residual floor.  Deep spectral holes are the main
        # source of metallic voice; the gate can lower steady room tone without
        # carving changing holes into speech harmonics.
        if gain_floor is None:
            gain_floor = max(0.08, 0.55 - 0.60 * strength)

        while self._ns_pos + frame <= in_total:
            offset = self._ns_pos - self._ns_in_start
            frame_in = self._ns_in[offset:offset + frame]
            spec = np.fft.rfft(frame_in * win)
            mag = np.abs(spec)
            if mag.size >= 3:
                mag_s = mag.copy()
                mag_s[1:-1] = (mag[:-2] + mag[1:-1] * 2.0 + mag[2:]) * 0.25
            else:
                mag_s = mag
            gain = self._suppression_gain(mag_s, mag, strength, gain_floor)
            clean = np.fft.irfft(spec * gain).astype(np.float32)
            o_off = self._ns_pos - self._ns_out_start
            if self._ns_out.size < o_off + frame:
                grow = o_off + frame - self._ns_out.size
                self._ns_out = np.concatenate(
                    [self._ns_out, np.zeros(grow, np.float32)])
                self._ns_weight = np.concatenate(
                    [self._ns_weight, np.zeros(grow, np.float32)])
            # Analysis and synthesis both use Hann, so normalize by the actual
            # streaming sum of squared windows.  A fixed multiplier creates a
            # 187.5 Hz amplitude ripple at this frame/hop size.
            self._ns_out[o_off:o_off + frame] += clean * win
            self._ns_weight[o_off:o_off + frame] += win * win
            self._ns_pos += hop

        # Emit exactly n fully-overlap-added samples (pre-roll guarantees the
        # front of the buffer is complete once the latency has filled).
        out = self._ns_out[:n].copy()
        weights = self._ns_weight[:n]
        valid = weights > 1e-6
        out[valid] /= weights[valid]
        out[~valid] = 0.0
        if out.size < n:  # only conceivable during the initial fill
            out = np.concatenate([out, np.zeros(n - out.size, np.float32)])
        self._ns_out = self._ns_out[n:]
        self._ns_weight = self._ns_weight[n:]
        self._ns_out_start += n

        # Trim consumed input history (frames never re-read the past).
        keep_from = self._ns_pos - self._ns_in_start
        if keep_from > 0:
            self._ns_in = self._ns_in[keep_from:]
            self._ns_in_start += keep_from
        return out

    def _suppression_gain(self, mag_s: np.ndarray, mag: np.ndarray,
                          strength: float, floor: float) -> np.ndarray:
        """Per-bin gain with adaptive noise estimate + temporal smoothing."""
        if self._ns_noise_mag is None:
            self._ns_noise_mag = np.maximum(mag_s.copy(), 1e-7)
        noise = self._ns_noise_mag

        frame_power = float(np.mean(mag ** 2))
        noise_power = float(np.mean(noise ** 2)) + 1e-12
        snr = frame_power / noise_power

        if self._ns_frame_count < 12 or snr < 3.0:
            # Symmetric tracking avoids a low-biased estimate that fails to
            # attenuate stationary fan noise. Speech-dominant frames are held.
            self._ns_noise_mag = 0.90 * noise + 0.10 * mag_s
        self._ns_frame_count += 1

        noise = self._ns_noise_mag
        clean_fraction = np.maximum(mag_s * mag_s - noise * noise, 0.0) / (mag_s * mag_s + 1e-12)
        raw_gain = floor + (1.0 - floor) * np.power(clean_fraction, 1.0 + strength)
        # Frequency smoothing prevents isolated bin-to-bin holes (musical noise).
        if raw_gain.size >= 3:
            smooth = raw_gain.copy()
            smooth[1:-1] = (raw_gain[:-2] + 2.0 * raw_gain[1:-1] + raw_gain[2:]) * 0.25
            raw_gain = smooth
        if self._ns_prev_gain is None or self._ns_prev_gain.shape != raw_gain.shape:
            # Start transparent and fade suppression in across several frames;
            # immediate full attenuation is audible when speech begins at t=0.
            self._ns_prev_gain = np.ones_like(raw_gain)
        # Suppression engages slowly and releases faster, preserving consonant
        # attacks while avoiding frame-to-frame flutter.
        alpha = np.where(raw_gain < self._ns_prev_gain, 0.08, 0.30)
        gain = self._ns_prev_gain + alpha * (raw_gain - self._ns_prev_gain)
        self._ns_prev_gain = gain
        return gain.astype(np.float32, copy=False)

    # ------------------------------------------------------------------- gate

    def _apply_gate(self, x: np.ndarray, cfg: Dict[str, Any]) -> np.ndarray:
        """Adaptive voice gate: hysteresis + hold-time + residual floor so word
        beginnings/endings are never clipped; silence drops to a soft floor
        instead of digital zero. The gain trajectory is a closed-form
        exponential toward the block target (no per-sample loop)."""
        sr = self._sample_rate
        open_lin = 10.0 ** (float(cfg.get("gate_threshold_db", -38.0)) / 20.0)
        close_lin = open_lin * 0.55
        floor_gain = 10.0 ** (float(cfg.get("gate_floor_db", -55.0)) / 20.0)
        hold_samples = int(sr * float(cfg.get("gate_hold_ms", 130.0)) / 1000.0)
        n = x.size

        rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
        if rms >= open_lin:
            self._gate_hold = hold_samples
            target = 1.0
            coeff = 1.0 - math.exp(-1.0 / (1.5 * sr * 0.001))          # preserve consonants
        elif self._gate_hold > 0:
            self._gate_hold -= n
            target = 1.0
            coeff = 1.0 - math.exp(-1.0 / (1.5 * sr * 0.001))
        elif rms <= close_lin:
            target = floor_gain
            coeff = 1.0 - math.exp(-1.0 / (max(1.0, float(cfg.get("gate_release_ms", 130.0))) * sr * 0.001))
        else:
            target = self._gate_gain  # inside hysteresis band: hold current
            coeff = 1.0 - math.exp(-1.0 / (1.5 * sr * 0.001))

        g0 = self._gate_gain
        if abs(target - g0) < 1e-6:
            return x
        decay = (1.0 - coeff) ** np.arange(1, n + 1)
        gain = target + (g0 - target) * decay
        self._gate_gain = float(gain[-1])
        return (x * gain).astype(np.float32, copy=False)

    # -------------------------------------------------------------------- EQ

    def _apply_speech_eq(self, x: np.ndarray, low_db: float, mid_db: float, high_db: float) -> np.ndarray:
        """3-Band Speech EQ: low warmth shelf, mid presence boost, high air shelf."""
        x = self._biquad_shelf(x, 160.0, low_db, shelf_type="low", band_idx=0)
        x = self._biquad_peaking(x, 2800.0, mid_db, q=1.2, band_idx=1)
        x = self._biquad_shelf(x, 8000.0, high_db, shelf_type="high", band_idx=2)
        return x

    def _biquad_shelf(self, x: np.ndarray, freq: float, gain_db: float, shelf_type: str, band_idx: int) -> np.ndarray:
        if abs(gain_db) < 0.1:
            return x
        sr = self._sample_rate
        a = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * math.pi * freq / sr
        cos_w = math.cos(w0)
        sin_w = math.sin(w0)
        s = 1.0
        alpha = sin_w / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / s - 1.0) + 2.0)
        two_sqrt_a_alpha = 2.0 * math.sqrt(a) * alpha

        if shelf_type == "low":
            b0 = a * ((a + 1.0) - (a - 1.0) * cos_w + two_sqrt_a_alpha)
            b1 = 2.0 * a * ((a - 1.0) - (a + 1.0) * cos_w)
            b2 = a * ((a + 1.0) - (a - 1.0) * cos_w - two_sqrt_a_alpha)
            a0 = (a + 1.0) + (a - 1.0) * cos_w + two_sqrt_a_alpha
            a1 = -2.0 * ((a - 1.0) + (a + 1.0) * cos_w)
            a2 = (a + 1.0) + (a - 1.0) * cos_w - two_sqrt_a_alpha
        else:
            b0 = a * ((a + 1.0) + (a - 1.0) * cos_w + two_sqrt_a_alpha)
            b1 = -2.0 * a * ((a - 1.0) + (a + 1.0) * cos_w)
            b2 = a * ((a + 1.0) - (a - 1.0) * cos_w - two_sqrt_a_alpha)
            a0 = (a + 1.0) - (a - 1.0) * cos_w + two_sqrt_a_alpha
            a1 = 2.0 * ((a - 1.0) - (a + 1.0) * cos_w)
            a2 = (a + 1.0) - (a - 1.0) * cos_w - two_sqrt_a_alpha

        return self._run_biquad(x, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0, band_idx)

    def _biquad_peaking(self, x: np.ndarray, freq: float, gain_db: float, q: float, band_idx: int) -> np.ndarray:
        if abs(gain_db) < 0.1:
            return x
        sr = self._sample_rate
        a = 10.0 ** (gain_db / 40.0)
        w0 = 2.0 * math.pi * freq / sr
        alpha = math.sin(w0) / (2.0 * q)
        cos_w = math.cos(w0)

        b0 = 1.0 + alpha * a
        b1 = -2.0 * cos_w
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cos_w
        a2 = 1.0 - alpha / a

        return self._run_biquad(x, b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0, band_idx)

    def _run_biquad(self, x: np.ndarray, b0: float, b1: float, b2: float, a1: float, a2: float, idx: int) -> np.ndarray:
        b = [b0, b1, b2]
        a = [1.0, a1, a2]
        y, zi = lfilter(b, a, x, zi=self._eq_zi[idx])
        self._eq_zi[idx] = zi
        return y

    # -------------------------------------------------------------- de-esser

    def _apply_deesser(self, x: np.ndarray, amount: float) -> np.ndarray:
        """Dynamic sibilance tamer: ducks only the >5.8 kHz band when it gets
        harsh, leaving the body of the voice untouched. Fully vectorized."""
        sr = self._sample_rate
        b, a = _onepole_hp_bands(5800.0, sr)
        s, self._ds_band_zi = lfilter(b, a, x, zi=self._ds_band_zi)
        level = np.abs(s)

        att = 1.0 - math.exp(-1.0 / (0.8 * sr * 0.001))
        rel = 1.0 - math.exp(-1.0 / (35.0 * sr * 0.001))
        env_fast, self._ds_env_zi = lfilter([att], [1.0, -(1.0 - att)], level, zi=self._ds_env_zi)
        env_slow, self._ds_slow_zi = lfilter([rel], [1.0, -(1.0 - rel)], level, zi=self._ds_slow_zi)
        env = np.maximum(env_fast, env_slow)

        threshold = 0.05
        over = np.clip((env - threshold) / threshold, 0.0, 1.0)
        reduction = amount * over * 0.85
        y = x - s * reduction
        return y

    # ------------------------------------------------------------ compressor

    def _apply_compressor(self, x: np.ndarray, cfg: Dict[str, Any]) -> np.ndarray:
        """Feedforward compressor with soft knee and profile-based timing,
        built from a fast/slow max envelope follower (fast attack, slow
        release, no pumping loops)."""
        sr = self._sample_rate
        thresh_db = float(cfg.get("comp_threshold_db", -18.0))
        ratio = max(1.0, float(cfg.get("comp_ratio", 3.0)))
        makeup = 10.0 ** (float(cfg.get("comp_makeup_db", 2.5)) / 20.0)
        att = 1.0 - math.exp(-1.0 / (max(0.5, float(cfg.get("comp_attack_ms", 12.0))) * sr * 0.001))
        rel = 1.0 - math.exp(-1.0 / (max(5.0, float(cfg.get("comp_release_ms", 120.0))) * sr * 0.001))

        level = np.abs(x)
        env_fast, self._comp_env_zi = lfilter([att], [1.0, -(1.0 - att)], level, zi=self._comp_env_zi)
        env_slow, self._comp_slow_zi = lfilter([rel], [1.0, -(1.0 - rel)], level, zi=self._comp_slow_zi)
        env = np.maximum(env_fast, env_slow)

        thresh_lin = 10.0 ** (thresh_db / 20.0)
        knee = 6.0
        env_db = 20.0 * np.log10(np.maximum(env, 1e-6))
        over = env_db - thresh_db
        full = over >= knee * 0.5
        none = over <= -knee * 0.5
        mid = ~(full | none)
        reduction_db = np.zeros_like(over)
        reduction_db[full] = over[full] * (1.0 - 1.0 / ratio)
        t = (over[mid] + knee * 0.5) / knee
        reduction_db[mid] = t * t * (knee * 0.5 + over[mid] * (1.0 - 1.0 / ratio)) * 0.5
        gain = 10.0 ** (-reduction_db / 20.0) * makeup
        return (x * gain).astype(np.float32, copy=False)

    # --------------------------------------------------------------- limiter

    def _apply_limiter(self, x: np.ndarray, peak_limit: float = 0.95) -> np.ndarray:
        """Smooth brickwall: fast attack on peaks, musical release, so no
        block-edge discontinuities and no clipping distortion."""
        sr = self._sample_rate
        att = 1.0 - math.exp(-1.0 / (0.2 * sr * 0.001))
        rel = 1.0 - math.exp(-1.0 / (80.0 * sr * 0.001))
        level = np.abs(x)
        env_fast, self._lim_env_zi = lfilter([att], [1.0, -(1.0 - att)], level, zi=self._lim_env_zi)
        env_slow, self._lim_slow_zi = lfilter([rel], [1.0, -(1.0 - rel)], level, zi=self._lim_slow_zi)
        env = np.maximum(env_fast, env_slow)
        gain = np.minimum(1.0, peak_limit / np.maximum(env, 1e-9))
        y = x * gain
        np.clip(y, -0.995, 0.995, out=y)
        return y
