"""AI and rule-based structured extraction for financial transactions.

Extracts income and expenses from Uzbek text or voice transcripts into Decimal amounts,
standard categories, and transaction types.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

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
    transaction_date: date = field(default_factory=date.today)
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


def parse_uzbek_amount(text: str) -> Optional[Decimal]:
    """Parse Uzbek numeric text like '45 ming', '85 mingga', '2 million', '50k', '150 000' into Decimal."""
    clean = text.lower().replace(",", ".").strip()

    # Pattern: 2.5 million / 2 million / 2 mln / 2 millionga / 2 mlnlik
    m_mil = re.search(r"(\d+(?:\.\d+)?)\s*(?:mln|million)(?:ga|da|dan|cha|lik|ni)?\b", clean)
    if m_mil:
        return Decimal(m_mil.group(1)) * Decimal("1000000")

    # Pattern: 45 ming / 45k / 45 тыс / 45 mingga / 45 minglik / 45k ga
    m_ming = re.search(r"(\d+(?:\.\d+)?)\s*(?:ming|k|тыс)(?:ga|da|dan|cha|lik|ni)?\b", clean)
    if m_ming:
        return Decimal(m_ming.group(1)) * Decimal("1000")

    # Pattern: 150 000 / 150000 / 150.000 / 8000 so'm / 8000 so'mga
    m_som = re.search(r"(\d{1,3}(?:[\s\.]\d{3})+|\d{3,})\s*(?:so['`]?m|sum|usd|\$|dollar)(?:ga|da|dan|cha|lik|ni)?\b", clean)
    if m_som:
        raw_num = m_som.group(1).replace(" ", "").replace(".", "")
        return Decimal(raw_num)

    # Pattern: raw formatted digits: 150 000 / 150000 / 150.000
    m_digits = re.search(r"(\d{1,3}(?:[\s\.]\d{3})+|\d{4,})", clean)
    if m_digits:
        raw_num = m_digits.group(1).replace(" ", "").replace(".", "")
        return Decimal(raw_num)

    return None


class FinanceExtractor:
    """Extracts financial transactions from text or voice messages."""

    def __init__(self, ai_generator: Optional[Any] = None) -> None:
        self.ai = ai_generator

    def _match_category(self, text: str, tx_type: str) -> str:
        """Find best matching category from keywords with smart Uzbek stemming and domain priority."""
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
        return "OTHER"

    def rule_based_extract(self, text: str) -> FinanceExtractionResult:
        """Rule-based regex parser for common Uzbek financial statements."""
        clean = text.strip()
        lower = clean.lower()

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

        # Multi-item split by comma, "va", "va yana", "hamda"
        parts = re.split(r"[,;]|\bva\b|\bhamda\b|\byana\b", clean, flags=re.IGNORECASE)
        transactions: List[ExtractedTransactionItem] = []

        # Explicit income triggers
        income_triggers = [
            "daromad", "oylik", "maosh", "avans", "zarplata", "tushum",
            "pul tushdi", "tushdi", "topdim", "keldi", "ishladim",
            "pul oldim", "oylik oldim", "maosh oldim", "avans oldim",
        ]
        is_income_overall = any(w in lower for w in income_triggers)

        # Detect transaction date (today vs yesterday)
        import datetime
        tx_date = date.today()
        if "kecha" in lower or "kechagi" in lower:
            tx_date = date.today() - datetime.timedelta(days=1)
        elif "oldingi kun" in lower or "avvalgi kun" in lower:
            tx_date = date.today() - datetime.timedelta(days=2)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            amount = parse_uzbek_amount(part)
            if amount is None or amount <= Decimal("0"):
                continue

            part_lower = part.lower()
            part_is_income = is_income_overall or any(w in part_lower for w in income_triggers)
            tx_type = "INCOME" if part_is_income else "EXPENSE"

            category = self._match_category(part_lower, tx_type)

            # Clean description
            desc = re.sub(r"\d+(?:\.\d+)?\s*(?:ming|mln|million|k|so['`]?m)?(?:ga|da|dan|cha|lik|ni)?", "", part, flags=re.IGNORECASE).strip()
            desc = re.sub(r"\b(kecha|bugun|ga|da|so['`]?m)\b", "", desc, flags=re.IGNORECASE).strip()
            desc = re.sub(r"\s+", " ", desc).strip(" ,.-")
            if not desc:
                desc = category.capitalize()

            transactions.append(
                ExtractedTransactionItem(
                    type=tx_type,
                    amount=amount,
                    category=category,
                    description=desc.capitalize(),
                    transaction_date=tx_date,
                )
            )

        if transactions:
            return FinanceExtractionResult(
                transactions=transactions,
                is_ambiguous=False,
                raw_text=clean,
            )

        return FinanceExtractionResult(transactions=[], is_ambiguous=False, raw_text=clean)

    async def extract(self, text: str) -> FinanceExtractionResult:
        """Extract transactions using rules first, and AI fallback if ambiguous or complex."""
        rule_res = self.rule_based_extract(text)
        if rule_res.transactions or rule_res.is_ambiguous:
            return rule_res

        # If rule couldn't extract but text contains financial keywords, try AI extraction if generator is present
        if self.ai is not None and any(w in text.lower() for w in ["so'm", "som", "ming", "million", "sarfladim", "ketdi", "daromad", "oldim"]):
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
            '    {"type": "EXPENSE|INCOME", "amount": 45000, "category": "FOOD", "description": "Tushlik"}\n'
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
                tx_type = item.get("type", "EXPENSE").upper()
                amt = Decimal(str(item.get("amount", 0)))
                if amt <= Decimal("0"):
                    continue
                cat = item.get("category", "OTHER").upper()
                desc = item.get("description") or cat
                items.append(
                    ExtractedTransactionItem(
                        type=tx_type,
                        amount=amt,
                        category=cat,
                        description=desc,
                        transaction_date=date.today(),
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
