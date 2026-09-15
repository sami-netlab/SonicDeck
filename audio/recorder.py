"""In-memory audio test recorder & comparison engine for SonicDeck.

Allows recording a 5–15s test clip of raw microphone audio, then playing back
either the raw audio ('Before') or the processed audio ('After') passing through
the active Smart Mic cleanup pipeline and Voice Effects in real-time.
"""

import threading
import wave
import copy
from typing import Optional, Callable
import numpy as np
import sounddevice as sd


class AudioRecorder:
    """Manages temporary microphone recording and A/B comparison playback."""

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.is_recording = False
        self.is_playing = False
        self.play_mode = "raw"  # "raw" or "processed"

        self._raw_buffer: list[np.ndarray] = []
        self._recorded_audio: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._play_stream = None
        self._play_pos = 0
        self._play_data: Optional[np.ndarray] = None
        self._on_playback_finished: Optional[Callable[[], None]] = None

    @property
    def has_recording(self) -> bool:
        with self._lock:
            return self._recorded_audio is not None and len(self._recorded_audio) > 0

    @property
    def duration_seconds(self) -> float:
        with self._lock:
            if self._recorded_audio is None:
                return 0.0
            return float(len(self._recorded_audio)) / float(self.sample_rate)

    def start_recording(self, max_duration_sec: float = 10.0) -> None:
        """Start capturing incoming audio chunks."""
        with self._lock:
            self.stop_playback()
            self._raw_buffer = []
            self._recorded_audio = None
            self.is_recording = True
            self._max_frames = int(max_duration_sec * self.sample_rate)
            self._recorded_frames = 0

    def feed_audio(self, block: np.ndarray) -> bool:
        """Feed a mono float32 block from the mic callback. Returns True if recording finished."""
        if not self.is_recording:
            return False

        with self._lock:
            data = np.asarray(block, dtype=np.float32).copy().reshape(-1)
            self._raw_buffer.append(data)
            self._recorded_frames += data.size
            if self._recorded_frames >= self._max_frames:
                self.is_recording = False
                self._recorded_audio = np.concatenate(self._raw_buffer)[:self._max_frames]
                self._raw_buffer = []
                return True
        return False

    def stop_recording(self) -> None:
        """Stop capturing and finalize recording buffer."""
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False
            if self._raw_buffer:
                self._recorded_audio = np.concatenate(self._raw_buffer)
                self._raw_buffer = []

    def play(
        self,
        mode: str = "raw",
        smart_mic_engine=None,
        voice_fx_engine=None,
        output_device: Optional[int] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> bool:
        """Play back recorded clip either raw or processed through active DSP engines."""
        self.stop_playback()

        with self._lock:
            if self._recorded_audio is None or len(self._recorded_audio) == 0:
                return False
            raw = self._recorded_audio.copy()

        # Process audio if requested
        if mode == "processed":
            play_data = self._process_offline(raw, smart_mic_engine, voice_fx_engine)
        else:
            play_data = raw

        self._play_data = play_data.astype(np.float32, copy=False)
        self._play_pos = 0
        self.play_mode = mode
        self._on_playback_finished = on_finished
        self.is_playing = True

        try:
            device = output_device if output_device is not None else sd.default.device[1]
            self._play_stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=960,
                device=device,
                channels=2,
                dtype="float32",
                callback=self._play_callback,
                finished_callback=self._on_stream_finished,
            )
            self._play_stream.start()
            return True
        except Exception:
            self.is_playing = False
            return False

    def _play_callback(self, outdata, frames, time_info, status):
        outdata[:] = 0
        if not self.is_playing or self._play_data is None:
            raise sd.CallbackStop()

        total = len(self._play_data)
        rem = total - self._play_pos
        if rem <= 0:
            raise sd.CallbackStop()

        n = min(frames, rem)
        chunk = self._play_data[self._play_pos:self._play_pos + n]
        self._play_pos += n

        outdata[:n, 0] = chunk
        outdata[:n, 1] = chunk

        if self._play_pos >= total:
            raise sd.CallbackStop()

    def _on_stream_finished(self):
        self.is_playing = False
        if self._on_playback_finished:
            try:
                self._on_playback_finished()
            except Exception:
                pass

    def stop_playback(self) -> None:
        """Stop current playback stream."""
        self.is_playing = False
        if self._play_stream is not None:
            try:
                self._play_stream.stop()
                self._play_stream.close()
            except Exception:
                pass
            self._play_stream = None

    def save_wav(self, file_path: str, mode: str = "raw", smart_mic_engine=None, voice_fx_engine=None) -> bool:
        """Export recorded audio to a standard WAV file."""
        with self._lock:
            if self._recorded_audio is None or len(self._recorded_audio) == 0:
                return False
            raw = self._recorded_audio.copy()

        if mode == "processed":
            data = self._process_offline(raw, smart_mic_engine, voice_fx_engine)
        else:
            data = raw

        try:
            # Convert float32 (-1.0..1.0) to int16
            int16_data = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
            with wave.open(file_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(int16_data.tobytes())
            return True
        except Exception:
            return False

    @staticmethod
    def _process_offline(raw: np.ndarray, smart_mic_engine=None, voice_fx_engine=None) -> np.ndarray:
        """Process a clip with fresh DSP state and compensate fixed latency.

        Cloning keeps A/B playback from inheriting live callback state or
        resetting the live monitor.  Padding and trimming removes suppressor
        and downward-pitch pre-roll silence without dropping the clip tail.
        """
        engines = []
        for engine in (smart_mic_engine, voice_fx_engine):
            if engine is not None and getattr(engine, "enabled", False):
                fresh = copy.deepcopy(engine)
                fresh.reset()
                engines.append(fresh)
        latency = sum(int(getattr(engine, "latency_samples", 0)) for engine in engines)
        source = np.concatenate([raw, np.zeros(latency, dtype=np.float32)]) if latency else raw.copy()
        processed = []
        for i in range(0, source.size, 960):
            chunk = source[i:i + 960]
            for engine in engines:
                chunk = engine.process(chunk)
            processed.append(chunk)
        data = np.concatenate(processed) if processed else raw.copy()
        return data[latency:latency + raw.size].astype(np.float32, copy=False)

    def shutdown(self) -> None:
        self.stop_recording()
        self.stop_playback()
