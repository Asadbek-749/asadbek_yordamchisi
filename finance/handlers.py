"""Telegram bot router and handlers for Nargis Personal Finance Manager."""
from __future__ import annotations

import datetime
import io
import logging
import os
import tempfile
from decimal import Decimal
from typing import Any, Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from finance.extractor import FinanceExtractor
from finance.keyboards import (
    back_to_finance_keyboard,
    compound_pending_keyboard,
    main_finance_keyboard,
    pending_transaction_keyboard,
)
from finance.service import FinanceService

logger = logging.getLogger(__name__)


def build_finance_router(service: FinanceService, owner_id: int, ai_generator: Optional[Any] = None) -> Router:
    """Build and return aiogram router for the Personal Finance module."""
    router = Router(name="finance_router")
    is_owner = F.from_user.id == owner_id
    extractor = FinanceExtractor(ai_generator)

    # ─────────────────────────────────────────────────────────────────────────
    # Non-owner guard: any attempt by external users is strictly DENIED
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(
        ~is_owner,
        Command(
            "finance",
            "income",
            "expense",
            "addincome",
            "addexpense",
            "budget",
            "setbudget",
            "pendingfinance",
            "finance_today",
            "finance_week",
            "finance_month",
            "finance_report",
        ),
    )
    async def cmd_non_owner_finance(message: Message) -> None:
        await message.answer(
            "⛔ <b>Kirish taqiqlangan</b>\n\n"
            "Moliya moduli Asadbek ustozning shaxsiy ma'lumoti hisoblanadi. "
            "Ushbu ma'lumotlar tashqi foydalanuvchilarga ko'rsatilmaydi.",
            parse_mode="HTML",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # /finance — Asosiy moliya boshqaruv paneli
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("finance"))
    @router.message(is_owner, F.text == "💰 Moliya")
    async def cmd_finance(message: Message) -> None:
        summary = await service.get_daily_summary(owner_id)
        now = datetime.date.today()
        period_key = now.strftime("%Y-%m")
        budget_status = await service.get_budget_status(owner_id, period_key)

        income_str = service.format_money(summary["total_income"])
        expense_str = service.format_money(summary["total_expense"])
        net_str = service.format_money(summary["net_balance"])
        net_prefix = "+" if summary["net_balance"] >= 0 else ""

        budget_line = ""
        if budget_status:
            used_pct = budget_status["usage_percentage"]
            limit_str = service.format_money(budget_status["limit_amount"])
            budget_line = f"\n🎯 <b>Oylik byudjet:</b> {used_pct:.1f}% ishlatildi ({limit_str})"

        pending_warn = ""
        if summary["pending_count"] > 0:
            pending_warn = f"\n\n⏳ <b>Kutilayotgan (Pending):</b> {summary['pending_count']} ta tasdiqlanmagan yozuv"

        text = (
            f"💰 <b>NARGIS — SHAXSIY MOLIYA BOSHQARUVI</b>\n\n"
            f"📅 <b>Bugungi holat ({now.strftime('%d.%m.%Y')}):</b>\n"
            f"• 💰 Daromad: <b>{income_str}</b>\n"
            f"• 💸 Xarajat: <b>{expense_str}</b>\n"
            f"• 📈 Sof balans: <b>{net_prefix}{net_str}</b>"
            f"{budget_line}"
            f"{pending_warn}\n\n"
            f"<i>Xarajat yoki daromadni kiritish uchun shunchaki matn yoki ovozli xabar yozing!\n"
            f"Masalan: 'Bugun tushlikka 45 ming sarfladim'</i>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=main_finance_keyboard())

    # ─────────────────────────────────────────────────────────────────────────
    # /finance_today — Bugungi kunlik moliyaviy hisobot
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("finance_today"))
    @router.message(is_owner, Command("todayfinance"))
    async def cmd_finance_today(message: Message) -> None:
        summary = await service.get_daily_summary(owner_id)
        text = _format_daily_summary_text(summary, service)
        await message.answer(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    # ─────────────────────────────────────────────────────────────────────────
    # /finance_week — Haftalik moliyaviy hisobot
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("finance_week"))
    async def cmd_finance_week(message: Message) -> None:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=today.weekday())  # Monday
        summary = await service.get_weekly_summary(owner_id, start, today)
        text = _format_weekly_summary_text(summary, service)

        # Append Finance Intelligence week-over-week variance
        from finance.intelligence import FinanceIntelligenceEngine
        intel = FinanceIntelligenceEngine(owner_id)
        comp = await intel.compare_weekly_spending(today)
        var_msg = intel.generate_variance_message(comp)
        if var_msg:
            text += f"\n\n{var_msg}"

        await message.answer(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.message(is_owner, Command("finance_compare"))
    async def cmd_finance_compare(message: Message) -> None:
        from finance.intelligence import FinanceIntelligenceEngine
        intel = FinanceIntelligenceEngine(owner_id)
        comp = await intel.compare_weekly_spending()
        msg = intel.generate_variance_message(comp)
        if msg:
            await message.answer(msg, parse_mode="HTML", reply_markup=back_to_finance_keyboard())
        else:
            pct = comp["pct_change"]
            curr = service.format_money(comp["current_total"])
            prev = service.format_money(comp["previous_total"])
            await message.answer(
                f"📊 <b>Haftalik taqqoslash:</b>\n\n"
                f"• Bu hafta: <b>{curr}</b>\n"
                f"• O'tgan hafta: <b>{prev}</b>\n"
                f"• O'zgarish: <b>{pct}%</b>\n\n"
                f"Xarajatlarda keskin o'sish kuzatilmadi ✅",
                parse_mode="HTML",
                reply_markup=back_to_finance_keyboard(),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # /finance_month — Oylik moliyaviy hisobot
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("finance_month"))
    async def cmd_finance_month(message: Message) -> None:
        today = datetime.date.today()
        summary = await service.get_monthly_summary(owner_id, today.year, today.month)
        text = _format_monthly_summary_text(summary, service)
        await message.answer(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    # ─────────────────────────────────────────────────────────────────────────
    # /budget — Byudjet holati va /setbudget <summa>
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("setbudget"))
    async def cmd_set_budget(message: Message) -> None:
        args = message.text.split()[1:] if message.text else []
        if not args:
            await message.answer(
                "ℹ️ Byudjet belgilash formati:\n<code>/setbudget 4000000</code> yoki <code>/setbudget 4 million</code>",
                parse_mode="HTML",
            )
            return

        from finance.extractor import parse_uzbek_amount
        raw_amt = " ".join(args)
        amt = parse_uzbek_amount(raw_amt)
        if amt is None or amt <= Decimal("0"):
            await message.answer("❌ Noto'g'ri summa kiritildi. Iltimos, raqam bilan ko'rsating.")
            return

        period_key = datetime.date.today().strftime("%Y-%m")
        budget = await service.set_monthly_budget(owner_id, period_key, amt)
        await message.answer(
            f"✅ <b>Oylik byudjet muvaffaqiyatli belgilandi!</b>\n\n"
            f"📅 Davr: <b>{period_key}</b>\n"
            f"🎯 Limit: <b>{service.format_money(budget.limit_amount)}</b>",
            parse_mode="HTML",
            reply_markup=back_to_finance_keyboard(),
        )

    @router.message(is_owner, Command("budget"))
    async def cmd_budget_status(message: Message) -> None:
        period_key = datetime.date.today().strftime("%Y-%m")
        status = await service.get_budget_status(owner_id, period_key)
        if not status:
            await message.answer(
                f"ℹ️ <b>{period_key}</b> oyi uchun hali byudjet belgilanmagan.\n\n"
                f"Belgilash uchun: <code>/setbudget 4000000</code>",
                parse_mode="HTML",
                reply_markup=back_to_finance_keyboard(),
            )
            return

        limit = status["limit_amount"]
        spent = status["spent_amount"]
        rem = status["remaining_amount"]
        pct = status["usage_percentage"]
        icon = "🚨" if status["is_exceeded"] else ("⚠️" if pct >= 90 else "🎯")

        text = (
            f"{icon} <b>OYLIK BYUDJET TAHLILI ({period_key})</b>\n\n"
            f"🎯 <b>Limit:</b> {service.format_money(limit)}\n"
            f"💸 <b>Sarflandi:</b> {service.format_money(spent)}\n"
            f"💰 <b>Qoldi:</b> {service.format_money(rem)}\n"
            f"📊 <b>Ishlatildi:</b> <b>{pct:.1f}%</b>"
        )
        await message.answer(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    # ─────────────────────────────────────────────────────────────────────────
    # /pendingfinance — Tasdiqlanmagan tranzaksiyalar
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("pendingfinance"))
    async def cmd_pending_finance(message: Message) -> None:
        pending = await service.get_pending_transactions(owner_id)
        if not pending:
            await message.answer("✅ Hozirda kutilayotgan (Pending) moliyaviy yozuvlar yo'q.")
            return

        lines = [f"⏳ <b>Kutilayotgan tranzaksiyalar ({len(pending)} ta):</b>\n"]
        for p in pending:
            t_icon = "💰" if p.type == "INCOME" else "💸"
            amt_str = service.format_money(p.amount)
            lines.append(f"{t_icon} <b>#{p.id}</b> — {p.category_name or p.type}: <b>{amt_str}</b> ({p.description or ''})")

        lines.append("\nQuyidagi tugma orqali barchasini tasdiqlashingiz mumkin:")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=compound_pending_keyboard())

    # ─────────────────────────────────────────────────────────────────────────
    # Voice messages from owner (Speech-to-text -> Finance or Copilot)
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, F.voice)
    async def handle_owner_voice(message: Message) -> None:
        bot = message.bot
        voice = message.voice
        if not voice:
            return

        status_msg = await message.answer("🎙 <i>Ovozli xabar tinglanmoqda va tahlil qilinmoqda...</i>", parse_mode="HTML")

        try:
            file_info = await bot.get_file(voice.file_id)
            audio_io = io.BytesIO()
            await bot.download_file(file_info.file_path, audio_io)
            audio_bytes = audio_io.getvalue()
            transcription = ""

            # 1. Transcribe using Gemini multimodal audio capability
            if hasattr(ai_generator, "transcribe_audio"):
                transcription = await ai_generator.transcribe_audio(audio_bytes, mime_type="audio/ogg")
            else:
                gemini_key = os.environ.get("GEMINI_API_KEY")
                if gemini_key:
                    from assistant.gemini_client import GeminiClient
                    gc = GeminiClient(api_key=gemini_key)
                    transcription = await gc.transcribe_audio(audio_bytes, mime_type="audio/ogg")

            transcription = transcription.strip()
            if not transcription:
                await status_msg.edit_text(
                    "🎙 Ovozli xabarni eshitib bo'lmadi yoki unda nutq aniqlanmadi. "
                    "Iltimos, qayta yozib ko'ring yoki xabaringizni matn shaklida yuboring.",
                    parse_mode="HTML",
                )
                return

            # Display heard transcription
            await status_msg.edit_text(f"🎙 <b>Eshitildi:</b> <i>«{transcription}»</i>", parse_mode="HTML")

            # 1.5 Check if this is a dispatch command (send message to contact, confirm, cancel, status inquiry)
            try:
                from nargis.dispatcher import get_message_dispatcher
                dispatcher = get_message_dispatcher()
                dispatched = await dispatcher.handle_owner_message(
                    message=message,
                    text=transcription,
                    owner_id=owner_id,
                    bot=bot,
                    ai_generator=ai_generator,
                )
                if dispatched:
                    return
            except Exception as _disp_err:
                logger.warning("Nargis dispatcher error on voice: %s", _disp_err)

            # 2. Process via VoiceIntelligenceEngine (handles finance, tasks, plans, queries, status)
            from nargis.voice import VoiceIntelligenceEngine
            voice_engine = VoiceIntelligenceEngine(ai_generator=ai_generator, owner_id=owner_id)
            res = await voice_engine.process_voice_action(
                transcript=transcription,
                user_id=owner_id,
                bot=bot,
                source="voice",
            )
            resp_text = res.get("response_text") or "Ovozingiz qabul qilindi, Asadbek ustoz."
            markup = res.get("reply_markup")
            await message.answer(resp_text, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logger.error("Failed to process owner voice: %s", e, exc_info=True)
            await status_msg.edit_text(
                "⚠️ Ovozli xabarni tahlil qilishda texnik muammo yuz berdi. "
                "Iltimos, xarajat yoki xabaringizni matn ko'rinishida yozib yuboring.",
                parse_mode="HTML",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # CALLBACK QUERY HANDLERS (fin:confirm, fin:cancel, fin:main, etc.)
    # ─────────────────────────────────────────────────────────────────────────
    @router.callback_query(is_owner, F.data.startswith("fin:confirm:"))
    async def cb_confirm_transaction(callback: CallbackQuery) -> None:
        tx_id = int(callback.data.split(":")[-1])
        tx = await service.confirm_transaction(tx_id, actor_id=owner_id)
        if tx:
            amt_str = service.format_money(tx.amount)
            await callback.answer("✅ Hisobga qo'shildi!")
            await callback.message.edit_text(
                f"✅ <b>TRANZAKSIYA TASDIQLANDI VA QO'SHILDI</b>\n\n"
                f"📌 <b>Kategoriya:</b> {tx.category_name}\n"
                f"💰 <b>Summa:</b> {amt_str}\n"
                f"📅 <b>Sana:</b> {tx.transaction_date}\n"
                f"📝 <b>Izoh:</b> {tx.description or '—'}\n\n"
                f"<i>Muvaffaqiyatli hisobotlarga kiritildi.</i>",
                parse_mode="HTML",
            )
        else:
            await callback.answer("❌ Tranzaksiya topilmadi.")

    @router.callback_query(is_owner, F.data.startswith("fin:cancel:"))
    async def cb_cancel_transaction(callback: CallbackQuery) -> None:
        tx_id = int(callback.data.split(":")[-1])
        await service.cancel_transaction(tx_id, actor_id=owner_id)
        await callback.answer("❌ Bekor qilindi.")
        await callback.message.edit_text("❌ <b>Tranzaksiya bekor qilindi.</b>", parse_mode="HTML")

    @router.callback_query(is_owner, F.data == "fin:confirm_all")
    async def cb_confirm_all(callback: CallbackQuery) -> None:
        count = await service.confirm_all_pending(owner_id, actor_id=owner_id)
        await callback.answer(f"✅ Barcha ({count} ta) yozuv tasdiqlandi!")
        await callback.message.edit_text(
            f"✅ <b>Barcha kutilayotgan tranzaksiyalar ({count} ta) tasdiqlandi va hisobga qo'shildi!</b>",
            parse_mode="HTML",
        )

    @router.callback_query(is_owner, F.data == "fin:cancel_all")
    async def cb_cancel_all(callback: CallbackQuery) -> None:
        pending = await service.get_pending_transactions(owner_id)
        for p in pending:
            await service.cancel_transaction(p.id, actor_id=owner_id)
        await callback.answer("❌ Hammasi bekor qilindi.")
        await callback.message.edit_text("❌ <b>Barcha kutilayotgan yozuvlar bekor qilindi.</b>", parse_mode="HTML")

    @router.callback_query(is_owner, F.data == "fin:today")
    async def cb_fin_today(callback: CallbackQuery) -> None:
        summary = await service.get_daily_summary(owner_id)
        text = _format_daily_summary_text(summary, service)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:week")
    async def cb_fin_week(callback: CallbackQuery) -> None:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=today.weekday())
        summary = await service.get_weekly_summary(owner_id, start, today)
        text = _format_weekly_summary_text(summary, service)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:month")
    async def cb_fin_month(callback: CallbackQuery) -> None:
        today = datetime.date.today()
        summary = await service.get_monthly_summary(owner_id, today.year, today.month)
        text = _format_monthly_summary_text(summary, service)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:budget")
    async def cb_fin_budget(callback: CallbackQuery) -> None:
        period_key = datetime.date.today().strftime("%Y-%m")
        status = await service.get_budget_status(owner_id, period_key)
        if not status:
            await callback.message.edit_text(
                f"ℹ️ <b>{period_key}</b> oyi uchun hali byudjet belgilanmagan.\n\n"
                f"Belgilash uchun: <code>/setbudget 4000000</code>",
                parse_mode="HTML",
                reply_markup=back_to_finance_keyboard(),
            )
            return

        limit = status["limit_amount"]
        spent = status["spent_amount"]
        rem = status["remaining_amount"]
        pct = status["usage_percentage"]
        icon = "🚨" if status["is_exceeded"] else ("⚠️" if pct >= 90 else "🎯")
        text = (
            f"{icon} <b>OYLIK BYUDJET TAHLILI ({period_key})</b>\n\n"
            f"🎯 Limit: {service.format_money(limit)}\n"
            f"💸 Sarflandi: {service.format_money(spent)}\n"
            f"💰 Qoldi: {service.format_money(rem)}\n"
            f"📊 Ishlatildi: <b>{pct:.1f}%</b>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:pending")
    async def cb_fin_pending(callback: CallbackQuery) -> None:
        pending = await service.get_pending_transactions(owner_id)
        if not pending:
            await callback.message.edit_text(
                "✅ Hozirda kutilayotgan (Pending) moliyaviy yozuvlar yo'q.",
                reply_markup=back_to_finance_keyboard(),
            )
            return
        lines = [f"⏳ <b>Kutilayotgan tranzaksiyalar ({len(pending)} ta):</b>\n"]
        for p in pending:
            amt_str = service.format_money(p.amount)
            lines.append(f"• #{p.id} — {p.category_name}: <b>{amt_str}</b> ({p.description or '—'})")
        lines.append("\nQuyidagi tugma orqali barchasini tasdiqlashingiz mumkin:")
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=compound_pending_keyboard()
        )

    @router.callback_query(is_owner, F.data == "fin:main")
    async def cb_fin_main(callback: CallbackQuery) -> None:
        summary = await service.get_daily_summary(owner_id)
        now = datetime.date.today()
        income_str = service.format_money(summary["total_income"])
        expense_str = service.format_money(summary["total_expense"])
        net_str = service.format_money(summary["net_balance"])
        net_prefix = "+" if summary["net_balance"] >= 0 else ""
        text = (
            f"💰 <b>NARGIS — SHAXSIY MOLIYA BOSHQARUVI</b>\n\n"
            f"📅 <b>Bugungi holat ({now.strftime('%d.%m.%Y')}):</b>\n"
            f"• 💰 Daromad: <b>{income_str}</b>\n"
            f"• 💸 Xarajat: <b>{expense_str}</b>\n"
            f"• 📈 Sof balans: <b>{net_prefix}{net_str}</b>\n\n"
            f"<i>Kerakli bo'limni tanlang:</i>"
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=main_finance_keyboard())

    return router


async def _process_finance_text(
    message: Message,
    text: str,
    service: FinanceService,
    extractor: FinanceExtractor,
    owner_id: int,
    source: str = "text",
) -> None:
    """Helper to process extracted transactions from text and prompt owner for confirmation."""
    result = await extractor.extract(text)

    if result.is_ambiguous:
        question = result.clarification_question or "Qancha summa sarfladingiz yoki daromad oldingiz?"
        await message.answer(f"❓ {question}")
        return

    if not result.transactions:
        # Not a financial message
        return

    # Check for duplicate
    is_duplicate = False
    if len(result.transactions) == 1:
        tx_item = result.transactions[0]
        is_duplicate = await service.detect_duplicate(owner_id, tx_item.amount, tx_item.category)

    # Create pending transactions in database
    created = await service.create_pending_transactions(
        owner_id=owner_id,
        items=result.transactions,
        source=source,
        telegram_message_id=message.message_id,
    )

    dup_warning = "\n⚠️ <i>Diqqat: Ushbu yozuv oldingi kiritilgan xarajatga o'xshaydi.</i>\n" if is_duplicate else ""

    if len(created) == 1:
        tx = created[0]
        t_label = "YANGI DAROMAD" if tx.type == "INCOME" else "YANGI XARAJAT"
        t_icon = "💰" if tx.type == "INCOME" else "💸"
        amt_str = service.format_money(tx.amount)

        text_card = (
            f"📥 <b>{t_label}</b>{dup_warning}\n"
            f"{t_icon} <b>Turi:</b> {tx.type}\n"
            f"📌 <b>Kategoriya:</b> {tx.category_name}\n"
            f"💰 <b>Summa:</b> {amt_str}\n"
            f"📅 <b>Sana:</b> Bugun\n"
            f"📝 <b>Izoh:</b> {tx.description or '—'}\n\n"
            f"<b>Hisobga qo'shishni tasdiqlaysizmi?</b>"
        )
        await message.answer(text_card, parse_mode="HTML", reply_markup=pending_transaction_keyboard(tx.id))
    else:
        # Compound transactions
        lines = [f"📥 <b>YANGI TRANZAKSIYALAR ({len(created)} ta)</b>{dup_warning}\n"]
        total = Decimal("0.00")
        for i, tx in enumerate(created, 1):
            amt_str = service.format_money(tx.amount)
            total += tx.amount
            lines.append(f"{i}. {tx.category_name} — <b>{amt_str}</b> <i>({tx.description or ''})</i>")

        lines.append(f"\n<b>Jami summa:</b> <b>{service.format_money(total)}</b>")
        lines.append("\n<b>Hisobga qo'shishni tasdiqlaysizmi?</b>")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=compound_pending_keyboard())


def _format_daily_summary_text(summary: dict[str, Any], service: FinanceService) -> str:
    inc_str = service.format_money(summary["total_income"])
    exp_str = service.format_money(summary["total_expense"])
    net_str = service.format_money(summary["net_balance"])
    net_prefix = "+" if summary["net_balance"] >= 0 else ""
    t_date = summary["date"].strftime("%d.%m.%Y")

    lines = [
        f"📊 <b>BUGUNGI MOLIYAVIY HOLAT ({t_date})</b>\n",
        f"💰 <b>Daromad:</b> {inc_str}",
        f"💸 <b>Xarajat:</b> {exp_str}",
        f"📈 <b>Sof balans:</b> <b>{net_prefix}{net_str}</b>",
        f"\n━━━━━━━━━━━━━━━━━━━━",
        f"<b>XARAJATLAR:</b>",
    ]

    cats = summary.get("categories", {})
    if cats:
        for cat_name, amt in cats.items():
            lines.append(f"• {cat_name} — {service.format_money(amt)}")
    else:
        lines.append("<i>Bugun tasdiqlangan xarajatlar yo'q.</i>")

    return "\n".join(lines)


def _format_weekly_summary_text(summary: dict[str, Any], service: FinanceService) -> str:
    start_str = summary["start_date"].strftime("%d.%m")
    end_str = summary["end_date"].strftime("%d.%m.%Y")
    inc_str = service.format_money(summary["total_income"])
    exp_str = service.format_money(summary["total_expense"])
    net_str = service.format_money(summary["net_balance"])
    net_prefix = "+" if summary["net_balance"] >= 0 else ""

    lines = [
        f"📊 <b>HAFTALIK MOLIYAVIY HISOBOT</b>",
        f"📅 <i>{start_str} — {end_str}</i>\n",
        f"💰 <b>Jami daromad:</b> {inc_str}",
        f"💸 <b>Jami xarajat:</b> {exp_str}",
        f"📈 <b>Sof balans:</b> <b>{net_prefix}{net_str}</b>",
        f"\n━━━━━━━━━━━━━━━━━━━━",
        f"<b>KATEGORIYALAR BO'YICHA:</b>",
    ]

    cats = summary.get("categories", [])
    if cats:
        for cat_name, amt in cats:
            lines.append(f"• {cat_name}: {service.format_money(amt)}")
    else:
        lines.append("<i>Hafta davomida xarajatlar qayd etilmagan.</i>")

    top_cat = summary.get("top_category")
    if top_cat:
        lines.append(f"\n📌 <b>Eng ko'p xarajat kategoriyasi:</b> {top_cat[0]} ({service.format_money(top_cat[1])})")

    top_tx = summary.get("top_expense_tx")
    if top_tx:
        lines.append(f"🏆 <b>Eng katta xarajat:</b> {top_tx.category_name} — {service.format_money(top_tx.amount)}")

    return "\n".join(lines)


def _format_monthly_summary_text(summary: dict[str, Any], service: FinanceService) -> str:
    month_name = datetime.date(summary["year"], summary["month"], 1).strftime("%B %Y")
    inc_str = service.format_money(summary["total_income"])
    exp_str = service.format_money(summary["total_expense"])
    net_str = service.format_money(summary["net_balance"])
    net_prefix = "+" if summary["net_balance"] >= 0 else ""

    ratio_str = ""
    if summary["expense_ratio"] is not None:
        ratio_str = f"\n📊 <b>Xarajat / Daromad nisbati:</b> {summary['expense_ratio']:.1f}%"

    lines = [
        f"📊 <b>OYLIK MOLIYAVIY HISOBOT</b>",
        f"📅 <b>{month_name}</b>\n",
        f"💰 <b>Jami daromad:</b> {inc_str}",
        f"💸 <b>Jami xarajat:</b> {exp_str}",
        f"📈 <b>Sof balans:</b> <b>{net_prefix}{net_str}</b>"
        f"{ratio_str}",
        f"\n━━━━━━━━━━━━━━━━━━━━",
        f"<b>XARAJATLAR:</b>",
    ]

    exp_cats = summary.get("expense_categories", [])
    if exp_cats:
        for cat_name, amt in exp_cats:
            lines.append(f"• {cat_name}: {service.format_money(amt)}")
    else:
        lines.append("<i>Ushbu oyda xarajatlar yo'q.</i>")

    inc_cats = summary.get("income_sources", [])
    if inc_cats:
        lines.append(f"\n<b>DAROMAD MANBALARI:</b>")
        for src_name, amt in inc_cats:
            lines.append(f"• {src_name}: {service.format_money(amt)}")

    return "\n".join(lines)
