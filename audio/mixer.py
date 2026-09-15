"""Windows Core Audio Mixer implementation using pycaw and comtypes."""
import comtypes, psutil
from pycaw.pycaw import AudioUtilities
from sonicdeck.audio.session import AudioSession, SessionState
from sonicdeck.core.categories import categorize_session

class AudioManagerError(Exception): pass

class WindowsAudioMixer:
    """Manages master volume and per-application audio sessions on Windows."""
    def __init__(self, refresh_interval_ms: int = 3000):
        self.refresh_interval_ms = refresh_interval_ms
        self._init_com()
        
    def _init_com(self) -> None:
        try: comtypes.CoInitialize()
        except Exception: pass

    def get_master_volume_control(self):
        self._init_com()
        try:
            device = AudioUtilities.GetSpeakers()
            return device.EndpointVolume if device else None
        except Exception: return None

    def get_master_volume(self) -> float:
        ctrl = self.get_master_volume_control()
        try: return ctrl.GetMasterVolumeLevelScalar() if ctrl else 1.0
        except Exception: return 1.0

    def set_master_volume(self, volume: float) -> None:
        ctrl = self.get_master_volume_control()
        if ctrl:
            try: ctrl.SetMasterVolumeLevelScalar(max(0.0, min(1.0, volume)), None)
            except Exception: pass

    def get_master_mute(self) -> bool:
        ctrl = self.get_master_volume_control()
        try: return bool(ctrl.GetMute()) if ctrl else False
        except Exception: return False

    def set_master_mute(self, mute: bool) -> None:
        ctrl = self.get_master_volume_control()
        if ctrl:
            try: ctrl.SetMute(mute, None)
            except Exception: pass

    def get_active_sessions(self) -> list[AudioSession]:
        self._init_com()
        sessions_map = {}
        try: pycaw_sessions = AudioUtilities.GetAllSessions()
        except Exception: return []

        for s in pycaw_sessions:
            try:
                pid = s.ProcessId
                if pid == 0:
                    process_name, display_name, exe_path = "system", "System Sounds", None
                else:
                    try:
                        proc = psutil.Process(pid)
                        process_name = proc.name().lower()
                        exe_path = proc.exe()
                        display_name = process_name.replace(".exe", "").capitalize()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        process_name, display_name, exe_path = "unknown", "Unknown Application", None
                
                if s.DisplayName and not s.DisplayName.startswith("@") and s.DisplayName.strip():
                    display_name = s.DisplayName
                if s.State == 2: continue

                vol_control = s.SimpleAudioVolume
                vol = vol_control.GetMasterVolume()
                is_muted = bool(vol_control.GetMute())
                
                session_state = SessionState.Unknown
                if s.State == 1: session_state = SessionState.PLAYING
                elif s.State == 0: session_state = SessionState.PAUSED
                if is_muted: session_state = SessionState.Muted
                
                key = process_name if process_name != "unknown" else f"unknown_{pid}"
                category = categorize_session(process_name)

                if key in sessions_map:
                    existing = sessions_map[key]
                    existing.volume = max(existing.volume, vol)
                    existing.is_muted = existing.is_muted and is_muted
                    if existing.state != SessionState.PLAYING and session_state == SessionState.PLAYING:
                        existing.state = SessionState.PLAYING
                    if not existing.icon_path and exe_path: existing.icon_path = exe_path
                else:
                    sessions_map[key] = AudioSession(
                        name=display_name, process_name=process_name, volume=vol,
                        is_muted=is_muted, state=session_state, icon_path=exe_path, category=category
                    )
            except Exception: continue
        return list(sessions_map.values())

    def _set_session_prop(self, process_name: str, val, is_mute: bool) -> bool:
        self._init_com()
        try:
            sessions = AudioUtilities.GetAllSessions()
            success = False
            for s in sessions:
                try:
                    pid = s.ProcessId
                    name = "system" if pid == 0 else psutil.Process(pid).name().lower()
                except Exception: name = "unknown"
                if name == process_name.lower():
                    try:
                        if is_mute: s.SimpleAudioVolume.SetMute(bool(val), None)
                        else: s.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, float(val))), None)
                        success = True
                    except Exception: pass
            return success
        except Exception: return False

    def set_session_volume(self, process_name: str, volume: float) -> bool:
        return self._set_session_prop(process_name, volume, is_mute=False)

    def set_session_mute(self, process_name: str, mute: bool) -> bool:
        return self._set_session_prop(process_name, mute, is_mute=True)

