"""Microphone capture / monitoring engine for SonicDeck.

sounddevice (PortAudio/WASAPI) based, no Qt imports — usable from tests.
The engine captures the selected input device, applies gain + optional voice
effects, and optionally monitors the processed signal on a chosen output
device (speakers or a virtual-audio-cable input for system-wide use).

Every entry point is failure-tolerant: hardware problems are recorded in
`last_error` instead of raising, so the GUI can never crash from audio I/O.
"""

import threading

import numpy as np
import sounddevice as sd

from sonicdeck.audio.smart_mic import SmartMicEngine
from sonicdeck.audio.voice_effects import VoiceEffectEngine
from sonicdeck.audio.recorder import AudioRecorder

# Name fragments of common virtual-audio-cable outputs (used as a mic source
# by other apps to receive the transformed voice system-wide).
_VIRTUAL_MARKERS = (
    "cable input", "vb-audio", "voicemeeter", "virtual audio",
    "line 1 (virtual", "blackhole", "voice bridge",
)

_BLOCKSIZE = 960        # ~20 ms @ 48 kHz
_SAMPLE_RATES = (48000, 44100)


class MicrophoneEngine:
    """Owns the capture stream and all mic parameters."""

    def __init__(
        self,
        effect_engine: VoiceEffectEngine | None = None,
        smart_mic_engine: SmartMicEngine | None = None,
    ):
        self.effect_engine = effect_engine or VoiceEffectEngine()
        self.smart_mic_engine = smart_mic_engine or SmartMicEngine()
        self.recorder = AudioRecorder(sample_rate=48000)
        self.volume = 1.0        # monitor / processed output volume, 0..1
        self.gain = 1.0          # input boost applied before effects, 1..4
        self.muted = False
        self.monitoring = False  # hear processed voice on the monitor device
        self.input_device: int | None = None   # None = system default
        self.monitor_device: int | None = None  # None = system default output
        self.last_error = ""

        self._stream = None
        self._lock = threading.Lock()
        self._level = 0.0        # smoothed input peak, 0..1
        self._is_clipping = False
        self._out_channels = 2

    # ------------------------------------------------------------- devices

    @staticmethod
    def list_input_devices() -> list[dict]:
        devices = []
        try:
            default_in = sd.default.device[0]
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    hostapi = sd.query_hostapis(dev["hostapi"])["name"]
                    devices.append({
                        "index": idx,
                        "name": dev["name"],
                        "hostapi": hostapi,
                        "channels": dev["max_input_channels"],
                        "default": idx == default_in,
                    })
        except Exception:
            pass
        return devices

    @staticmethod
    def list_output_devices() -> list[dict]:
        devices = []
        try:
            default_out = sd.default.device[1]
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_output_channels", 0) > 0:
                    hostapi = sd.query_hostapis(dev["hostapi"])["name"]
                    devices.append({
                        "index": idx,
                        "name": dev["name"],
                        "hostapi": hostapi,
                        "default": idx == default_out,
                    })
        except Exception:
            pass
        return devices

    @staticmethod
    def find_virtual_outputs() -> list[dict]:
        """Output devices that typically act as virtual microphone inputs."""
        found = []
        lowered = None
        for dev in MicrophoneEngine.list_output_devices():
            lowered = dev["name"].lower()
            if any(marker in lowered for marker in _VIRTUAL_MARKERS):
                found.append(dev)
        return found

    def set_input_device(self, index: int | None) -> bool:
        self.input_device = index
        if self.is_running:
            self.restart()
        return True

    def set_monitor_device(self, index: int | None) -> bool:
        self.monitor_device = index
        if self.is_running and self.monitoring:
            self.restart()
        return True

    # ----------------------------------------------------------- transport

    @property
    def is_running(self) -> bool:
        return self._stream is not None and self._stream.active

    def start(self) -> tuple[bool, str]:
        """Open the capture stream. Returns (ok, message)."""
        with self._lock:
            self._close_stream()
            try:
                devices = sd.query_devices()
            except Exception as error:
                self.last_error = str(error)
                return False, str(error)

            in_idx = self.input_device
            if in_idx is None:
                in_idx = sd.default.device[0]
            if in_idx is None or in_idx < 0 or in_idx >= len(devices):
                self.last_error = "no_input"
                return False, "no_input"
            in_max_ch = int(devices[in_idx].get("max_input_channels", 0) or 0)
            if in_max_ch < 1:
                self.last_error = "no_input"
                return False, "no_input"

            monitor_idx = self.monitor_device
            if monitor_idx is None:
                monitor_idx = sd.default.device[1]
            out_max_ch = 0
            if 0 <= monitor_idx < len(devices):
                out_max_ch = int(devices[monitor_idx].get("max_output_channels", 0) or 0)

            self.effect_engine.reset()
            in_ch = 1 if in_max_ch >= 1 else in_max_ch
            self._out_channels = 2 if out_max_ch >= 2 else max(1, out_max_ch)

            attempts = []
            for sr in _SAMPLE_RATES:
                if self.monitoring and out_max_ch >= 1:
                    attempts.append({
                        "kind": "duplex", "samplerate": sr,
                        "device": (in_idx, monitor_idx), "channels": (in_ch, self._out_channels),
                    })
                attempts.append({
                    "kind": "input", "samplerate": sr,
                    "device": in_idx, "channels": in_ch,
                })

            last_error = ""
            for attempt in attempts:
                try:
                    if attempt["kind"] == "duplex":
                        self._stream = sd.Stream(
                            samplerate=attempt["samplerate"],
                            blocksize=_BLOCKSIZE,
                            device=attempt["device"],
                            channels=attempt["channels"],
                            dtype="float32",
                            callback=self._duplex_callback,
                        )
                    else:
                        self._stream = sd.InputStream(
                            samplerate=attempt["samplerate"],
                            blocksize=_BLOCKSIZE,
                            device=attempt["device"],
                            channels=attempt["channels"],
                            dtype="float32",
                            callback=self._input_callback,
                        )
                    # Start every hardware stream with clean DSP history.  A
                    # same-rate restart previously retained stale OLA/delay
                    # buffers, leaking a short fragment from the prior stream.
                    self.effect_engine.reset(attempt["samplerate"])
                    self.smart_mic_engine.reset(attempt["samplerate"])
                    self.recorder.sample_rate = attempt["samplerate"]
                    self._stream.start()
                    self.last_error = ""
                    return True, "ok"
                except Exception as error:
                    last_error = str(error)
                    self._close_stream()
            self.last_error = last_error or "open_failed"
            return False, self.last_error

    def stop(self) -> None:
        with self._lock:
            self._close_stream()

    def restart(self) -> tuple[bool, str]:
        if self.is_running:
            self.stop()
        return self.start()

    def shutdown(self) -> None:
        self.stop()
        try:
            self.recorder.shutdown()
        except Exception:
            pass

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # -------------------------------------------------------------- meter

    @property
    def level(self) -> float:
        """Smoothed input peak 0..1 (updated by the audio callback)."""
        return float(self._level)

    @property
    def is_clipping(self) -> bool:
        """Indicates if input audio recently hit clipping limits."""
        return bool(self._is_clipping)

    # ----------------------------------------------------------- callbacks

    def _input_callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        try:
            mono = indata[:, 0] if indata.shape[1] > 1 else indata.reshape(-1)
            peak = float(np.max(np.abs(mono))) if mono.size else 0.0
            self._level = max(peak, self._level * 0.86)
            self._is_clipping = peak >= 0.96

            if self.recorder.is_recording:
                self.recorder.feed_audio(mono)
        except Exception:
            pass

    def _duplex_callback(self, indata, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        try:
            outdata[:] = 0
            mono = indata[:, 0] if indata.shape[1] > 1 else indata.reshape(-1)
            peak = float(np.max(np.abs(mono))) if mono.size else 0.0
            self._level = max(peak, self._level * 0.86)
            self._is_clipping = peak >= 0.96

            if self.recorder.is_recording:
                self.recorder.feed_audio(mono)

            x = np.clip(mono * self.gain, -1.0, 1.0).astype(np.float32, copy=False)
            if self.smart_mic_engine.enabled:
                x = self.smart_mic_engine.process(x)
            if self.effect_engine.enabled:
                x = self.effect_engine.process(x)
            if self.muted or not self.monitoring:
                return
            y = np.clip(x * self.volume, -1.0, 1.0)
            channels = outdata.shape[1]
            outdata[:, 0] = y
            for ch in range(1, channels):
                outdata[:, ch] = y
        except Exception as error:
            try:
                outdata[:] = 0
            except Exception:
                pass
            self.last_error = str(error)
