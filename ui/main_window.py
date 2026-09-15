"""Premium SonicDeck Windows audio mixer UI."""

import sys
from typing import List, Optional

from PySide6.QtCore import QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from sonicdeck.audio.mixer import WindowsAudioMixer
from sonicdeck.audio.session import AudioSession
from sonicdeck.config.settings import settings
from sonicdeck.i18n import Language, is_rtl, set_language, t
from sonicdeck.ui.mic_panel import MicrophonePanel, SmartMicPanel, VoiceEffectsPanel


class SessionRefreshWorker(QThread):
    """Enumerate sessions off the GUI thread."""

    sessions_updated = Signal(list)
    error_occurred = Signal(str)

    def __init__(self, mixer: WindowsAudioMixer):
        super().__init__()
        self.mixer = mixer

    def run(self):
        try:
            self.sessions_updated.emit(self.mixer.get_active_sessions())
        except Exception as error:
            self.error_occurred.emit(str(error))


class AudioCommandWorker(QThread):
    """Run one potentially blocking Core Audio write away from the GUI thread."""

    completed = Signal(bool, str)

    def __init__(self, mixer: WindowsAudioMixer, command: str, process_name: str = "", value=None):
        super().__init__()
        self.mixer = mixer
        self.command = command
        self.process_name = process_name
        self.value = value

    def run(self):
        try:
            if self.command == "session_volume":
                result = self.mixer.set_session_volume(self.process_name, self.value)
            elif self.command == "session_mute":
                result = self.mixer.set_session_mute(self.process_name, self.value)
            elif self.command == "master_volume":
                self.mixer.set_master_volume(self.value)
                result = True
            else:
                self.mixer.set_master_mute(self.value)
                result = True
            self.completed.emit(bool(result), self.command)
        except Exception as error:
            self.completed.emit(False, str(error))


class MasterStateWorker(QThread):
    """Read master Core Audio state without blocking widget construction."""

    state_read = Signal(float, bool)

    def __init__(self, mixer: WindowsAudioMixer):
        super().__init__()
        self.mixer = mixer

    def run(self):
        try:
            self.state_read.emit(self.mixer.get_master_volume(), self.mixer.get_master_mute())
        except Exception:
            pass


class StyledSlider(QSlider):
    """Consistent compact slider used by master and session controls."""

    def __init__(self, large=False):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 100)
        self.setFixedHeight(28 if large else 24)
        self.setStyleSheet("""
            QSlider::groove:horizontal { background: #293444; height: 6px; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #3ca6e8; border-radius: 3px; }
            QSlider::handle:horizontal { background: #f4f7fb; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }
            QSlider::handle:horizontal:hover { background: #72c7f2; }
        """)


class SessionCard(QFrame):
    volume_changed = Signal(str, float)
    mute_changed = Signal(str, bool)

    def __init__(self, session: AudioSession):
        super().__init__()
        self.session = session
        self._updating = False
        self._pending_volume: Optional[float] = None
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(80)
        self._volume_timer.timeout.connect(self._emit_pending_volume)
        self.setObjectName("sessionCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        layout.setSpacing(16)
        icon = QLabel()
        icon.setFixedSize(44, 44)
        icon.setAlignment(Qt.AlignCenter)
        pixmap = self._get_icon_pixmap()
        if pixmap:
            icon.setPixmap(pixmap)
        else:
            icon.setText("◉")
            icon.setStyleSheet("color: #54b8e8; font-size: 25px;")
        layout.addWidget(icon)

        info = QVBoxLayout()
        info.setSpacing(3)
        self._name_label = QLabel(self.session.name or self.session.process_name)
        self._name_label.setObjectName("sessionName")
        info.addWidget(self._name_label)
        self._details_label = QLabel(f"{self.session.process_name}  ·  {self.session.category}")
        self._details_label.setObjectName("sessionDetails")
        info.addWidget(self._details_label)
        layout.addLayout(info, 1)

        controls = QVBoxLayout()
        controls.setSpacing(5)
        top = QHBoxLayout()
        top.addWidget(QLabel(t("volume")))
        top.addStretch()
        self._volume_label = QLabel()
        self._volume_label.setObjectName("volumeValue")
        top.addWidget(self._volume_label)
        controls.addLayout(top)
        self._volume_slider = StyledSlider()
        self._volume_slider.setValue(round(self.session.volume * 100))
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        self._volume_slider.sliderReleased.connect(self._flush_volume)
        controls.addWidget(self._volume_slider)
        layout.addLayout(controls, 2)

        self._mute_btn = QPushButton()
        self._mute_btn.setObjectName("muteButton")
        self._mute_btn.setFixedWidth(106)
        self._mute_btn.clicked.connect(self._on_mute_clicked)
        layout.addWidget(self._mute_btn)
        self._set_display_state(self.session.volume, self.session.is_muted)

    def _get_icon_pixmap(self) -> Optional[QPixmap]:
        if not self.session.icon_path:
            return None
        try:
            pixmap = QPixmap(self.session.icon_path)
            return pixmap.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pixmap.isNull() else None
        except Exception:
            return None

    def _set_display_state(self, volume: float, muted: bool):
        percent = round(max(0.0, min(1.0, volume)) * 100)
        self._volume_label.setText(f"{percent}%")
        self._mute_btn.setText(t("unmute") if muted else t("mute"))
        self._mute_btn.setProperty("muted", muted)
        self._mute_btn.style().unpolish(self._mute_btn)
        self._mute_btn.style().polish(self._mute_btn)

    def _on_volume_changed(self, value: int):
        if self._updating:
            return
        self._pending_volume = value / 100.0
        self._set_display_state(self._pending_volume, self.session.is_muted)
        self._volume_timer.start()

    def _flush_volume(self):
        self._volume_timer.stop()
        self._emit_pending_volume()

    def _emit_pending_volume(self):
        if self._pending_volume is not None:
            volume = self._pending_volume
            self._pending_volume = None
            self.session.volume = volume
            self.volume_changed.emit(self.session.process_name, volume)

    def _on_mute_clicked(self):
        muted = not self.session.is_muted
        self.session.is_muted = muted
        self._set_display_state(self.session.volume, muted)
        self.mute_changed.emit(self.session.process_name, muted)

    def update_session(self, session: AudioSession):
        self.session = session
        self._updating = True
        self._volume_slider.setValue(round(session.volume * 100))
        self._set_display_state(session.volume, session.is_muted)
        self._updating = False


class MasterVolumeWidget(QFrame):
    volume_changed = Signal(float)
    mute_changed = Signal(bool)

    def __init__(self, mixer: WindowsAudioMixer):
        super().__init__()
        self.mixer = mixer
        self._updating = False
        self._pending_volume: Optional[float] = None
        self._volume_timer = QTimer(self)
        self._volume_timer.setSingleShot(True)
        self._volume_timer.setInterval(80)
        self._volume_timer.timeout.connect(self._emit_pending_volume)
        self.setObjectName("masterCard")
        self._init_ui()
        self._load_master_volume()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel(t("master_volume"))
        title.setObjectName("masterTitle")
        title_box.addWidget(title)
        device = QLabel(t("default_output_active"))
        device.setObjectName("mutedText")
        title_box.addWidget(device)
        header.addLayout(title_box)
        header.addStretch()
        self._volume_label = QLabel("100%")
        self._volume_label.setObjectName("masterValue")
        header.addWidget(self._volume_label)
        layout.addLayout(header)
        self._slider = StyledSlider(large=True)
        self._slider.valueChanged.connect(self._on_slider_changed)
        self._slider.sliderReleased.connect(self._flush_volume)
        layout.addWidget(self._slider)
        actions = QHBoxLayout()
        actions.addWidget(QLabel(t("output_volume")))
        actions.addStretch()
        self._mute_btn = QPushButton(t("mute"))
        self._mute_btn.setObjectName("masterMuteButton")
        self._mute_btn.setFixedWidth(110)
        self._mute_btn.clicked.connect(self._on_mute_clicked)
        actions.addWidget(self._mute_btn)
        layout.addLayout(actions)

    def _set_display_state(self, volume: float, muted: bool):
        percent = round(max(0.0, min(1.0, volume)) * 100)
        self._volume_label.setText(f"{percent}%")
        self._mute_btn.setText(t("unmute") if muted else t("mute"))
        self._mute_btn.setProperty("muted", muted)
        self._mute_btn.style().unpolish(self._mute_btn)
        self._mute_btn.style().polish(self._mute_btn)

    def _on_slider_changed(self, value: int):
        if not self._updating:
            self._pending_volume = value / 100.0
            self._set_display_state(self._pending_volume, self._mute_btn.property("muted") is True)
            self._volume_timer.start()

    def _flush_volume(self):
        self._volume_timer.stop()
        self._emit_pending_volume()

    def _emit_pending_volume(self):
        if self._pending_volume is not None:
            volume = self._pending_volume
            self._pending_volume = None
            self.volume_changed.emit(volume)

    def _on_mute_clicked(self):
        muted = self._mute_btn.text() == t("mute")
        self._set_display_state(self._slider.value() / 100.0, muted)
        self.mute_changed.emit(muted)

    def _load_master_volume(self):
        self._master_state_worker = MasterStateWorker(self.mixer)
        self._master_state_worker.state_read.connect(self._apply_master_state)
        self._master_state_worker.finished.connect(self._clear_master_state_worker)
        self._master_state_worker.finished.connect(self._master_state_worker.deleteLater)
        self._master_state_worker.start()

    @Slot()
    def _clear_master_state_worker(self):
        self._master_state_worker = None

    @Slot(float, bool)
    def _apply_master_state(self, volume: float, muted: bool):
        self._updating = True
        self._slider.setValue(round(volume * 100))
        self._set_display_state(volume, muted)
        self._updating = False

    def update_mute(self, muted: bool):
        self._set_display_state(self._slider.value() / 100.0, muted)

    def shutdown(self):
        """Wait for the initial master read before the widget is destroyed."""
        if self._master_state_worker is not None and self._master_state_worker.isRunning():
            self._master_state_worker.wait(1000)


class SonicDeck(QMainWindow):
    """Main window with non-blocking refresh and incremental card updates."""

    def __init__(self):
        super().__init__()
        self.mixer = None
        self._sessions: List[AudioSession] = []
        self._session_widgets = {}
        self._category_buttons = {}
        self._refresh_worker = None
        self._command_workers = []
        self._active_category = "All"
        self._settings_button = None
        self._content_stack = None
        self._mic_panel = None
        self._smart_mic_panel = None
        self._voice_fx_panel = None
        self._audio_scroll = None
        try:
            self.mixer = WindowsAudioMixer(refresh_interval_ms=3000)
        except Exception as error:
            QMessageBox.critical(self, "Audio Initialization Failed", str(error))
        self._init_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_sessions)
        self._refresh_timer.setInterval(settings.get("refresh_interval_ms", 3000))
        if settings.get("auto_refresh", True):
            self._refresh_timer.start()

    def _init_ui(self):
        self.setWindowTitle(t("app_title"))
        self.setWindowIcon(self._get_window_icon())
        self.resize(1180, 780)
        self.setMinimumSize(820, 600)
        self._apply_theme()
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._create_sidebar())
        root.addWidget(self._create_content(), 1)
        self.statusBar().showMessage(t("ready"))
        direction = Qt.RightToLeft if is_rtl() else Qt.LeftToRight
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(direction)
        self.setLayoutDirection(direction)

    def _apply_theme(self):
        self.setStyleSheet("""
            QMainWindow { background: #10151c; }
            QWidget { color: #eef4fa; font-family: 'Segoe UI'; font-size: 10pt; }
            QWidget#centralWidget, QWidget#contentArea, QStackedWidget { background: #10151c; }
            QLabel { background: transparent; }
            QFrame#sidebar { background: #151c25; border-right: 1px solid #273443; }
            QFrame#sessionCard, QFrame#masterCard, QFrame#micCard, QFrame#fxCard { background: #1b2634; border: 1px solid #34485f; border-radius: 12px; }
            QFrame#sessionCard:hover { border: 1px solid #3c617d; background: #1e2c3b; }
            QLabel#brand { color: #f6f9fc; font-size: 21pt; font-weight: 700; background: transparent; }
            QLabel#eyebrow { color: #74c6ee; font-size: 9pt; font-weight: 700; letter-spacing: 1px; background: transparent; }
            QLabel#pageTitle { color: #f6f9fc; font-size: 22pt; font-weight: 700; background: transparent; }
            QLabel#pageSubtitle, QLabel#mutedText, QLabel#sessionDetails, QLabel#fxHint { color: #a3b4c6; font-size: 9pt; background: transparent; }
            QLabel#sessionName { color: #f5f8fb; font-size: 11pt; font-weight: 600; background: transparent; }
            QLabel#volumeValue, QLabel#valueBadge { background: transparent; color: #5ebde8; font-size: 11pt; font-weight: 700; padding: 1px 4px; }
            QLabel#masterValue { background: transparent; color: #5ebde8; font-size: 24pt; font-weight: 700; padding: 0px 4px; }
            QLabel#sectionTitle { color: #f5f8fb; font-size: 13pt; font-weight: 700; background: transparent; }
            QLabel#masterTitle { color: #f6f9fc; font-size: 14pt; font-weight: 700; background: transparent; }
            QLabel#rowLabel { color: #c3d2e0; font-weight: 600; background: transparent; }
            QLabel#statusPill { background: #202d3c; border: 1px solid #364d66; border-radius: 10px; padding: 3px 12px; color: #dceaf6; font-weight: 600; }
            QPushButton#navButton, QPushButton#settingsButton { background: transparent; color: #aebdc9; border: 0; border-radius: 8px; padding: 11px 13px; text-align: left; }
            QPushButton#navButton:hover, QPushButton#settingsButton:hover { background: #202d3b; color: #f6f9fc; }
            QPushButton#settingsButton[selected="true"] { background: #1f465f; color: #e9f7ff; border-left: 3px solid #63c3ee; }
            QPushButton#navButton[selected="true"] { background: #1f465f; color: #e9f7ff; border-left: 3px solid #63c3ee; }
            QPushButton#micNavButton { background: #16324a; color: #cfeafc; border: 1px solid #2c5b7d; border-radius: 8px; padding: 11px 13px; text-align: left; font-weight: 600; }
            QPushButton#micNavButton:hover { background: #1d405e; color: #f0f9ff; }
            QPushButton#muteButton, QPushButton#masterMuteButton { background: #233445; color: #e2eef7; border: 1px solid #3d586f; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
            QPushButton#muteButton:hover, QPushButton#masterMuteButton:hover { background: #2b516b; }
            QPushButton#muteButton[muted="true"], QPushButton#masterMuteButton[muted="true"] { background: #713a43; border-color: #a95562; color: #ffe9eb; }
            QPushButton#smallButton { background: #22364a; color: #d8e9f5; border: 1px solid #3a556e; border-radius: 7px; padding: 7px 14px; font-weight: 600; }
            QPushButton#smallButton:hover { background: #2b516b; }
            QComboBox#deckCombo, QSpinBox { background: #0d141c; color: #eef4fa; border: 1px solid #3a556e; border-radius: 7px; padding: 6px 10px; min-height: 22px; }
            QComboBox#deckCombo:hover, QSpinBox:hover { border: 1px solid #4d7291; }
            QComboBox#deckCombo::drop-down { border: 0; width: 26px; }
            QComboBox#deckCombo QAbstractItemView { background: #0d141c; color: #eef4fa; border: 1px solid #3a556e; selection-background-color: #1f465f; selection-color: #ffffff; outline: 0; }
            QCheckBox#deckCheck { color: #d3e2ee; spacing: 8px; background: transparent; }
            QCheckBox#deckCheck::indicator { width: 17px; height: 17px; border: 1px solid #3a556e; border-radius: 4px; background: #0d141c; }
            QCheckBox#deckCheck::indicator:checked { background: #3ca6e8; border-color: #3ca6e8; }
            QSpinBox::up-button, QSpinBox::down-button { background: #22364a; border: 1px solid #3a556e; width: 18px; }
            QToolTip { background: #0d141c; color: #eef4fa; border: 1px solid #3a556e; padding: 4px; }
            QScrollArea { border: 0; background: transparent; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 4px; }
            QScrollBar::handle:vertical { background: #304253; border-radius: 5px; min-height: 30px; }
            QStatusBar { background: #151c25; color: #9fb1c4; border-top: 1px solid #273443; }
        """)

    def _get_window_icon(self):
        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#247da6"))
        return QIcon(pixmap)

    def _create_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(236)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(22, 24, 18, 18)
        layout.setSpacing(8)
        brand = QLabel("SonicDeck")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        tagline = QLabel("AUDIO CONTROL CENTER")
        tagline.setObjectName("eyebrow")
        layout.addWidget(tagline)
        layout.addSpacing(28)
        label = QLabel(t("categories").upper())
        label.setObjectName("mutedText")
        layout.addWidget(label)
        categories = [("All", "All"), ("games", t("games")), ("chat", t("chat")), ("music", t("music")), ("browsers", t("browsers")), ("system", t("system"))]
        for key, text in categories:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, selected=key: self._select_category(selected))
            self._category_buttons[key] = button
            layout.addWidget(button)
        layout.addSpacing(12)
        mic_button = QPushButton(t("mic_title"))
        mic_button.setObjectName("micNavButton")
        mic_button.setCursor(Qt.PointingHandCursor)
        mic_button.clicked.connect(self._goto_microphone)
        layout.addWidget(mic_button)
        layout.addStretch()
        settings_button = QPushButton(t("settings"))
        settings_button.setObjectName("settingsButton")
        settings_button.setCursor(Qt.PointingHandCursor)
        settings_button.clicked.connect(self._show_settings)
        self._settings_button = settings_button
        layout.addWidget(settings_button)
        self._category_buttons["All"].setProperty("selected", True)
        return sidebar

    def _create_content(self):
        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._create_audio_content())
        self._content_stack.addWidget(self._create_settings_page())
        return self._content_stack

    def _create_audio_content(self):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 28, 34, 20)
        layout.setSpacing(18)
        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("SonicDeck")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        subtitle = QLabel("Windows Audio Mixer  ·  Audio Engine • Active")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        layout.addLayout(heading)
        if self.mixer:
            self._master_widget = MasterVolumeWidget(self.mixer)
            self._master_widget.volume_changed.connect(self._on_master_volume_changed)
            self._master_widget.mute_changed.connect(self._on_master_mute_changed)
            layout.addWidget(self._master_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        self._sessions_layout = QVBoxLayout(container)
        self._sessions_layout.setContentsMargins(0, 0, 0, 0)
        self._sessions_layout.setSpacing(10)
        self._empty_label = QLabel(t("loading_sessions"))
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setObjectName("mutedText")
        self._sessions_layout.addWidget(self._empty_label)
        self._mic_panel = MicrophonePanel()
        self._smart_mic_panel = SmartMicPanel(self._mic_panel.engine.smart_mic_engine)
        self._voice_fx_panel = VoiceEffectsPanel(self._mic_panel.engine.effect_engine)
        self._mic_panel.fx_panel = self._voice_fx_panel
        self._mic_panel.smart_mic_panel = self._smart_mic_panel
        self._sessions_layout.addWidget(self._mic_panel)
        self._sessions_layout.addWidget(self._smart_mic_panel)
        self._sessions_layout.addWidget(self._voice_fx_panel)
        self._sessions_layout.addStretch()
        scroll.setWidget(container)
        self._audio_scroll = scroll
        layout.addWidget(scroll, 1)
        self._status_label = QLabel(t("loading_sessions"))
        self._status_label.setObjectName("mutedText")
        layout.addWidget(self._status_label)
        return content

    def _create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 20)
        layout.setSpacing(18)

        title = QLabel(t("settings_title"))
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        options = QFrame()
        options.setObjectName("masterCard")
        options_layout = QVBoxLayout(options)
        options_layout.setContentsMargins(24, 22, 24, 22)
        options_layout.setSpacing(14)

        language_label = QLabel(t("language"))
        language_combo = QComboBox()
        language_combo.addItem(t("english"), Language.ENGLISH.value)
        language_combo.addItem(t("arabic"), Language.ARABIC.value)
        language_combo.setCurrentIndex(0 if settings.language == Language.ENGLISH.value else 1)
        language_combo.currentIndexChanged.connect(self._on_language_changed)
        self._language_label = language_label
        self._language_combo = language_combo
        language_row = QHBoxLayout()
        language_row.addWidget(language_label)
        language_row.addStretch()
        language_row.addWidget(language_combo)
        options_layout.addLayout(language_row)

        self._auto_refresh_check = QCheckBox(t("auto_refresh"))
        self._auto_refresh_check.setChecked(settings.get("auto_refresh", True))
        self._auto_refresh_check.toggled.connect(self._on_auto_refresh_changed)
        options_layout.addWidget(self._auto_refresh_check)

        interval_row = QHBoxLayout()
        self._refresh_interval_label = QLabel(t("refresh_interval"))
        interval_row.addWidget(self._refresh_interval_label)
        interval_row.addStretch()
        self._refresh_interval_spin = QSpinBox()
        self._refresh_interval_spin.setRange(1, 60)
        self._refresh_interval_spin.setSuffix(" s")
        self._refresh_interval_spin.setValue(round(settings.get("refresh_interval_ms", 3000) / 1000))
        self._refresh_interval_spin.valueChanged.connect(self._on_refresh_interval_changed)
        interval_row.addWidget(self._refresh_interval_spin)
        options_layout.addLayout(interval_row)
        layout.addWidget(options)

        about = QLabel(f"{t('about')}\n{t('version')}")
        about.setObjectName("mutedText")
        layout.addWidget(about)
        layout.addStretch()
        self._settings_title = title
        self._about_label = about
        return page

    def _show_audio(self):
        self._content_stack.setCurrentIndex(0)
        self._settings_button.setProperty("selected", False)
        self._settings_button.style().unpolish(self._settings_button)
        self._settings_button.style().polish(self._settings_button)

    def _show_settings(self):
        self._content_stack.setCurrentIndex(1)
        self._settings_button.setProperty("selected", True)
        self._settings_button.style().unpolish(self._settings_button)
        self._settings_button.style().polish(self._settings_button)

    def _goto_microphone(self):
        """Jump to the microphone studio card at the bottom of the audio page."""
        self._show_audio()
        if self._audio_scroll is not None:
            def _scroll_to_mic():
                bar = self._audio_scroll.verticalScrollBar()
                bar.setValue(bar.maximum())
            QTimer.singleShot(0, _scroll_to_mic)

    def _on_auto_refresh_changed(self, enabled):
        settings.set("auto_refresh", enabled)
        if enabled:
            self._refresh_timer.start()
        else:
            self._refresh_timer.stop()

    def _on_refresh_interval_changed(self, seconds):
        interval_ms = seconds * 1000
        settings.set("refresh_interval_ms", interval_ms)
        self._refresh_timer.setInterval(interval_ms)

    def _on_language_changed(self, index):
        language = Language(self._language_combo.itemData(index))
        settings.language = language.value
        set_language(language)
        direction = Qt.RightToLeft if is_rtl() else Qt.LeftToRight
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(direction)
        self.setLayoutDirection(direction)
        self._settings_title.setText(t("settings_title"))
        self._language_label.setText(t("language"))
        self._language_combo.setItemText(0, t("english"))
        self._language_combo.setItemText(1, t("arabic"))
        self._auto_refresh_check.setText(t("auto_refresh"))
        self._refresh_interval_label.setText(t("refresh_interval"))
        self._about_label.setText(f"{t('about')}\n{t('version')}")
        self._settings_button.setText(t("settings"))
        if self._mic_panel is not None:
            self._mic_panel.retranslate()
        if self._smart_mic_panel is not None:
            self._smart_mic_panel.retranslate()
        if self._voice_fx_panel is not None:
            self._voice_fx_panel.retranslate()

    def _select_category(self, category):
        self._show_audio()
        self._active_category = category
        for key, button in self._category_buttons.items():
            button.setProperty("selected", key == category)
            button.style().unpolish(button)
            button.style().polish(button)
        self._update_ui_sessions()

    def _refresh_sessions(self):
        if self.mixer and (self._refresh_worker is None or not self._refresh_worker.isRunning()):
            self._refresh_worker = SessionRefreshWorker(self.mixer)
            self._refresh_worker.sessions_updated.connect(self._on_sessions_updated)
            self._refresh_worker.error_occurred.connect(self._on_refresh_error)
            self._refresh_worker.start()

    @Slot(list)
    def _on_sessions_updated(self, sessions):
        self._sessions = sessions
        self._update_ui_sessions()

    def _visible_sessions(self):
        if self._active_category == "All":
            return self._sessions
        category_map = {"games": "🎮 Games", "chat": "💬 Chat", "music": "🎵 Music/Media", "browsers": "🌐 Browsers", "system": "🔊 System/Other"}
        return [session for session in self._sessions if session.category == category_map[self._active_category]]

    def _update_ui_sessions(self):
        visible = self._visible_sessions()
        visible_keys = {session.process_name for session in visible}
        for process_name in list(self._session_widgets):
            if process_name not in visible_keys:
                card = self._session_widgets.pop(process_name)
                self._sessions_layout.removeWidget(card)
                card.deleteLater()
        for session in visible:
            card = self._session_widgets.get(session.process_name)
            if card is None:
                card = SessionCard(session)
                card.volume_changed.connect(self._on_session_volume_changed)
                card.mute_changed.connect(self._on_session_mute_changed)
                self._session_widgets[session.process_name] = card
                # Insert above the microphone card so session cards stay grouped.
                insert_at = self._sessions_layout.indexOf(self._mic_panel) \
                    if self._mic_panel is not None else self._sessions_layout.count() - 1
                self._sessions_layout.insertWidget(insert_at, card)
            else:
                card.update_session(session)
        self._empty_label.setVisible(not visible)
        self._empty_label.setText(t("no_sessions") if not self._sessions else t("no_category_sessions"))
        self._status_label.setText(t("session_count", count=len(visible)))

    @Slot(str)
    def _on_refresh_error(self, message):
        self._status_label.setText(f"{t('error_prefix')}{message}")

    def _run_command(self, command, process_name="", value=None):
        if not self.mixer:
            return
        worker = AudioCommandWorker(self.mixer, command, process_name, value)
        worker.completed.connect(self._on_command_completed)
        worker.finished.connect(lambda: self._discard_worker(worker))
        self._command_workers.append(worker)
        worker.start()

    def _discard_worker(self, worker):
        if worker in self._command_workers:
            self._command_workers.remove(worker)
        worker.deleteLater()

    @Slot(bool, str)
    def _on_command_completed(self, success, command):
        if not success:
            self.statusBar().showMessage(t("audio_command_failed"))
        elif command == "master_volume":
            self.statusBar().showMessage(t("master_volume_updated"))

    @Slot(str, float)
    def _on_session_volume_changed(self, process_name, volume):
        self._run_command("session_volume", process_name, volume)

    @Slot(str, bool)
    def _on_session_mute_changed(self, process_name, muted):
        self._run_command("session_mute", process_name, muted)

    @Slot(float)
    def _on_master_volume_changed(self, volume):
        self._run_command("master_volume", value=volume)

    @Slot(bool)
    def _on_master_mute_changed(self, muted):
        self._master_widget.update_mute(muted)
        self._run_command("master_mute", value=muted)

    def closeEvent(self, event):
        self._refresh_timer.stop()
        if hasattr(self, "_master_widget"):
            self._master_widget.shutdown()
        if self._mic_panel is not None:
            self._mic_panel.shutdown()
        if self._refresh_worker and self._refresh_worker.isRunning():
            self._refresh_worker.wait(1000)
        for worker in self._command_workers:
            if worker.isRunning():
                worker.wait(1000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = SonicDeck()
    window.show()
    window._refresh_sessions()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
