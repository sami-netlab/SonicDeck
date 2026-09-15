"""Audio Session models and data structures."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import uuid


class SessionState(Enum):
    """Enumeration representing the audio session state."""
    PAUSED = "paused"
    PLAYING = "playing"
    Muted = "muted"
    Unknown = "unknown"


@dataclass
class AudioSession:
    """
    Represents an audio session (e.g., Spotify, Discord, Chrome).
    
    Attributes:
        id: Unique identifier for the session.
        name: Display name of the application/session.
        process_name: Windows process name.
        volume: Current volume as a value between 0 and 1.
        is_muted: Whether the session is currently muted.
        state: Current audio state (playing/paused/muted).
        icon_path: Optional path to application icon.
        category: The category this session belongs to.
    """
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    process_name: str = ""
    volume: float = 1.0
    is_muted: bool = False
    state: SessionState = SessionState.Unknown
    icon_path: Optional[str] = None
    category: str = "other"
    
    def __post_init__(self):
        """Initialize with default values if not provided."""
        if self.name:
            self._display_name = self.name
        else:
            self._display_name = self.process_name
    
    @property
    def display_name(self) -> str:
        """Get the display name for UI purposes."""
        return self._display_name
    
    @property
    def volume_percent(self) -> int:
        """Get volume as a percentage (0-100)."""
        return int(self.volume * 100) if not self.is_muted else 0
    
    def set_volume(self, volume: float) -> bool:
        """
        Set the session volume.
        
        Args:
            volume: New volume value between 0 and 1.
            
        Returns:
            True if successful, False otherwise.
        """
        if not 0 <= volume <= 1:
            return False
        self.volume = volume
        return True
    
    def set_mute(self, muted: bool) -> None:
        """Set the mute state."""
        self.is_muted = muted


@dataclass 
class SessionInfo:
    """Raw information gathered about an audio session."""
    name: str
    process_name: str
    pid: int
    volume: float
    is_muted: bool
    icon_path: Optional[str]
    state: Optional[SessionState] = None


@dataclass
class MasterDeviceInfo:
    """Information about the audio device."""
    name: str
    default: bool
    sample_rate: int = 44100
    channels: tuple = (2, 2)