"""SonicDeck Audio Backend Abstraction."""

from .mixer import WindowsAudioMixer, AudioManagerError
from .session import AudioSession, SessionState

__all__ = [
    "WindowsAudioMixer",
    "AudioManagerError", 
    "AudioSession",
    "SessionState"
]
