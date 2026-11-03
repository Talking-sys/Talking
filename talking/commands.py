"""Local voice commands for the target language. No API calls.

Examples: «переведи на немецкий», «translate to Spanish».
A command is recognized only if the WHOLE utterance is a command (^…$).
"""
from __future__ import annotations

import re
import unicodedata

from .languages import WHISPER_LANGUAGES


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е").replace("ł", "l")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_STEMS: dict[str, list[str]] = {
    "en": ["english", "ingl", "anglais", "englisch", "английск", "англииск", "англиис", "англійськ", "англіиськ", "angielsk", "inglese"],
    "es": ["spanish", "espanol", "espanhol", "espagnol", "spagnol", "spanisch", "castellano",
           "испанск", "испанськ", "іспанськ", "hiszpansk"],
    "de": ["german", "deutsch", "aleman", "alemao", "allemand", "tedesc", "немецк", "німецьк", "niemieck"],
    "fr": ["french", "frances", "francais", "franzosisch", "francese", "французск", "французьк", "francusk"],
    "it": ["italian", "italien", "итальянск", "італіиськ", "італійськ", "wlosk"],
    "pt": ["portug", "португальск", "португальськ", "portugalsk"],
    "zh": ["chinese", "chino", "chinois", "chinesisch", "cinese", "chines", "mandarin",
           "китаиск", "китайськ", "китаиськ", "chinsk"],
    "ja": ["japanese", "japones", "japonais", "japanisch", "giappones", "японск", "японськ", "japonsk"],
    "uk": ["ukrain", "ucrani", "ucraino", "украинск", "украінськ", "українськ", "ukrainsk"],
    "pl": ["polish", "polaco", "polonais", "polnisch", "polacc", "polones", "польск", "польськ", "polsk"],
    "ru": ["russian", "ruso", "russe", "russisch", "russo", "русск", "російськ", "росіиськ", "rosyjsk"],
    "ko": ["korean", "coreano", "coreen", "koreanisch", "корейск", "кореиск", "корейськ", "koreansk"],
    "ar": ["arab", "арабск", "арабськ", "arabsk"],
    "tr": ["turkish", "turco", "turc", "turkisch", "турецк", "турецьк", "tureck"],
    "hu": ["hungarian", "hungaro", "hongrois", "ungarisch", "ungherese", "венгерск", "угорськ", "wegiersk", "magyar"],
    "nl": ["dutch", "holland", "neerland", "niederland", "olandes", "голландск", "нидерландск", "нідерландськ"],
    "he": ["hebrew", "hebreo", "hebreu", "hebraisch", "ebraico", "иврит"],
    "el": ["greek", "griego", "grec", "griechisch", "greco", "греческ", "грецьк"],
    "hi": ["hindi", "хинди", "гінді"],
    "fa": ["persian", "farsi", "персидск", "фарси"],
}
for _code, (_en, _ru) in WHISPER_LANGUAGES.items():
    lst = _STEMS.setdefault(_code, [])
    lst.append(_norm(_en))
    ru = _norm(_ru)
    lst.append(re.sub(r"(ии|ыи|ои|ий|ый|ой)$", "", ru) if len(ru) > 6 else ru)

_STEM_LIST = sorted({(st, c) for c, sts in _STEMS.items() for st in sts if st}, key=lambda x: -len(x[0]))


def language_from_word(word: str) -> str | None:
    w = _norm(word)
    if w in WHISPER_LANGUAGES and len(w) >= 2 and w not in ("no", "is", "it", "la", "en", "de"):
        return w
    for stem, code in _STEM_LIST:
        if len(stem) <= 3:
            if w == stem:
                return code
        elif w.startswith(stem):
            return code
    return None


_FILLER = (r"(?:пожалуиста|теперь|давай|давайте|ну|так|океи|ок|эи|слушай|please|now|ok|okay|hey|"
           r"por favor|ahora|bitte|jetzt|s il te plait|s il vous plait|maintenant|per favore|ora|"
           r"agora|por favor|будь ласка|тепер|prosze|teraz|grok|грок|все|всё|everything|it|this|todo|alles|tout|tutto|tudo|wszystko)")
_TRIGGER = (r"(?:переведи(?:те)?|переводи(?:те)?|перевод|переключи(?:сь|те)?|говори(?:те)?|озвучивай|"
            r"translate|switch|change(?: the)? language|speak|set(?: the)? (?:target )?language|"
            r"traduce|traduzca|traducir|traduceme|traducelo|cambia(?: el idioma)?|habla|"
            r"ubersetze|ubersetz|ubersetzen sie|wechsle|sprich|"
            r"traduis|traduisez|traduire|passe|parle|"
            r"traduci|tradurre|traduca|passa|parla|"
            r"traduza|traduz|traduzir|mude|fala|fale|"
            r"переклади(?:ть)?|перекладай|переключи(?:сь)?|говори|"
            r"przetlumacz|tlumacz|przelacz|mow)")
_PREP = (r"(?:на|na|in|into|to|al|a|en|ins|auf|nach|vers|para o|para a|para|pro|em|in lingua|"
         r"на язык|на мову|w|po)")
_LANGWORD = r"(?:язык|языке|language|idioma|sprache|langue|lingua|lingua|мову|мова|jezyk|jezyku|lang)"

_CMD_RE = re.compile(
    rf"^(?:{_FILLER}\s+)*{_TRIGGER}(?:\s+{_FILLER})*(?:\s+{_PREP})?(?:\s+(?:el|la|lo|le|l|das|die|der|il|o))?"
    rf"\s+(?P<lang>\w+)(?:\s+{_LANGWORD})?(?:\s+{_FILLER})*$")
_CMD_RE_LANGWORD_FIRST = re.compile(
    rf"^(?:{_FILLER}\s+)*(?:язык перевода|target language|idioma de destino|zielsprache|langue cible)"
    rf"\s+(?P<lang>\w+)(?:\s+{_FILLER})*$")

_ZH_NAMES = {"英": "en", "西班牙": "es", "俄": "ru", "德": "de", "法": "fr", "意大利": "it", "葡萄牙": "pt",
             "中": "zh", "汉": "zh", "漢": "zh", "日": "ja", "乌克兰": "uk", "烏克蘭": "uk", "波兰": "pl",
             "波蘭": "pl", "韩": "ko", "韓": "ko", "阿拉伯": "ar", "土耳其": "tr"}
_JA_NAMES = {"英": "en", "スペイン": "es", "ロシア": "ru", "ドイツ": "de", "フランス": "fr", "イタリア": "it",
             "ポルトガル": "pt", "中国": "zh", "日本": "ja", "ウクライナ": "uk", "ポーランド": "pl", "韓国": "ko"}
_ZH_RE = re.compile(r"^(?:请|請)?(?:帮我)?(?:翻译成|翻譯成|译成|譯成|翻成|换成|換成|改成)(?P<n>[\u4e00-\u9fff]{1,4}?)(?:语|語|文|话|話)?(?:吧)?$")
_JA_RE = re.compile(r"^(?P<n>[\u3040-\u30ff\u4e00-\u9fff]{1,6}?)(?:語)?(?:に|へ)(?:翻訳|訳)(?:して)?(?:ください|下さい)?$")


def parse_command(text: str) -> str | None:
    """Return a language code if the utterance is a target-language command, else None."""
    if not text:
        return None
    raw = re.sub(r"[\s。、，,.!！?？]+", "", text)
    if raw and re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", raw):
        m = _ZH_RE.match(raw)
        if m and m.group("n") in _ZH_NAMES:
            return _ZH_NAMES[m.group("n")]
        m = _JA_RE.match(raw)
        if m and m.group("n") in _JA_NAMES:
            return _JA_NAMES[m.group("n")]
        return None
    t = _norm(text)
    if not t or len(t.split()) > 8:
        return None
    for rx in (_CMD_RE, _CMD_RE_LANGWORD_FIRST):
        m = rx.match(t)
        if m:
            code = language_from_word(m.group("lang"))
            if code:
                return code
    return None
