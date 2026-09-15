"""Microphone studio, Smart Mic (Voice Cleanup), and voice-effects panels for SonicDeck.

Self-contained cards added below the session mixer area:
- MicrophonePanel: input device, volume, gain, mute, live level meter with clipping
  indication, monitoring, and an integrated Mic Test / Comparison recording area.
- SmartMicPanel: local offline AI/DSP voice cleanup (Noise Gate, 50/60Hz Hum filter,
  Speech EQ, Vocal Compressor, Limiter) with profiles: Natural, Clean, Isolation, Studio.
- VoiceEffectsPanel: preset picker + intensity / wet-dry controls bound to the
  same VoiceEffectEngine instance used by the capture stream.
"""

from typing import Optional
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from sonicdeck.audio.microphone import MicrophoneEngine
from sonicdeck.audio.smart_mic import SmartMicEngine, SMART_MIC_PROFILES
from sonicdeck.audio.voice_effects import PRESET_IDS
from sonicdeck.i18n import t

_DEFAULT_INTENSITY = 80
_DEFAULT_WET = 90


class MicSlider(QSlider):
    """Slider matching SonicDeck's existing style, with per-use accent."""

    def __init__(self, accent="#3ca6e8", large=False):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 100)
        self.setFixedHeight(28 if large else 24)
        self.setStyleSheet(f"""
            QSlider::groove:horizontal {{ background: #131a23; height: 6px; border-radius: 3px; border: 1px solid #2b3b4d; }}
            QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 3px; }}
            QSlider::handle:horizontal {{ background: #f4f7fb; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }}
            QSlider::handle:horizontal:hover {{ background: #72c7f2; }}
        """)


class LevelMeter(QWidget):
    """Compact horizontal input level meter with peak-hold tick and clipping indicator."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(18)
        self.setMinimumWidth(140)
        self._level = 0.0
        self._peak = 0.0
        self._is_clipping = False

    def set_level(self, value: float, is_clipping: bool = False):
        value = max(0.0, min(1.0, float(value)))
        self._peak = max(value, self._peak * 0.94)
        changed = abs(value - self._level) > 0.005 or (value == 0.0 and self._level != 0.0) or (is_clipping != self._is_clipping)
        self._level = value
        self._is_clipping = is_clipping
        if changed:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)

        # Background track
        painter.setPen(QPen(QColor("#2b3b4d"), 1))
        painter.setBrush(QColor("#131a23"))
        painter.drawRoundedRect(rect, 5, 5)

        # Level fill
        w = int((rect.width() - 2) * self._level)
        if w > 0:
            fill = rect.adjusted(1, 1, 1 - rect.width() + 1 + w, -1)
            color = QColor("#e84a5f") if (self._level > 0.92 or self._is_clipping) else QColor("#3ca6e8")
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(fill, 4, 4)

        # Peak hold tick
        peak_x = rect.left() + 1 + int((rect.width() - 2) * self._peak)
        if self._peak > 0.02:
            tick_color = QColor("#ff4757") if self._is_clipping else QColor("#eaf3fb")
            painter.setPen(QPen(tick_color, 2))
            painter.drawLine(peak_x, rect.top() + 2, peak_x, rect.bottom() - 1)

        # Clipping badge
        if self._is_clipping:
            painter.setPen(QPen(QColor("#ff4757"), 1))
            painter.setBrush(QColor("#ff4757"))
            clip_font = QFont("Segoe UI", 7, QFont.Bold)
            painter.setFont(clip_font)
            painter.drawText(rect.adjusted(0, 0, -4, 0), Qt.AlignRight | Qt.AlignVCenter, "CLIP")

        painter.end()


def _value_badge(text_value: str) -> QLabel:
    label = QLabel(text_value)
    label.setObjectName("valueBadge")
    label.setAlignment(Qt.AlignCenter)
    label.setMinimumWidth(58)
    label.setStyleSheet("background: transparent; color: #5ebde8; font-weight: 700;")
    return label


class MicrophonePanel(QFrame):
    """Microphone controls card shown under the session mixer area."""

    def __init__(self):
        super().__init__()
        self.setObjectName("micCard")
        self.engine = MicrophoneEngine()
        self.fx_panel: Optional["VoiceEffectsPanel"] = None
        self.smart_mic_panel: Optional["SmartMicPanel"] = None
        self._input_indices: list[int] = []
        self._monitor_indices: list[Optional[int]] = []
        self._row_labels: dict[str, QLabel] = {}
        self._test_timer = QTimer(self)
        self._test_timer.setInterval(100)
        self._test_timer.timeout.connect(self._on_test_poll)
        self._init_ui()
        self.refresh_devices()
        self._meter_timer = QTimer(self)
        self._meter_timer.setInterval(50)
        self._meter_timer.timeout.connect(self._update_meter)
        self._meter_timer.start()

    def _row_label(self, key: str) -> QLabel:
        label = QLabel(t(key))
        label.setObjectName("rowLabel")
        self._row_labels[key] = label
        return label

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(t("mic_title"))
        title.setObjectName("sectionTitle")
        self._title_label = title
        header.addWidget(title)
        header.addStretch()
        self._status_pill = QLabel(t("mic_status_idle"))
        self._status_pill.setObjectName("statusPill")
        header.addWidget(self._status_pill)
        layout.addLayout(header)

        # Input device row
        device_row = QHBoxLayout()
        device_row.setSpacing(10)
        device_row.addWidget(self._row_label("mic_device"))
        self._device_combo = QComboBox()
        self._device_combo.setObjectName("deckCombo")
        self._device_combo.setMinimumWidth(320)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        device_row.addWidget(self._device_combo, 1)
        refresh_btn = QPushButton(t("mic_refresh"))
        refresh_btn.setObjectName("smallButton")
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_devices)
        device_row.addWidget(refresh_btn)
        layout.addLayout(device_row)

        # Volume / gain / mute row
        controls_row = QHBoxLayout()
        controls_row.setSpacing(10)
        controls_row.addWidget(self._row_label("mic_volume"))
        self._volume_slider = MicSlider()
        self._volume_slider.setValue(100)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        controls_row.addWidget(self._volume_slider, 2)
        self._volume_badge = _value_badge("100%")
        controls_row.addWidget(self._volume_badge)

        controls_row.addSpacing(8)
        controls_row.addWidget(self._row_label("mic_gain"))
        self._gain_slider = MicSlider(accent="#8fd3f0")
        self._gain_slider.setValue(0)
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        controls_row.addWidget(self._gain_slider, 2)
        self._gain_badge = _value_badge("+0 dB")
        controls_row.addWidget(self._gain_badge)
        layout.addLayout(controls_row)

        # Meter / transport row
        meter_row = QHBoxLayout()
        meter_row.setSpacing(10)
        self._activate_btn = QPushButton(t("mic_activate"))
        self._activate_btn.setObjectName("muteButton")
        self._activate_btn.setFixedWidth(140)
        self._activate_btn.setCursor(Qt.PointingHandCursor)
        self._activate_btn.clicked.connect(self._on_activate_clicked)
        meter_row.addWidget(self._activate_btn)

        self._mute_btn = QPushButton(t("mute"))
        self._mute_btn.setObjectName("muteButton")
        self._mute_btn.setProperty("muted", False)
        self._mute_btn.setFixedWidth(110)
        self._mute_btn.setCursor(Qt.PointingHandCursor)
        self._mute_btn.clicked.connect(self._on_mute_clicked)
        meter_row.addWidget(self._mute_btn)

        self._meter = LevelMeter()
        meter_row.addWidget(self._meter, 1)

        self._monitor_check = QCheckBox(t("mic_monitor"))
        self._monitor_check.setObjectName("deckCheck")
        self._monitor_check.toggled.connect(self._on_monitor_toggled)
        meter_row.addWidget(self._monitor_check)
        layout.addLayout(meter_row)

        # Monitor output row
        monitor_row = QHBoxLayout()
        monitor_row.setSpacing(10)
        monitor_row.addWidget(self._row_label("mic_monitor_output"))
        self._monitor_combo = QComboBox()
        self._monitor_combo.setObjectName("deckCombo")
        self._monitor_combo.setMinimumWidth(320)
        self._monitor_combo.currentIndexChanged.connect(self._on_monitor_device_changed)
        monitor_row.addWidget(self._monitor_combo, 1)
        self._status_label = QLabel(t("mic_status_hint"))
        self._status_label.setObjectName("mutedText")
        monitor_row.addWidget(self._status_label, 1)
        layout.addLayout(monitor_row)

        # --- Mic Test & Comparison Section ---
        test_frame = QFrame()
        test_frame.setObjectName("testSubCard")
        test_frame.setStyleSheet("""
            QFrame#testSubCard {
                background: #141d27;
                border: 1px solid #2d3e52;
                border-radius: 8px;
            }
        """)
        test_layout = QVBoxLayout(test_frame)
        test_layout.setContentsMargins(14, 12, 14, 12)
        test_layout.setSpacing(10)

        test_header = QHBoxLayout()
        test_title = QLabel(t("test_title"))
        test_title.setStyleSheet("font-weight: 700; color: #a5d8f6; font-size: 10pt;")
        self._test_title_label = test_title
        test_header.addWidget(test_title)
        test_header.addStretch()
        self._test_status_label = QLabel(t("test_status_ready"))
        self._test_status_label.setObjectName("mutedText")
        test_header.addWidget(self._test_status_label)
        test_layout.addLayout(test_header)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._test_record_btn = QPushButton(t("test_record"))
        self._test_record_btn.setObjectName("smallButton")
        self._test_record_btn.clicked.connect(self._on_test_record_clicked)
        btn_row.addWidget(self._test_record_btn)

        self._test_stop_btn = QPushButton(t("test_stop"))
        self._test_stop_btn.setObjectName("smallButton")
        self._test_stop_btn.setEnabled(False)
        self._test_stop_btn.clicked.connect(self._on_test_stop_clicked)
        btn_row.addWidget(self._test_stop_btn)

        self._test_raw_btn = QPushButton(t("test_play_raw"))
        self._test_raw_btn.setObjectName("smallButton")
        self._test_raw_btn.setEnabled(False)
        self._test_raw_btn.clicked.connect(self._on_test_play_raw_clicked)
        btn_row.addWidget(self._test_raw_btn)

        self._test_proc_btn = QPushButton(t("test_play_proc"))
        self._test_proc_btn.setObjectName("smallButton")
        self._test_proc_btn.setEnabled(False)
        self._test_proc_btn.setStyleSheet("QPushButton { border-color: #3ca6e8; color: #72c7f2; }")
        self._test_proc_btn.clicked.connect(self._on_test_play_proc_clicked)
        btn_row.addWidget(self._test_proc_btn)

        btn_row.addStretch()

        self._test_save_btn = QPushButton(t("test_save"))
        self._test_save_btn.setObjectName("smallButton")
        self._test_save_btn.setEnabled(False)
        self._test_save_btn.clicked.connect(self._on_test_save_clicked)
        btn_row.addWidget(self._test_save_btn)
        test_layout.addLayout(btn_row)

        layout.addWidget(test_frame)

    # ------------------------------------------------------------ refresh

    def refresh_devices(self):
        try:
            inputs = self.engine.list_input_devices()
        except Exception:
            inputs = []
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._input_indices = []
        for dev in inputs:
            suffix = f"  ·  {dev['hostapi']}" if dev["hostapi"] else ""
            self._device_combo.addItem(f"{'★ ' if dev['default'] else ''}{dev['name']}{suffix}")
            self._input_indices.append(dev["index"])
        if not self._device_combo.count():
            self._device_combo.addItem(t("mic_no_input"))
        self._device_combo.blockSignals(False)
        default_pos = next((i for i, dev in enumerate(inputs) if dev["default"]), 0)
        if inputs:
            self._device_combo.setCurrentIndex(default_pos)

        try:
            outputs = self.engine.list_output_devices()
            virtual = self.engine.find_virtual_outputs()
        except Exception:
            outputs, virtual = [], []
        self._monitor_combo.blockSignals(True)
        self._monitor_combo.clear()
        self._monitor_indices = []
        self._monitor_combo.addItem(t("mic_monitor_default"))
        self._monitor_indices.append(None)
        for dev in outputs:
            marker = "  ⟵ " + t("fx_virtual_tag") if dev in virtual else ""
            self._monitor_combo.addItem(f"{dev['name']}{marker}")
            self._monitor_indices.append(dev["index"])
        self._monitor_combo.blockSignals(False)
        self._monitor_combo.setCurrentIndex(0)
        self._update_fx_hint(bool(virtual))

    def _update_fx_hint(self, has_virtual: bool):
        if self.fx_panel is not None:
            self.fx_panel.set_virtual_hint(has_virtual)

    # ------------------------------------------------------------- events

    def _on_device_changed(self, index: int):
        if 0 <= index < len(self._input_indices):
            self.engine.set_input_device(self._input_indices[index])
            self._refresh_status()

    def _on_monitor_device_changed(self, index: int):
        if 0 <= index < len(self._monitor_indices):
            self.engine.set_monitor_device(self._monitor_indices[index])

    def _on_volume_changed(self, value: int):
        self.engine.volume = value / 100.0
        self._volume_badge.setText(f"{value}%")

    def _on_gain_changed(self, value: int):
        gain = 10 ** (value / 20.0)
        self.engine.gain = gain
        self._gain_badge.setText(f"+{value} dB")

    def _on_monitor_toggled(self, checked: bool):
        self.engine.monitoring = bool(checked)
        if self.engine.is_running:
            ok, message = self.engine.restart()
            self._refresh_status(ok, message)

    def _on_activate_clicked(self):
        if self.engine.is_running:
            self.engine.stop()
            self._refresh_status()
        else:
            ok, message = self.engine.start()
            self._refresh_status(ok, message)

    def _on_mute_clicked(self):
        self.engine.muted = not self.engine.muted
        self._refresh_status()

    # ----------------------------------------------------- Mic Test logic

    def _on_test_record_clicked(self):
        # Auto-activate mic if not already running
        if not self.engine.is_running:
            ok, _ = self.engine.start()
            if not ok:
                self._test_status_label.setText(t("mic_no_input"))
                return
            self._refresh_status()

        self.engine.recorder.start_recording(max_duration_sec=5.0)
        self._test_record_btn.setEnabled(False)
        self._test_stop_btn.setEnabled(True)
        self._test_raw_btn.setEnabled(False)
        self._test_proc_btn.setEnabled(False)
        self._test_save_btn.setEnabled(False)
        self._test_status_label.setText(t("test_status_recording"))
        self._test_timer.start()

    def _on_test_stop_clicked(self):
        self._test_timer.stop()
        if self.engine.recorder.is_recording:
            self.engine.recorder.stop_recording()
        if self.engine.recorder.is_playing:
            self.engine.recorder.stop_playback()
        self._update_test_ui_state()

    def _on_test_poll(self):
        if not self.engine.recorder.is_recording:
            self._test_timer.stop()
            self._update_test_ui_state()

    def _update_test_ui_state(self):
        has_rec = self.engine.recorder.has_recording
        is_rec = self.engine.recorder.is_recording
        is_play = self.engine.recorder.is_playing

        self._test_record_btn.setEnabled(not is_rec and not is_play)
        self._test_stop_btn.setEnabled(is_rec or is_play)
        self._test_raw_btn.setEnabled(has_rec and not is_rec and not is_play)
        self._test_proc_btn.setEnabled(has_rec and not is_rec and not is_play)
        self._test_save_btn.setEnabled(has_rec and not is_rec and not is_play)

        if has_rec and not is_rec and not is_play:
            self._test_status_label.setText(t("test_status_recorded"))

    def _on_test_play_raw_clicked(self):
        self._test_status_label.setText(t("test_status_playing_raw"))
        self._test_stop_btn.setEnabled(True)
        self._test_raw_btn.setEnabled(False)
        self._test_proc_btn.setEnabled(False)
        self.engine.recorder.play(
            mode="raw",
            output_device=self.engine.monitor_device,
            on_finished=self._on_playback_done,
        )

    def _on_test_play_proc_clicked(self):
        self._test_status_label.setText(t("test_status_playing_proc"))
        self._test_stop_btn.setEnabled(True)
        self._test_raw_btn.setEnabled(False)
        self._test_proc_btn.setEnabled(False)
        self.engine.recorder.play(
            mode="processed",
            smart_mic_engine=self.engine.smart_mic_engine,
            voice_fx_engine=self.engine.effect_engine,
            output_device=self.engine.monitor_device,
            on_finished=self._on_playback_done,
        )

    def _on_playback_done(self):
        QTimer.singleShot(0, self._update_test_ui_state)

    def _on_test_save_clicked(self):
        if not self.engine.recorder.has_recording:
            return
        path, _ = QFileDialog.getSaveFileName(self, t("test_save"), "sonicdeck_test.wav", "WAV Audio (*.wav)")
        if path:
            ok = self.engine.recorder.save_wav(
                path,
                mode="processed",
                smart_mic_engine=self.engine.smart_mic_engine,
                voice_fx_engine=self.engine.effect_engine,
            )
            if ok:
                self._test_status_label.setText(t("test_saved_success").format(path=path))

    # ------------------------------------------------------------- status

    def _update_meter(self):
        level = self.engine.level if self.engine.is_running else 0.0
        clipping = self.engine.is_clipping if self.engine.is_running else False
        self._meter.set_level(level, is_clipping=clipping)

    def _refresh_status(self, ok: Optional[bool] = None, message: str = ""):
        if ok is False:
            key = "mic_status_error"
            self._status_label.setText(f"{t('error_prefix')}{message}")
        elif self.engine.is_running:
            key = "mic_status_muted" if self.engine.muted else "mic_status_live"
            self._status_label.setText(t("mic_status_listening"))
        else:
            key = "mic_status_idle"
            self._status_label.setText(t("mic_status_hint"))
        self._status_pill.setText(t(key))
        self._activate_btn.setText(t("mic_deactivate") if self.engine.is_running else t("mic_activate"))
        self._mute_btn.setText(t("unmute") if self.engine.muted else t("mute"))
        self._mute_btn.setProperty("muted", self.engine.muted)
        self._mute_btn.style().unpolish(self._mute_btn)
        self._mute_btn.style().polish(self._mute_btn)

    def shutdown(self):
        try:
            self._meter_timer.stop()
            self._test_timer.stop()
            self.engine.shutdown()
        except Exception:
            pass

    def retranslate(self):
        if self._title_label is not None:
            self._title_label.setText(t("mic_title"))
        if hasattr(self, "_test_title_label"):
            self._test_title_label.setText(t("test_title"))
        for key, label in self._row_labels.items():
            label.setText(t(key))
        self._monitor_check.setText(t("mic_monitor"))
        self._test_record_btn.setText(t("test_record"))
        self._test_stop_btn.setText(t("test_stop"))
        self._test_raw_btn.setText(t("test_play_raw"))
        self._test_proc_btn.setText(t("test_play_proc"))
        self._test_save_btn.setText(t("test_save"))
        self.refresh_devices()
        self._refresh_status()
        self._update_test_ui_state()


class SmartMicPanel(QFrame):
    """Voice cleanup (Smart Mic) controls card bound to the microphone engine's DSP."""

    def __init__(self, smart_mic_engine: SmartMicEngine):
        super().__init__()
        self.setObjectName("micCard")
        self.engine = smart_mic_engine
        self._row_labels: dict[str, QLabel] = {}
        self._title_label: Optional[QLabel] = None
        self._init_ui()

    def _row_label(self, key: str) -> QLabel:
        label = QLabel(t(key))
        label.setObjectName("rowLabel")
        self._row_labels[key] = label
        return label

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(t("smart_mic_title"))
        title.setObjectName("sectionTitle")
        self._title_label = title
        header.addWidget(title)
        header.addStretch()

        status_tag = QLabel(t("smart_mic_status_active"))
        status_tag.setObjectName("statusPill")
        header.addWidget(status_tag)

        self._enable_check = QCheckBox(t("smart_mic_enable"))
        self._enable_check.setObjectName("deckCheck")
        self._enable_check.setStyleSheet("font-weight: 600; color: #63c3ee;")
        self._enable_check.setChecked(self.engine.enabled)
        self._enable_check.toggled.connect(self._on_enable_toggled)
        header.addWidget(self._enable_check)
        layout.addLayout(header)

        profile_row = QHBoxLayout()
        profile_row.setSpacing(10)
        profile_row.addWidget(self._row_label("smart_mic_profile"))
        self._profile_combo = QComboBox()
        self._profile_combo.setObjectName("deckCombo")
        self._profile_combo.setMinimumWidth(260)
        for prof in SMART_MIC_PROFILES:
            self._profile_combo.addItem(t(f"profile_{prof}"), prof)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self._profile_combo)
        profile_row.addStretch()
        layout.addLayout(profile_row)

        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(16)

        self._hum_check = QCheckBox(t("smart_mic_hum_filter"))
        self._hum_check.setObjectName("deckCheck")
        self._hum_check.setChecked(self.engine.hum_filter_enabled)
        self._hum_check.toggled.connect(lambda c: setattr(self.engine, "hum_filter_enabled", bool(c)))
        toggles_row.addWidget(self._hum_check)

        self._gate_check = QCheckBox(t("smart_mic_noise_gate"))
        self._gate_check.setObjectName("deckCheck")
        self._gate_check.setChecked(self.engine.gate_enabled)
        self._gate_check.toggled.connect(lambda c: setattr(self.engine, "gate_enabled", bool(c)))
        toggles_row.addWidget(self._gate_check)

        self._eq_check = QCheckBox(t("smart_mic_speech_eq"))
        self._eq_check.setObjectName("deckCheck")
        self._eq_check.setChecked(self.engine.eq_enabled)
        self._eq_check.toggled.connect(lambda c: setattr(self.engine, "eq_enabled", bool(c)))
        toggles_row.addWidget(self._eq_check)

        self._comp_check = QCheckBox(t("smart_mic_compressor"))
        self._comp_check.setObjectName("deckCheck")
        self._comp_check.setChecked(self.engine.compressor_enabled)
        self._comp_check.toggled.connect(lambda c: setattr(self.engine, "compressor_enabled", bool(c)))
        toggles_row.addWidget(self._comp_check)

        toggles_row.addStretch()
        layout.addLayout(toggles_row)

    def _on_enable_toggled(self, checked: bool):
        self.engine.enabled = bool(checked)

    def _on_profile_changed(self, index: int):
        prof = self._profile_combo.itemData(index) or "clean"
        self.engine.set_profile(prof)

    def retranslate(self):
        if self._title_label is not None:
            self._title_label.setText(t("smart_mic_title"))
        for key, label in self._row_labels.items():
            label.setText(t(key))
        self._enable_check.setText(t("smart_mic_enable"))
        self._hum_check.setText(t("smart_mic_hum_filter"))
        self._gate_check.setText(t("smart_mic_noise_gate"))
        self._eq_check.setText(t("smart_mic_speech_eq"))
        self._comp_check.setText(t("smart_mic_compressor"))
        for index, prof in enumerate(SMART_MIC_PROFILES):
            self._profile_combo.setItemText(index, t(f"profile_{prof}"))


class VoiceEffectsPanel(QFrame):
    """Voice changer controls card bound to the microphone engine's DSP."""

    def __init__(self, effect_engine):
        super().__init__()
        self.setObjectName("fxCard")
        self.engine = effect_engine
        self._has_virtual = False
        self._row_labels: dict[str, QLabel] = {}
        self._title_label: Optional[QLabel] = None
        self._init_ui()
        self._apply_preset(0)

    def _row_label(self, key: str) -> QLabel:
        label = QLabel(t(key))
        label.setObjectName("rowLabel")
        self._row_labels[key] = label
        return label

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel(t("fx_title"))
        title.setObjectName("sectionTitle")
        self._title_label = title
        header.addWidget(title)
        header.addStretch()
        self._enable_check = QCheckBox(t("fx_enable"))
        self._enable_check.setObjectName("deckCheck")
        self._enable_check.setStyleSheet("font-weight: 600; color: #b28ae8;")
        self._enable_check.toggled.connect(self._on_enable_toggled)
        header.addWidget(self._enable_check)
        layout.addLayout(header)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(10)
        preset_row.addWidget(self._row_label("fx_preset"))
        self._preset_combo = QComboBox()
        self._preset_combo.setObjectName("deckCombo")
        self._preset_combo.setMinimumWidth(260)
        for preset_id in PRESET_IDS:
            self._preset_combo.addItem(t(f"preset_{preset_id}"), preset_id)
        preset_row.addWidget(self._preset_combo)
        self._preset_combo.currentIndexChanged.connect(self._apply_preset)
        preset_row.addStretch()
        reset_btn = QPushButton(t("fx_reset"))
        reset_btn.setObjectName("smallButton")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.clicked.connect(self._reset_defaults)
        preset_row.addWidget(reset_btn)
        layout.addLayout(preset_row)

        params_row = QHBoxLayout()
        params_row.setSpacing(10)
        params_row.addWidget(self._row_label("fx_intensity"))
        self._intensity_slider = MicSlider(accent="#b28ae8")
        self._intensity_slider.setValue(_DEFAULT_INTENSITY)
        self._intensity_slider.valueChanged.connect(self._on_intensity_changed)
        params_row.addWidget(self._intensity_slider, 1)
        self._intensity_badge = _value_badge(f"{_DEFAULT_INTENSITY}%")
        params_row.addWidget(self._intensity_badge)

        params_row.addSpacing(8)
        params_row.addWidget(self._row_label("fx_wetdry"))
        self._wet_slider = MicSlider(accent="#7fd8a4")
        self._wet_slider.setValue(_DEFAULT_WET)
        self._wet_slider.valueChanged.connect(self._on_wet_changed)
        params_row.addWidget(self._wet_slider, 1)
        self._wet_badge = _value_badge(f"{_DEFAULT_WET}%")
        params_row.addWidget(self._wet_badge)
        layout.addLayout(params_row)

        self._hint_label = QLabel(t("fx_hint_no_virtual"))
        self._hint_label.setObjectName("fxHint")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

    # ------------------------------------------------------------- events

    def _on_enable_toggled(self, checked: bool):
        self.engine.enabled = bool(checked)

    def _apply_preset(self, index: int):
        preset_id = self._preset_combo.itemData(index) or "clean"
        self.engine.set_preset(preset_id)

    def _on_intensity_changed(self, value: int):
        self.engine.intensity = value / 100.0
        self._intensity_badge.setText(f"{value}%")

    def _on_wet_changed(self, value: int):
        self.engine.wet = value / 100.0
        self._wet_badge.setText(f"{value}%")

    def _reset_defaults(self):
        self._enable_check.setChecked(False)
        self._preset_combo.setCurrentIndex(0)
        self._intensity_slider.setValue(_DEFAULT_INTENSITY)
        self._wet_slider.setValue(_DEFAULT_WET)

    # --------------------------------------------------------------- hint

    def set_virtual_hint(self, has_virtual: bool):
        self._has_virtual = bool(has_virtual)
        self._hint_label.setText(t("fx_hint_virtual") if has_virtual else t("fx_hint_no_virtual"))

    def retranslate(self):
        if self._title_label is not None:
            self._title_label.setText(t("fx_title"))
        for key, label in self._row_labels.items():
            label.setText(t(key))
        self._enable_check.setText(t("fx_enable"))
        for index, preset_id in enumerate(PRESET_IDS):
            self._preset_combo.setItemText(index, t(f"preset_{preset_id}"))
        self.set_virtual_hint(self._has_virtual)
