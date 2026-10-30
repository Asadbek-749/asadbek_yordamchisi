"""AI and rule-based structured extraction for financial transactions.

Extracts income and expenses from Uzbek text or voice transcripts into Decimal amounts,
standard categories, and transaction types.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, List, Optional, Tuple

from finance.timeutil import today_local

logger = logging.getLogger(__name__)

# Category keywords mapping (Uzbek, Russian, English)
EXPENSE_KEYWORDS: dict[str, list[str]] = {
    "FOOD": ["ovqat", "tushlik", "kechki ovqat", "nonushta", "restoran", "kafe", "kofe", "coffee", "choy", "ichimlik", "non", "osh", "somsa", "shashlik", "choyxona", "fastfood", "doner", "burger", "lavash", "food", "yegulik", "obed", "uzhin", "zavtrak"],
    "TRANSPORT": ["taksi", "taxi", "yandex", "transport", "yo'l", "yol", "yo'lkira", "benzin", "yoqilg'i", "meto", "metro", "avtobus", "marshrutka", "proyezd"],
    "HOME": ["ijara", "kvartira", "arenda", "uy", "remont", "mebel"],
    "UTILITIES": ["kommunal", "gaz", "svet", "elektr", "suv", "musor", "chiqindi"],
    "INTERNET": ["internet", "wifi", "vayfay", "aloqa", "tarif", "megabayt", "mb", "beeline", "ucell", "mobiuz", "uztelecom"],
    "SHOPPING": ["xarid", "bozor", "supermarket", "korzinka", "makro", "magazin", "do'kon", "dokon", "shopping", "bazar"],
    "CLOTHING": ["kiyim", "shim", "ko'ylak", "koylak", "oyoq kiyim", "poyabzal", "krossovka", "kurtka"],
    "TECHNOLOGY": ["texnika", "noutbuk", "laptop", "kompyuter", "telefon", "naushnik", "ayfon", "iphone", "gadjet"],
    "EDUCATION": ["ta'lim", "talim", "kurs", "dars", "kitob", "universitet", "kontrakt", "ucheba", "student"],
    "WORK": ["ish", "ofis", "xizmat safari", "kantselyariya"],
    "ENTERTAINMENT": ["ko'ngilochar", "kongilochar", "kino", "teatr", "o'yin", "game", "steam", "konsert", "otrix", "dam olish"],
    "HEALTH": ["sog'liq", "sogliq", "dori", "apteka", "doktor", "vrach", "shifoxona", "klinika", "lechenie"],
    "FAMILY": ["oila", "ota-ona", "bolalar", "xotin", "aka", "uka", "opa", "singil"],
    "DEBT": ["qarz", "kredit", "ipoteka", "dolgi"],
    "SAVINGS": ["jamg'arma", "kopilka", "investitsiya", "omonat", "depozit"],
    "OTHER": ["boshqa", "rasxod", "xarajat"],
}

INCOME_KEYWORDS: dict[str, list[str]] = {
    "MAIN_JOB": ["oylik", "maosh", "avans", "zarplata", "asoliy ish"],
    "TEACHING": ["dars berish", "o'qitish", "aipply", "kursdan", "o'quvchi", "talaba", "repetitorlik"],
    "AI_SERVICES": ["ai xizmat", "ai agent", "sun'iy intellekt", "bot tayyorlash", "bot", "avtomatlashtirish"],
    "FREELANCE": ["freelance", "frilans", "zakaz", "buyurtma", "loyiha", "proyekt", "mijoz"],
    "BUSINESS": ["biznes", "savdo", "foyda", "tushum", "daromad"],
    "BONUS": ["bonus", "mukofot", "sovg'a", "premiya"],
    "OTHER": ["daromad", "pul keldi", "tushdi", "boshqa"],
}


@dataclass
class ExtractedTransactionItem:
    """Single extracted transaction."""
    type: str  # "INCOME" or "EXPENSE"
    amount: Decimal
    category: str
    description: Optional[str] = None
    currency: str = "UZS"
    transaction_date: date = field(default_factory=today_local)
    confidence: float = 1.0


@dataclass
class FinanceExtractionResult:
    """Result of parsing text or voice transcript for transactions."""
    transactions: List[ExtractedTransactionItem] = field(default_factory=list)
    is_ambiguous: bool = False
    clarification_question: Optional[str] = None
    raw_text: str = ""

    @property
    def total_amount(self) -> Decimal:
        return sum((t.amount for t in self.transactions), Decimal("0.00"))


_MONTHS = "yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentyabr|oktyabr|noyabr|dekabr"
_CUR_UNIT = r"(?:so['`’]?m|sum|usd|\$|dollar\w*)"

# Numbers that look like money but are dates, years, times or phone numbers.
_NON_MONEY_PATTERNS = [
    re.compile(r"\+?\s*998[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b"),
    re.compile(r"\b998\d{9}\b"),
    re.compile(rf"\b9\d{{8}}\b(?!\s*{_CUR_UNIT})"),
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"),
    re.compile(r"\b\d{4}\s*-?\s*yil\w*"),
    re.compile(rf"\b\d{{1,2}}\s*-?\s*(?:{_MONTHS})\w*"),
    re.compile(r"\b\d{1,2}:\d{2}\b"),
    re.compile(r"\bsoat\s*\d{1,2}\b"),
]

_INCOME_WORDS = [
    "daromad", "oylik", "maosh", "avans", "zarplata", "tushum",
    "tushdi", "topdim", "keldi", "ishladim", "pul oldim",
]
_EXPENSE_WORDS = [
    "sarfladim", "ketdi", "ishlatdim", "to'ladim", "toladim", "to‘ladim",
    "xarajat", "sotib oldim", "berdim", "to'lov", "tolov",
]
_INCOME_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in _INCOME_WORDS) + r")\w*")
_EXPENSE_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in _EXPENSE_WORDS) + r")\w*")

_FINANCE_HINT_RE = re.compile(
    r"so['`’]?m\b|\bsum\b|\bming\b|\bmln\b|million|dollar|\busd\b|\$|\bpul\b|xarajat|daromad|"
    r"sarfla|ketdi|ishlatdim|to['`’‘]?ladim|toladim|oldim|berdim|tushdi|keldi|maosh|oylik|qarz|kredit"
)

# "Hozir kartamda 2 million 400 ming so'm pul bor": a balance report, not an expense or an income
_ACCOUNT_RE = re.compile(r"(?<!\w)(?:kartamda|kartada|kartamizda|hisobimda|hisobda|hamyonimda|hamyonda|cho['`’‘]?ntagimda)(?!\w)")
_HAVE_RE = re.compile(r"(?<!\w)(?:bor|qoldi|qolgan|turibdi|mavjud|qoldig['`’‘]?i)(?!\w)")
_MONEY_HAVE_RE = re.compile(r"(?<!\w)(?:pulim\s+(?:bor|qoldi)|pul\s+bor|balans\w*)(?!\w)")
BALANCE_QUESTION = (
    "Bu balans haqidagi xabar (hisobingizda qancha pul borligi), xarajat yoki daromad emas, shuning uchun yozib olmadim. "
    "Yozish uchun: «Tushlik 45 ming» yoki «Oylik 3 million oldim»."
)

_SPLIT_RE = re.compile(r";|,(?!\d)|(?<!\d),|\bva\b|\bhamda\b|\byana\b", re.IGNORECASE)

_MULTIPLIERS = {"ming": Decimal("1000"), "k": Decimal("1000"), "тыс": Decimal("1000"),
                "mln": Decimal("1000000"), "million": Decimal("1000000")}


def mask_non_money(text: str) -> str:
    """Blank out dates, years, clock times and phone numbers so they are never read as amounts."""
    for pattern in _NON_MONEY_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def find_amount(text: str) -> Optional[Tuple[Decimal, str, bool]]:
    """Find the money amount in text. Returns (amount, currency, has_unit) or None.

    has_unit is False for bare digits ("15000"), which callers should only trust in a money context.
    """
    clean = mask_non_money(text.lower()).replace(",", ".")

    # USD: "100 dollar", "25$", "$25", "3 ming usd"
    m_usd = re.search(r"(\d+(?:\.\d+)?)\s*(ming|k|mln|million)?\s*(?:\$|usd\b|dollar\w*)", clean)
    if not m_usd:
        m_usd = re.search(r"\$\s*(\d+(?:\.\d+)?)()", clean)
    if m_usd:
        mult = _MULTIPLIERS.get(m_usd.group(2) or "", Decimal("1"))
        return Decimal(m_usd.group(1)) * mult, "USD", True

    m_mil = re.search(r"(\d+(?:\.\d+)?)\s*(?:mln|million)(?:ga|da|dan|cha|lik|ni)?\b", clean)
    if m_mil:
        mantissa = Decimal(m_mil.group(1))
        if mantissa >= Decimal("10000"):  # "2400000 million" is a full number with a redundant unit
            return mantissa, "UZS", True
        total = mantissa * Decimal("1000000")
        # "2 million 400 ming" is 2 400 000: add the thousands that directly follow
        tail = re.match(r"\s*(\d{1,3})\s*(?:ming|k|тыс)(?:ga|da|dan|cha|lik|ni)?\b", clean[m_mil.end():])
        if tail:
            total += Decimal(tail.group(1)) * Decimal("1000")
        return total, "UZS", True

    m_ming = re.search(r"(\d+(?:\.\d+)?)\s*(?:ming|k|тыс)(?:ga|da|dan|cha|lik|ni)?\b", clean)
    if m_ming:
        mantissa = Decimal(m_ming.group(1))
        if mantissa >= Decimal("100000"):  # "2400000 ming": the number is already in so'm, the unit is redundant
            return mantissa, "UZS", True
        return mantissa * Decimal("1000"), "UZS", True

    m_som = re.search(r"(\d{1,3}(?:[\s\.]\d{3})+|\d{3,})\s*(?:so['`’]?m|sum)(?:ga|da|dan|cha|lik|ni)?\b", clean)
    if m_som:
        return Decimal(m_som.group(1).replace(" ", "").replace(".", "")), "UZS", True

    for m in re.finditer(r"(\d{1,3}(?:[\s\.]\d{3})+|\d{4,})", clean):
        raw = m.group(1)
        digits = raw.replace(" ", "").replace(".", "")
        grouped = digits != raw
        if not grouped and (1900 <= int(digits) <= 2100 or len(digits) >= 10):
            continue  # a year or an ID / phone, not money
        return Decimal(digits), "UZS", False

    return None


def parse_uzbek_amount(text: str) -> Optional[Decimal]:
    """Parse Uzbek numeric text like '45 ming', '85 mingga', '2 million', '50k', '150 000' into Decimal."""
    found = find_amount(text)
    return found[0] if found else None


class FinanceExtractor:
    """Extracts financial transactions from text or voice messages."""

    def __init__(self, ai_generator: Optional[Any] = None) -> None:
        self.ai = ai_generator

    def _find_category(self, text: str, tx_type: str) -> Optional[str]:
        """Best matching category from keywords (Uzbek stemming, domain priority), or None."""
        lower = text.lower()
        mapping = INCOME_KEYWORDS if tx_type == "INCOME" else EXPENSE_KEYWORDS

        category_order = (
            ["MAIN_JOB", "TEACHING", "AI_SERVICES", "FREELANCE", "BUSINESS", "BONUS", "OTHER"]
            if tx_type == "INCOME"
            else [
                "TRANSPORT",
                "FOOD",
                "EDUCATION",
                "HEALTH",
                "UTILITIES",
                "INTERNET",
                "CLOTHING",
                "TECHNOLOGY",
                "HOME",
                "SHOPPING",
                "ENTERTAINMENT",
                "FAMILY",
                "WORK",
                "DEBT",
                "SAVINGS",
                "OTHER",
            ]
        )

        for cat in category_order:
            keywords = mapping.get(cat, [])
            for kw in keywords:
                kw_clean = kw.strip().lower()
                if not kw_clean:
                    continue
                if len(kw_clean) <= 4:
                    pattern = rf"\b{re.escape(kw_clean)}(?:ga|da|dan|ni|ning|lar|m|miz|i|si|ka|ta)?\b"
                    if re.search(pattern, lower):
                        return cat
                else:
                    pattern = rf"\b{re.escape(kw_clean)}[a-z'`]*\b"
                    if re.search(pattern, lower):
                        return cat
        return None

    def _match_category(self, text: str, tx_type: str) -> str:
        return self._find_category(text, tx_type) or "OTHER"

    @staticmethod
    def _clean_description(text: str) -> str:
        desc = re.sub(
            r"\d+(?:\.\d+)?\s*(?:ming|mln|million|k|so['`]?m|usd|dollar\w*|\$)?(?:ga|da|dan|cha|lik|ni)?",
            "", text, flags=re.IGNORECASE,
        ).strip()
        desc = re.sub(r"\b(kecha|bugun|ga|da|so['`]?m)\b", "", desc, flags=re.IGNORECASE).strip()
        return re.sub(r"\s+", " ", desc).strip(" ,.-$")

    @staticmethod
    def _is_balance_statement(lower: str) -> bool:
        """True for "kartamda 500 ming bor" style messages that report a balance and spend/earn nothing."""
        if _EXPENSE_RE.search(lower) or _INCOME_RE.search(lower):
            return False
        return bool((_ACCOUNT_RE.search(lower) and _HAVE_RE.search(lower)) or _MONEY_HAVE_RE.search(lower))

    def rule_based_extract(self, text: str) -> FinanceExtractionResult:
        """Rule-based regex parser for common Uzbek financial statements.

        Income vs expense is decided for every part of the message separately; a part that only
        carries a meaning ("Oylik oldim") passes it to the next part that holds the amount.
        """
        clean = text.strip()
        lower = clean.lower()

        if self._is_balance_statement(lower):
            return FinanceExtractionResult(
                transactions=[], is_ambiguous=True, clarification_question=BALANCE_QUESTION, raw_text=clean
            )

        # Check for ambiguity (user mentioned spending/earning but gave no numbers)
        has_money_words = any(w in lower for w in ["pul", "sarfladim", "ketdi", "ishlatdim", "xarajat", "daromad", "oldim"])
        has_digits = bool(re.search(r"\d", lower))

        if has_money_words and not has_digits:
            cat = self._match_category(lower, "EXPENSE")
            cat_label = "ushbu xarajat" if cat == "OTHER" else cat.lower()
            return FinanceExtractionResult(
                transactions=[],
                is_ambiguous=True,
                clarification_question=f"{cat_label.capitalize()} uchun qancha summa sarflaganingizni aniq yozing yoki ayting (masalan: 45 ming so'm).",
                raw_text=clean,
            )

        tx_date = today_local()
        if "kecha" in lower or "kechagi" in lower:
            tx_date = tx_date - timedelta(days=1)
        elif "oldingi kun" in lower or "avvalgi kun" in lower:
            tx_date = tx_date - timedelta(days=2)

        transactions: List[ExtractedTransactionItem] = []
        pending: Optional[Tuple[str, str]] = None  # (type, text) of an amount-less part

        for part in _SPLIT_RE.split(clean):
            part = part.strip()
            if not part:
                continue
            part_lower = part.lower()
            income_sig = bool(_INCOME_RE.search(part_lower))
            expense_sig = bool(_EXPENSE_RE.search(part_lower))

            found = find_amount(part)
            if found is None:
                if expense_sig:
                    pending = ("EXPENSE", part_lower)
                elif income_sig:
                    pending = ("INCOME", part_lower)
                else:
                    pending = None
                continue

            amount, currency, has_unit = found
            if amount <= Decimal("0"):
                continue

            ctx_text = part_lower
            used_pending = False
            if expense_sig:
                tx_type = "EXPENSE"
            elif income_sig:
                tx_type = "INCOME"
            elif pending is not None:
                tx_type = pending[0]
                ctx_text = f"{pending[1]} {part_lower}"
                used_pending = True
            else:
                tx_type = "EXPENSE"
            pending_text = pending[1] if used_pending and pending else ""
            pending = None

            category = self._find_category(ctx_text, tx_type)
            if not has_unit and not (income_sig or expense_sig or used_pending or category):
                continue  # bare digits without any money context
            category = category or "OTHER"

            desc = self._clean_description(part) or self._clean_description(pending_text) or category.capitalize()

            transactions.append(
                ExtractedTransactionItem(
                    type=tx_type,
                    amount=amount,
                    category=category,
                    description=desc.capitalize(),
                    currency=currency,
                    transaction_date=tx_date,
                )
            )

        return FinanceExtractionResult(transactions=transactions, is_ambiguous=False, raw_text=clean)

    @staticmethod
    def looks_financial(text: str) -> bool:
        """Cheap gate: only talk about money to the AI when the message has a financial word."""
        return bool(_FINANCE_HINT_RE.search(text.lower()))

    async def extract(self, text: str) -> FinanceExtractionResult:
        """Extract transactions using rules first, and AI fallback if ambiguous or complex."""
        rule_res = self.rule_based_extract(text)
        if rule_res.transactions or rule_res.is_ambiguous:
            return rule_res

        # If rule couldn't extract but text contains financial keywords, try AI extraction if generator is present
        if self.ai is not None and self.looks_financial(text):
            try:
                ai_res = await self._ai_extract(text)
                if ai_res.transactions or ai_res.is_ambiguous:
                    return ai_res
            except Exception as e:
                logger.warning("AI finance extraction failed: %s", e)

        return rule_res

    async def _ai_extract(self, text: str) -> FinanceExtractionResult:
        """Invoke AI with strict JSON schema to extract complex multi-item financial statements."""
        prompt = (
            "You are a financial entity extraction engine for Nargis Personal Finance Manager.\n"
            "Analyze the text and extract all income or expense transactions in Uzbek sums (UZS).\n"
            "Rules:\n"
            "- Monetary amounts must be exact numbers (e.g. 45000, 2000000).\n"
            "- Available expense categories: FOOD, TRANSPORT, HOME, UTILITIES, INTERNET, SHOPPING, CLOTHING, TECHNOLOGY, EDUCATION, WORK, ENTERTAINMENT, HEALTH, FAMILY, DEBT, SAVINGS, OTHER.\n"
            "- Available income categories: MAIN_JOB, TEACHING, AI_SERVICES, FREELANCE, BUSINESS, BONUS, OTHER.\n"
            "- If the user mentions spending but specifies NO amount, set ambiguous=true and provide clarification_question in Uzbek.\n\n"
            "Respond ONLY with valid JSON (no markdown, no quotes around JSON):\n"
            "{\n"
            '  "transactions": [\n'
            '    {"type": "EXPENSE|INCOME", "amount": 45000, "category": "FOOD", "currency": "UZS|USD", "description": "Tushlik"}\n'
            "  ],\n"
            '  "ambiguous": false,\n'
            '  "clarification_question": null\n'
            "}\n\n"
            f"Input text:\n{text}"
        )
        raw = await self.ai.generate("You are an accurate financial entity extractor. Return only pure JSON.", prompt)
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        ambiguous = bool(data.get("ambiguous", False))
        clarification_question = data.get("clarification_question")

        items: List[ExtractedTransactionItem] = []
        for item in data.get("transactions", []):
            try:
                tx_type = str(item.get("type", "EXPENSE")).upper()
                if tx_type not in ("INCOME", "EXPENSE"):
                    continue
                amt = Decimal(str(item.get("amount", 0)))
                if amt <= Decimal("0") or amt > Decimal("100000000000"):
                    continue
                valid = INCOME_KEYWORDS if tx_type == "INCOME" else EXPENSE_KEYWORDS
                cat = str(item.get("category", "OTHER")).upper()
                if cat not in valid:
                    cat = "OTHER"
                currency = "USD" if str(item.get("currency", "UZS")).upper() == "USD" else "UZS"
                desc = item.get("description") or cat
                items.append(
                    ExtractedTransactionItem(
                        type=tx_type,
                        amount=amt,
                        category=cat,
                        description=desc,
                        currency=currency,
                        transaction_date=today_local(),
                    )
                )
            except Exception:
                continue

        return FinanceExtractionResult(
            transactions=items,
            is_ambiguous=ambiguous,
            clarification_question=clarification_question,
            raw_text=text,
        )
