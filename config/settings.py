"""Application settings management."""


class Settings:
    """Singleton settings manager."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Default settings
        self.settings = {
            "language": "en",
            "master_volume": 1.0,
            "refresh_interval_ms": 3000,
            "dark_theme": True,
            "auto_refresh": True,
            "show_empty_state": True,
        }
        self._initialized = True
    
    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.settings.get(key, default)
    
    def set(self, key: str, value):
        """Set a setting value."""
        self.settings[key] = value
    
    @property
    def language(self) -> str:
        """Get current language."""
        return self.settings.get("language", "en")
    
    @language.setter
    def language(self, value: str):
        """Set language."""
        self.settings["language"] = value
    
    @property
    def master_volume(self) -> float:
        """Get master volume (0-1)."""
        return self.settings.get("master_volume", 1.0)
    
    @master_volume.setter
    def master_volume(self, value: float):
        """Set master volume."""
        clamped = max(0, min(1, value))
        self.settings["master_volume"] = clamped
    
    @property
    def dark_theme(self) -> bool:
        """Check if dark theme is enabled."""
        return self.settings.get("dark_theme", True)


# Global settings instance
settings = Settings()