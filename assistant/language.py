"""Language detection module for Uzbek, Russian, and English."""
from __future__ import annotations

import re
from typing import Optional

CYRILLIC_PATTERN = re.compile(r"[а-яА-ЯёЁўқғҳЎҚҒҲ]")
LATIN_PATTERN = re.compile(r"[a-zA-Z]")

UZBEK_CYRILLIC_SPECIAL = re.compile(r"[ўқғҳЎҚҒҲ]")

UZBEK_CYRILLIC_WORDS = {
    "салом",
    "рахмат",
    "раҳмат",
    "яхши",
    "яхшимисиз",
    "нима",
    "қалесан",
    "калесан",
    "йўқ",
    "йук",
    "бугун",
    "эртага",
    "керак",
    "ассалому",
    "алейкум",
    "алайкум",
    "хайр",
    "хоп",
    "майли",
    "қандай",
    "қандоқ",
    "нега",
    "қаерда",
    "қаердасан",
    "мен",
    "сен",
    "сиз",
    "биз",
    "ҳозир",
    "хозир",
    "кейин",
    "зўр",
    "зор",
    "бопти",
}

UZBEK_LATIN_WORDS = {
    "salom",
    "rahmat",
    "yaxshi",
    "nima",
    "qalesan",
    "qayerda",
    "bugun",
    "ertaga",
    "kerak",
    "xop",
    "bopti",
    "zor",
    "hozir",
    "keyin",
    "men",
    "sen",
    "siz",
    "biz",
    "assalomu",
    "alaykum",
    "aleykum",
    "mayli",
    "qanday",
    "qandoq",
    "nega",
    "qachon",
    "qanaqa",
    "bormisan",
    "tushundim",
    "rahmatli",
    "rahmatlar",
    "yaxshimisiz",
    "xush",
    "boshmisan",
    "uchrashamizmi",
    "qonday",
    "qandaysiz",
    "qalesiz",
    "qayerdasan",
    "qayerdasiz",
    "ishdami",
    "ishdaman",
    "aniq",
    "tinchlikmi",
    "ustoz",
    "gaplashamiz",
    "yozaman",
    "yozing",
    "ovqat",
    "tushlik",
    "dars",
    "vazifa",
    "ha",
    "yoq",
    "yo'q",
}

ENGLISH_WORDS = {
    "hi",
    "hello",
    "how",
    "are",
    "you",
    "free",
    "tonight",
    "see",
    "at",
    "clock",
    "the",
    "is",
    "it",
    "its",
    "my",
    "dog",
    "dogs",
    "ball",
    "what",
    "where",
    "when",
    "why",
    "who",
    "which",
    "can",
    "will",
    "would",
    "could",
    "should",
    "do",
    "did",
    "does",
    "have",
    "has",
    "had",
    "please",
    "thanks",
    "thank",
    "good",
    "morning",
    "evening",
    "night",
    "yes",
    "no",
    "ok",
    "okay",
    "sorry",
    "help",
    "today",
    "tomorrow",
    "time",
    "task",
    "project",
    "finance",
    "money",
    "report",
}

UZBEK_SUFFIXES = (
    "misan",
    "misiz",
    "mizmi",
    "yapsan",
    "yapman",
    "yapti",
    "yapmiz",
    "asizmi",
    "ganman",
    "dingmi",
    "daman",
    "dasan",
    "dami",
    "gach",
    "moqda",
    "gancha",
    "miz",
    "siz",
    "san",
    "man",
    "lar",
)

# Match Uzbek o'/g' with all apostrophe variants, excluding o'clock and possessive 's
APOSTROPHE_CHARS = "'ʻ’‘`"
UZBEK_OG_PATTERN = re.compile(
    rf"(?i)\b(?!o[{APOSTROPHE_CHARS}]clock\b)[a-z]*[og][{APOSTROPHE_CHARS}](?!s\b)[a-z]*\b"
)


def detect_language(text: str) -> Optional[str]:
    """Detect language hint: 'uz', 'ru', 'en', or None if no letters."""
    if not text:
        return None

    cyrillic_count = len(CYRILLIC_PATTERN.findall(text))
    latin_count = len(LATIN_PATTERN.findall(text))

    if cyrillic_count == 0 and latin_count == 0:
        return None

    # Cyrillic majority
    if cyrillic_count > latin_count:
        if UZBEK_CYRILLIC_SPECIAL.search(text):
            return "uz"

        clean_text = re.sub(r"[^\w\s]", " ", text.lower())
        tokens = clean_text.split()
        for token in tokens:
            if token in UZBEK_CYRILLIC_WORDS:
                return "uz"
        return "ru"

    # Latin majority or equal
    # 1. Check for Uzbek o' or g'
    if UZBEK_OG_PATTERN.search(text):
        return "uz"

    # Normalize apostrophes to empty string to test normalized words
    text_norm = re.sub(rf"[{APOSTROPHE_CHARS}]", "", text.lower())
    clean_latin = re.sub(r"[^a-z\s]", " ", text_norm)
    tokens = clean_latin.split()

    if not tokens:
        return None

    uz_score = 0
    en_score = 0

    for token in tokens:
        if token in UZBEK_LATIN_WORDS:
            uz_score += 2
        # Uzbek distinctive character 'q' not followed by 'u' (e.g. qonday, qalesan, aniq)
        if "q" in token and "qu" not in token:
            uz_score += 2
        # Uzbek distinctive 'x' not in common english words
        if "x" in token and token not in ENGLISH_WORDS:
            uz_score += 1
        for suffix in UZBEK_SUFFIXES:
            if len(token) > len(suffix) and token.endswith(suffix):
                uz_score += 1
                break

        if token in ENGLISH_WORDS:
            en_score += 2

    if uz_score > en_score:
        return "uz"
    if en_score > uz_score:
        return "en"

    # Tie breaker: for this Uzbek assistant, default fallback is Uzbek ('uz')
    return "uz"
