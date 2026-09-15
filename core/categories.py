"""Session categorization logic."""


# Process name to category mapping
APP_CATEGORIES = {
    # Games
    "steam.exe": {"name": "Game", "category": "🎮 Games"},
    "store.exe": {"name": "Store", "category": "🎮 Games"},
    "xboxgamebar.exe": {"name": "Xbox Game Bar", "category": "🎮 Games"},
    
    # Chat/VoIP
    "discord.exe": {"name": "Discord", "category": "💬 Chat"},
    "teams.exe": {"name": "Teams", "category": "💬 Chat"},
    "zoom.exe": {"name": "Zoom", "category": "💬 Chat"},
    "skype.exe": {"name": "Skype", "category": "💬 Chat"},
    "slack.exe": {"name": "Slack", "category": "💬 Chat"},
    
    # Music/Media
    "spotify.exe": {"name": "Spotify", "category": "🎵 Music/Media"},
    "music.exe": {"name": "Music", "category": "🎵 Music/Media"},
    "vlc.exe": {"name": "VLC Media Player", "category": "🎵 Music/Media"},
    "mpc-hc.exe": {"name": "MPC-HC", "category": "🎵 Music/Media"},
    "potplayer.exe": {"name": "PotPlayer", "category": "🎵 Music/Media"},
    
    # Browsers
    "msedge.exe": {"name": "Microsoft Edge", "category": "🌐 Browsers"},
    "chrome.exe": {"name": "Google Chrome", "category": "🌐 Browsers"},
    "firefox.exe": {"name": "Firefox", "category": "🌐 Browsers"},
    "opera.exe": {"name": "Opera", "category": "🌐 Browsers"},
    
    # System
    "explorer.exe": {"name": "File Explorer", "category": "🔊 System"},
    "taskhostw.exe": {"name": "Task Host", "category": "🔊 System"},
}


def categorize_session(process_name: str) -> str:
    """
    Auto-categorize a session based on its process name.
    
    Args:
        process_name: The Windows process name (e.g., "spotify.exe")
        
    Returns:
        The category string with emoji prefix
    """
    # Normalize the process name
    normalized = process_name.lower()
    
    # Check exact match first
    if normalized in APP_CATEGORIES:
        return APP_CATEGORIES[normalized]["category"]
    
    # Partial matching for common patterns
    process_name_lower = normalized.lower()
    
    if any(name in process_name_lower for name in ["game", "tarkov"]):
        return "🎮 Games"
    elif any(name in process_name_lower for name in ["chat", "discord", "teams", "zoom", 
                                                     "skype", "slack"]):
        return "💬 Chat"
    elif any(name in process_name_lower for name in ["music", "spotify", "vlc", "potplayer"]):
        return "🎵 Music/Media"
    elif any(name in process_name_lower for name in ["chrome", "firefox", "edge", "opera"]):
        return "🌐 Browsers"
    else:
        return "🔊 System/Other"