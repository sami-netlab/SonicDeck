"""Multi-language support for SonicDeck."""

from enum import Enum
from typing import Dict, Optional


class Language(Enum):
    """Supported languages."""
    ENGLISH = "en"
    ARABIC = "ar"


class Translations:
    """Centralized translation strings."""
    
    STRINGS = {
        Language.ENGLISH: {
            # Main window
            "app_title": "SonicDeck - Windows Audio Mixer",
            "categories": "Categories",
            "settings": "⚙️ Settings",
            "settings_title": "Settings",
            "language": "Language",
            "english": "English",
            "arabic": "Arabic",
            "auto_refresh": "Auto refresh audio sessions",
            "refresh_interval": "Refresh interval (seconds)",
            "about": "About",
            "version": "SonicDeck 1.0.0",
            "master_volume": "🔊 Master Volume",
            "mute": "Mute",
            "unmute": "Unmute",
            "no_sessions": "No audio sessions found",
            "loading_sessions": "Loading audio sessions...",
            "ready": "Ready",
            "volume": "Volume",
            "output_volume": "Output volume",
            "default_output_active": "Default output device  ·  Audio Engine Active",
            "no_category_sessions": "No sessions in this category",
            "audio_command_failed": "Audio command failed",
            "master_volume_updated": "Master volume updated",

            # Microphone section
            "mic_title": "🎙 Microphone Studio",
            "mic_device": "Input device",
            "mic_volume": "Mic volume",
            "mic_gain": "Gain boost",
            "mic_monitor": "Monitor (hear yourself)",
            "mic_monitor_output": "Monitor output",
            "mic_monitor_default": "System default output",
            "mic_activate": "Activate",
            "mic_deactivate": "Deactivate",
            "mic_refresh": "Refresh",
            "mic_no_input": "No input device found",
            "mic_status_live": "Live",
            "mic_status_idle": "Idle",
            "mic_status_muted": "Muted",
            "mic_status_error": "Error",
            "mic_status_listening": "Capturing input — speak to see the level move.",
            "mic_status_hint": "Activate the microphone to preview your voice with effects.",

            # Voice effects section
            "fx_title": "✨ Voice Effects",
            "fx_enable": "Enable voice effects",
            "fx_preset": "Preset",
            "fx_intensity": "Intensity",
            "fx_wetdry": "Wet / Dry mix",
            "fx_reset": "Reset",
            "fx_virtual_tag": "virtual mic",
            "fx_hint_virtual": "Virtual mic device detected — set “Monitor output” to it in the Microphone Studio, then select that device as the microphone in games, Discord, OBS, etc. to use your transformed voice everywhere.",
            "fx_hint_no_virtual": "Preview/monitoring works out of the box. For system-wide voice changing, install a free virtual audio cable (e.g., VB-CABLE), then select it under “Monitor output” and pick it as the microphone in other apps.",
            # Voice effect presets
            "preset_clean": "Clean / Natural",
            "preset_deep": "Deep Male",
            "preset_warm": "Soft / Warm",
            "preset_bright": "Bright",
            "preset_radio": "Radio Host",
            "preset_telephone": "Telephone",
            "preset_robot": "Robot",
            "preset_monster": "Monster / Dark",
            "preset_tiny": "Tiny / High",
            "preset_scifi": "Sci-Fi",
            "preset_echo": "Echo",
            "preset_reverb": "Reverb Hall",
            "preset_helium": "Tiny / High",
            "preset_alien": "Sci-Fi",
            "preset_comic": "Funny / Comic",
            "preset_villain": "Dark Villain",

            # Smart Mic (Voice Cleanup) section
            "smart_mic_title": "🛡️ Smart Mic (Voice Cleanup)",
            "smart_mic_enable": "Enable Voice Cleanup",
            "smart_mic_profile": "Profile",
            "profile_natural": "Natural (Light)",
            "profile_clean": "Clean (Balanced)",
            "profile_isolation": "Strong Isolation",
            "profile_studio": "Studio Broadcast",
            "smart_mic_noise_gate": "Noise Gate",
            "smart_mic_hum_filter": "50/60Hz Hum Filter",
            "smart_mic_speech_eq": "Speech Clarity EQ",
            "smart_mic_compressor": "Vocal Leveler",
            "smart_mic_limiter": "Safety Limiter",
            "smart_mic_status_active": "Cleanup Active (Local DSP)",

            # Mic Test / Recording section
            "test_title": "🎙️ Mic Test & Comparison",
            "test_record": "Record Test (5s)",
            "test_stop": "Stop",
            "test_play_raw": "Play Raw (Before)",
            "test_play_proc": "Play Processed (After)",
            "test_save": "Save WAV...",
            "test_status_ready": "Ready to record a test sample.",
            "test_status_recording": "Recording... Speak into your microphone.",
            "test_status_recorded": "Recorded test sample. Click Play to compare Before/After.",
            "test_status_playing_raw": "Playing RAW mic audio (unprocessed)...",
            "test_status_playing_proc": "Playing PROCESSED audio (Smart Mic + Effects)...",
            "test_saved_success": "Saved audio to {path}",

            
            # Categories
            "games": "🎮 Games",
            "chat": "💬 Chat",
            "music": "🎵 Music/Media",
            "browsers": "🌐 Browsers",
            "system": "🔊 System/Other",
            
            # Status messages
            "session_count": "Showing {count} active session(s)",
            "master_volume_set": "Master volume: {percent}%",
            "session_muted": "Muted",
            "session_unmuted": "Unmuted",
            "error_prefix": "Error: ",
            "failed_set_volume": "Failed to set volume for {name}",
            "failed_mute": "Failed to mute {name}",
            
            # Application names (for better display)
            "system_sounds": "System Sounds",
            "unknown_app": "Unknown Application",
            "file_explorer": "File Explorer",
        },
        Language.ARABIC: {
            # Main window
            "app_title": "SonicDeck - محرر صوت Windows",
            "categories": "الفئات",
            "settings": "⚙️ الإعدادات",
            "settings_title": "الإعدادات",
            "language": "اللغة",
            "english": "الإنجليزية",
            "arabic": "العربية",
            "auto_refresh": "التحديث التلقائي للجلسات الصوتية",
            "refresh_interval": "فترة التحديث (بالثواني)",
            "about": "حول التطبيق",
            "version": "SonicDeck 1.0.0",
            "master_volume": "🔊 مستوى الصوت الرئيسي",
            "mute": "كتم الصوت",
            "unmute": "إلغاء كتم الصوت",
            "no_sessions": "لم يتم العثور على جلسات صوتية",
            "loading_sessions": "جاري تحميل الجلسات الصوتية...",
            "ready": "جاهز",
            "volume": "مستوى الصوت",
            "output_volume": "مستوى صوت الإخراج",
            "default_output_active": "جهاز الإخراج الافتراضي  ·  محرك الصوت نشط",
            "no_category_sessions": "لا توجد جلسات في هذه الفئة",
            "audio_command_failed": "فشل أمر الصوت",
            "master_volume_updated": "تم تحديث مستوى الصوت الرئيسي",

            # Microphone section
            "mic_title": "🎙 استوديو الميكروفون",
            "mic_device": "جهاز الإدخال",
            "mic_volume": "مستوى الميكروفون",
            "mic_gain": "تعزيز الكسب",
            "mic_monitor": "مراقبة الصوت (اسمع نفسك)",
            "mic_monitor_output": "مخرج المراقبة",
            "mic_monitor_default": "المخرج الافتراضي للنظام",
            "mic_activate": "تشغيل",
            "mic_deactivate": "إيقاف",
            "mic_refresh": "تحديث",
            "mic_no_input": "لم يتم العثور على جهاز إدخال",
            "mic_status_live": "نشط",
            "mic_status_idle": "متوقف",
            "mic_status_muted": "مكتوم",
            "mic_status_error": "خطأ",
            "mic_status_listening": "جارٍ التقاط الصوت — تحدث لرؤية مستوى الإشارة.",
            "mic_status_hint": "شغّل الميكروفون لسماع صوتك مع المؤثرات.",

            # Voice effects section
            "fx_title": "✨ مؤثرات الصوت",
            "fx_enable": "تشغيل مؤثرات الصوت",
            "fx_preset": "النمط",
            "fx_intensity": "الشدة",
            "fx_wetdry": "مزيج المؤثر / الأصلي",
            "fx_reset": "إعادة تعيين",
            "fx_virtual_tag": "ميكروفون افتراضي",
            "fx_hint_virtual": "تم اكتشاف جهاز ميكروفون افتراضي — اضبط «مخرج المراقبة» عليه في استوديو الميكروفون، ثم اختره كميكروفون في الألعاب وDiscord وOBS وغيرها لاستخدام صوتك المحوّل في كل التطبيقات.",
            "fx_hint_no_virtual": "تعمل المعاينة والمراقبة مباشرة. لتغيير الصوت على مستوى النظام، ثبّت كابل صوت افتراضي مجاني (مثل VB-CABLE)، ثم اختره ضمن «مخرج المراقبة» وحدده كميكروفون في التطبيقات الأخرى.",
            # Voice effect presets
            "preset_clean": "طبيعي / نقي",
            "preset_deep": "صوت رجالي عميق",
            "preset_warm": "صوت ناعم / دافئ",
            "preset_bright": "صوت مشرق",
            "preset_radio": "مذيع راديو",
            "preset_telephone": "هاتف قديم",
            "preset_robot": "روبوت آلي",
            "preset_monster": "وحش / مظلم",
            "preset_tiny": "صوت رفيع / عالي",
            "preset_scifi": "خيال علمي فضائي",
            "preset_echo": "صدى متكرر",
            "preset_reverb": "تردد قاعة",
            "preset_helium": "صوت رفيع / هيليوم",
            "preset_alien": "فضائي",
            "preset_comic": "مضحك / كوميدي",
            "preset_villain": "شرير مظلم",

            # Smart Mic (Voice Cleanup) section
            "smart_mic_title": "🛡️ الميكروفون الذكي (تنقية الصوت)",
            "smart_mic_enable": "تفعيل تنقية الصوت",
            "smart_mic_profile": "نمط التنقية",
            "profile_natural": "طبيعي (خفيف)",
            "profile_clean": "نقي (متوازن)",
            "profile_isolation": "عزل قوي للضوضاء",
            "profile_studio": "استوديو إذاعي",
            "smart_mic_noise_gate": "بوابة كتم الضوضاء",
            "smart_mic_hum_filter": "مرشح طنين الكهرباء 50/60Hz",
            "smart_mic_speech_eq": "معادل وضوح مخارج الحروف",
            "smart_mic_compressor": "موازن ديناميكيات الصوت",
            "smart_mic_limiter": "مانع التشويش والقص الرقمي",
            "smart_mic_status_active": "التنقية نشطة (معالجة محلية بدون إنترنت)",

            # Mic Test / Recording section
            "test_title": "🎙️ اختبار ومقارنة الميكروفون",
            "test_record": "تسجيل اختبار (5 ثوان)",
            "test_stop": "إيقاف",
            "test_play_raw": "تشغيل الصوت الخام (قبل)",
            "test_play_proc": "تشغيل الصوت المعالج (بعد)",
            "test_save": "حفظ التسجيل...",
            "test_status_ready": "جاهز لتسجيل عينة صوتية للاختبار.",
            "test_status_recording": "جارٍ التسجيل... تحدث الآن في الميكروفون.",
            "test_status_recorded": "تم تسجيل عينة اختبار. اضغط تشغيل للمقارنة بين قبل وبعد.",
            "test_status_playing_raw": "جارٍ تشغيل الصوت الخام (بدون مؤثرات)...",
            "test_status_playing_proc": "جارٍ تشغيل الصوت المعالج (مع التنقية والمؤثرات)...",
            "test_saved_success": "تم حفظ التسجيل في {path}",

            
            # Categories
            "games": "🎮 ألعاب",
            "chat": "💬 دردشة",
            "music": "🎵 موسيقى/وسائط",
            "browsers": "🌐 متصفحات",
            "system": "🔊 نظام/أخرى",
            
            # Status messages
            "session_count": "عرض {count} جلسة(جلسات) نشطة",
            "master_volume_set": "مستوى الصوت الرئيسي: {percent}%",
            "session_muted": "مكتوم الصوت",
            "session_unmuted": "الصوت مشغل",
            "error_prefix": "خطأ: ",
            "failed_set_volume": "فشل تعيين مستوى الصوت لـ {name}",
            "failed_mute": "فشل كتم صوت {name}",
            
            # Application names
            "system_sounds": "أصوات النظام",
            "unknown_app": "تطبيق غير معروف",
            "file_explorer": "مستكشف الملفات",
        }
    }
    
    def __init__(self, language: Language = Language.ENGLISH):
        """Initialize translator with default language."""
        self.current_language = language
    
    def get(self, key: str, **kwargs) -> str:
        """
        Get translated string.
        
        Args:
            key: Translation key
            **kwargs: Format arguments (e.g., count=5, percent=80)
        
        Returns:
            Translated string, or key if not found
        """
        strings = self.STRINGS.get(self.current_language, self.STRINGS[Language.ENGLISH])
        text = strings.get(key, key)
        
        # Format with provided arguments
        if kwargs:
            try:
                return text.format(**kwargs)
            except KeyError:
                return text
        
        return text
    
    def set_language(self, language: Language) -> None:
        """Set current language."""
        self.current_language = language
    
    def get_language(self) -> Language:
        """Get current language."""
        return self.current_language
    
    def is_rtl(self) -> bool:
        """Check if current language is RTL (right-to-left)."""
        return self.current_language == Language.ARABIC


# Global translator instance
translator = Translations(Language.ENGLISH)


def t(key: str, **kwargs) -> str:
    """Convenience function for translation."""
    return translator.get(key, **kwargs)


def set_language(language: Language) -> None:
    """Set global language."""
    translator.set_language(language)


def get_language() -> Language:
    """Get current global language."""
    return translator.get_language()


def is_rtl() -> bool:
    """Check if current language is RTL."""
    return translator.is_rtl()
