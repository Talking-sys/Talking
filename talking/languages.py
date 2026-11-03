"""Whisper language codes. Recognition covers Whisper's ~100 languages."""
from __future__ import annotations

WHISPER_LANGUAGES: dict[str, tuple[str, str]] = {
    "af": ("Afrikaans", "африкаанс"), "am": ("Amharic", "амхарский"), "ar": ("Arabic", "арабский"),
    "as": ("Assamese", "ассамский"), "az": ("Azerbaijani", "азербайджанский"), "ba": ("Bashkir", "башкирский"),
    "be": ("Belarusian", "белорусский"), "bg": ("Bulgarian", "болгарский"), "bn": ("Bengali", "бенгальский"),
    "bo": ("Tibetan", "тибетский"), "br": ("Breton", "бретонский"), "bs": ("Bosnian", "боснийский"),
    "ca": ("Catalan", "каталанский"), "cs": ("Czech", "чешский"), "cy": ("Welsh", "валлийский"),
    "da": ("Danish", "датский"), "de": ("German", "немецкий"), "el": ("Greek", "греческий"),
    "en": ("English", "английский"), "es": ("Spanish", "испанский"), "et": ("Estonian", "эстонский"),
    "eu": ("Basque", "баскский"), "fa": ("Persian", "персидский"), "fi": ("Finnish", "финский"),
    "fo": ("Faroese", "фарерский"), "fr": ("French", "французский"), "gl": ("Galician", "галисийский"),
    "gu": ("Gujarati", "гуджарати"), "ha": ("Hausa", "хауса"), "haw": ("Hawaiian", "гавайский"),
    "he": ("Hebrew", "иврит"), "hi": ("Hindi", "хинди"), "hr": ("Croatian", "хорватский"),
    "ht": ("Haitian Creole", "гаитянский креольский"), "hu": ("Hungarian", "венгерский"),
    "hy": ("Armenian", "армянский"), "id": ("Indonesian", "индонезийский"), "is": ("Icelandic", "исландский"),
    "it": ("Italian", "итальянский"), "ja": ("Japanese", "японский"), "jw": ("Javanese", "яванский"),
    "ka": ("Georgian", "грузинский"), "kk": ("Kazakh", "казахский"), "km": ("Khmer", "кхмерский"),
    "kn": ("Kannada", "каннада"), "ko": ("Korean", "корейский"), "la": ("Latin", "латынь"),
    "lb": ("Luxembourgish", "люксембургский"), "ln": ("Lingala", "лингала"), "lo": ("Lao", "лаосский"),
    "lt": ("Lithuanian", "литовский"), "lv": ("Latvian", "латышский"), "mg": ("Malagasy", "малагасийский"),
    "mi": ("Maori", "маори"), "mk": ("Macedonian", "македонский"), "ml": ("Malayalam", "малаялам"),
    "mn": ("Mongolian", "монгольский"), "mr": ("Marathi", "маратхи"), "ms": ("Malay", "малайский"),
    "mt": ("Maltese", "мальтийский"), "my": ("Burmese", "бирманский"), "ne": ("Nepali", "непальский"),
    "nl": ("Dutch", "нидерландский"), "nn": ("Norwegian Nynorsk", "норвежский (нюнорск)"),
    "no": ("Norwegian", "норвежский"), "oc": ("Occitan", "окситанский"), "pa": ("Punjabi", "панджаби"),
    "pl": ("Polish", "польский"), "ps": ("Pashto", "пушту"), "pt": ("Portuguese", "португальский"),
    "ro": ("Romanian", "румынский"), "ru": ("Russian", "русский"), "sa": ("Sanskrit", "санскрит"),
    "sd": ("Sindhi", "синдхи"), "si": ("Sinhala", "сингальский"), "sk": ("Slovak", "словацкий"),
    "sl": ("Slovenian", "словенский"), "sn": ("Shona", "шона"), "so": ("Somali", "сомали"),
    "sq": ("Albanian", "албанский"), "sr": ("Serbian", "сербский"), "su": ("Sundanese", "сунданский"),
    "sv": ("Swedish", "шведский"), "sw": ("Swahili", "суахили"), "ta": ("Tamil", "тамильский"),
    "te": ("Telugu", "телугу"), "tg": ("Tajik", "таджикский"), "th": ("Thai", "тайский"),
    "tk": ("Turkmen", "туркменский"), "tl": ("Tagalog", "тагальский"), "tr": ("Turkish", "турецкий"),
    "tt": ("Tatar", "татарский"), "uk": ("Ukrainian", "украинский"), "ur": ("Urdu", "урду"),
    "uz": ("Uzbek", "узбекский"), "vi": ("Vietnamese", "вьетнамский"), "yi": ("Yiddish", "идиш"),
    "yo": ("Yoruba", "йоруба"), "zh": ("Chinese", "китайский"), "yue": ("Cantonese", "кантонский"),
}

_GOOGLE_CODE = {"he": "iw", "jw": "jw", "zh": "zh-CN", "yue": "zh-TW", "nn": "no", "ba": "ru"}
_GTTS_CODE = {"he": "iw", "jw": "jw", "zh": "zh-CN", "yue": "yue", "nn": "no"}
_EDGE_LOCALE_PREFIX = {"jw": "jv", "no": "nb", "nn": "nb", "yue": "zh-HK", "zh": "zh-CN", "fil": "fil", "tl": "fil"}

# Optional edge-tts voices (only used if the optional extra is installed).
DEFAULT_EDGE_VOICES = {
    "ru": "ru-RU-SvetlanaNeural", "en": "en-US-AriaNeural", "de": "de-DE-KatjaNeural",
    "fr": "fr-FR-DeniseNeural", "es": "es-ES-ElviraNeural", "it": "it-IT-ElsaNeural",
    "uk": "uk-UA-PolinaNeural", "zh": "zh-CN-XiaoxiaoNeural", "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural", "pt": "pt-BR-FranciscaNeural", "tr": "tr-TR-EmelNeural",
    "pl": "pl-PL-ZofiaNeural", "ar": "ar-SA-ZariyahNeural", "hi": "hi-IN-SwaraNeural",
}


def display_name(code: str) -> str:
    en, ru = WHISPER_LANGUAGES.get(code, (code, code))
    return f"{ru.capitalize()} / {en} ({code})"


def english_name(code: str) -> str:
    return WHISPER_LANGUAGES.get(code, (code, code))[0]


def google_code(code: str) -> str:
    return _GOOGLE_CODE.get(code, code)


def gtts_code(code: str) -> str:
    return _GTTS_CODE.get(code, code)


def edge_locale_prefix(code: str) -> str:
    return _EDGE_LOCALE_PREFIX.get(code, code)


def sorted_codes() -> list[str]:
    return sorted(WHISPER_LANGUAGES, key=lambda c: WHISPER_LANGUAGES[c][1])


def languages_payload() -> list[dict]:
    return [{"code": c, "en": WHISPER_LANGUAGES[c][0], "ru": WHISPER_LANGUAGES[c][1],
             "label": display_name(c)} for c in sorted_codes()]
