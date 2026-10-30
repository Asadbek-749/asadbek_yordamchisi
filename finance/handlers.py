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
    category_choice_keyboard,
    compound_pending_keyboard,
    edit_menu_keyboard,
    main_finance_keyboard,
    pending_transaction_keyboard,
)
from finance.service import FinanceService
from finance.timeutil import month_label, today_local

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
            "setrate",
            "export",
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
        now = today_local()
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
        today = today_local()
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
        msg = intel.generate_full_comparison_report(comp)
        await message.answer(msg, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    # ─────────────────────────────────────────────────────────────────────────
    # /finance_month — Oylik moliyaviy hisobot
    # ─────────────────────────────────────────────────────────────────────────
    @router.message(is_owner, Command("finance_month"))
    async def cmd_finance_month(message: Message) -> None:
        today = today_local()
        summary = await service.get_monthly_summary(owner_id, today.year, today.month)
        text = _format_monthly_summary_text(summary, service)
        text += await _usd_equivalent_note(service, owner_id, summary)
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

        period_key = today_local().strftime("%Y-%m")
        budget = await service.set_monthly_budget(owner_id, period_key, amt)
        await message.answer(
            f"✅ <b>Oylik byudjet muvaffaqiyatli belgilandi!</b>\n\n"
            f"📅 Davr: <b>{period_key}</b>\n"
            f"🎯 Limit: <b>{service.format_money(budget.limit_amount)}</b>",
            parse_mode="HTML",
            reply_markup=back_to_finance_keyboard(),
        )

    @router.message(is_owner, Command("export"))
    async def cmd_export(message: Message) -> None:
        args = message.text.split()[1:] if message.text else []
        today = today_local()
        year, month = today.year, today.month
        if args:
            try:
                parsed = datetime.datetime.strptime(args[0], "%Y-%m")
                year, month = parsed.year, parsed.month
            except ValueError:
                await message.answer("❌ Format: <code>/export</code> yoki <code>/export 2026-10</code>", parse_mode="HTML")
                return

        from aiogram.types import BufferedInputFile
        from finance.export import export_month
        data = await export_month(owner_id, year, month)
        await message.answer_document(
            BufferedInputFile(data, filename=f"nargis_moliya_{year}-{month:02d}.xlsx"),
            caption=f"📊 {month_label(year, month).capitalize()} — moliyaviy hisobot",
        )

    @router.message(is_owner, Command("setrate"))
    async def cmd_set_rate(message: Message) -> None:
        args = message.text.split()[1:] if message.text else []
        if not args:
            rate = await service.get_usd_rate(owner_id)
            current = f"Joriy kurs: <b>1 USD = {service.format_money(rate)}</b>\n\n" if rate else ""
            await message.answer(
                f"{current}Kursni belgilash: <code>/setrate 12650</code>",
                parse_mode="HTML",
            )
            return

        from finance.extractor import parse_uzbek_amount
        rate = parse_uzbek_amount(" ".join(args))
        if rate is None or not (Decimal("1000") <= rate <= Decimal("100000")):
            await message.answer("❌ Kurs noto'g'ri. 1 dollar necha so'm ekanini yozing, masalan: /setrate 12650")
            return
        await service.set_usd_rate(owner_id, rate)
        await message.answer(
            f"✅ Kurs saqlandi: <b>1 USD = {service.format_money(rate)}</b>",
            parse_mode="HTML",
        )

    @router.message(is_owner, Command("budget"))
    async def cmd_budget_status(message: Message) -> None:
        period_key = today_local().strftime("%Y-%m")
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
            amt_str = service.format_money(p.amount, p.currency)
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
            amt_str = service.format_money(tx.amount, tx.currency)
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
        today = today_local()
        start = today - datetime.timedelta(days=today.weekday())
        summary = await service.get_weekly_summary(owner_id, start, today)
        text = _format_weekly_summary_text(summary, service)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:month")
    async def cb_fin_month(callback: CallbackQuery) -> None:
        today = today_local()
        summary = await service.get_monthly_summary(owner_id, today.year, today.month)
        text = _format_monthly_summary_text(summary, service)
        text += await _usd_equivalent_note(service, owner_id, summary)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:budget")
    async def cb_fin_budget(callback: CallbackQuery) -> None:
        period_key = today_local().strftime("%Y-%m")
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
            amt_str = service.format_money(p.amount, p.currency)
            lines.append(f"• #{p.id} — {p.category_name}: <b>{amt_str}</b> ({p.description or '—'})")
        lines.append("\nQuyidagi tugma orqali barchasini tasdiqlashingiz mumkin:")
        await callback.message.edit_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=compound_pending_keyboard()
        )

    @router.callback_query(is_owner, F.data == "fin:main")
    async def cb_fin_main(callback: CallbackQuery) -> None:
        summary = await service.get_daily_summary(owner_id)
        now = today_local()
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

    # ─────────────────────────────────────────────────────────────────────────
    # ✏️ Tahrirlash: summa va kategoriyani bot ichida o'zgartirish
    # ─────────────────────────────────────────────────────────────────────────
    edit_state: dict[int, int] = {}  # owner_id -> transaction awaiting a new amount

    async def _show_card(callback: CallbackQuery, tx: Any, header: str) -> None:
        await callback.message.edit_text(
            _pending_card_text(service, tx, header),
            parse_mode="HTML",
            reply_markup=pending_transaction_keyboard(tx.id),
        )

    async def _editable(callback: CallbackQuery, tx_id: int) -> Optional[Any]:
        tx = await service.get_transaction(tx_id, owner_id)
        if tx is None or tx.status == "CANCELLED":
            await callback.answer("❌ Tranzaksiya topilmadi yoki bekor qilingan.", show_alert=True)
            return None
        return tx

    @router.callback_query(is_owner, F.data.startswith("fin:edit:"))
    async def cb_edit_menu(callback: CallbackQuery) -> None:
        tx = await _editable(callback, int(callback.data.split(":")[-1]))
        if tx is None:
            return
        edit_state.pop(owner_id, None)
        await callback.answer()
        await callback.message.edit_text(
            "✏️ <b>Nimani o'zgartiramiz?</b>", parse_mode="HTML", reply_markup=edit_menu_keyboard(tx.id)
        )

    @router.callback_query(is_owner, F.data.startswith("fin:editback:"))
    async def cb_edit_back(callback: CallbackQuery) -> None:
        tx = await _editable(callback, int(callback.data.split(":")[-1]))
        if tx is None:
            return
        edit_state.pop(owner_id, None)
        await callback.answer()
        await _show_card(callback, tx, "📥 <b>YOZUV</b>")

    @router.callback_query(is_owner, F.data.startswith("fin:editamt:"))
    async def cb_edit_amount(callback: CallbackQuery) -> None:
        tx = await _editable(callback, int(callback.data.split(":")[-1]))
        if tx is None:
            return
        edit_state[owner_id] = tx.id
        await callback.answer()
        await callback.message.edit_text(
            "💰 <b>Yangi summani yozing</b> (masalan: <code>50 ming</code> yoki <code>1 200 000</code>).\n"
            "Bekor qilish uchun: <code>bekor</code>",
            parse_mode="HTML",
        )

    @router.callback_query(is_owner, F.data.startswith("fin:editcat:"))
    async def cb_edit_category(callback: CallbackQuery) -> None:
        tx = await _editable(callback, int(callback.data.split(":")[-1]))
        if tx is None:
            return
        cats = await service.list_categories(tx.type)
        await callback.answer()
        await callback.message.edit_text(
            "📌 <b>Kategoriyani tanlang:</b>",
            parse_mode="HTML",
            reply_markup=category_choice_keyboard(tx.id, cats),
        )

    @router.callback_query(is_owner, F.data.startswith("fin:setcat:"))
    async def cb_set_category(callback: CallbackQuery) -> None:
        _, _, raw_id, cat_key = callback.data.split(":", 3)
        if await _editable(callback, int(raw_id)) is None:
            return
        tx = await service.edit_transaction(int(raw_id), actor_id=owner_id, new_category_key=cat_key)
        await callback.answer("✅ Kategoriya o'zgartirildi")
        await _show_card(callback, tx, "✏️ <b>YANGILANDI</b>")

    def _awaiting_new_amount(message: Message) -> bool:
        text = (message.text or "").strip()
        return owner_id in edit_state and bool(text) and not text.startswith("/")

    @router.message(is_owner, F.text, _awaiting_new_amount)
    async def msg_new_amount(message: Message) -> None:
        tx_id = edit_state[owner_id]
        text = message.text.strip()
        if text.lower() in ("bekor", "bekor qilish", "cancel"):
            edit_state.pop(owner_id, None)
            tx = await service.get_transaction(tx_id, owner_id)
            if tx is not None:
                await message.answer(
                    _pending_card_text(service, tx, "📥 <b>YOZUV</b>"),
                    parse_mode="HTML",
                    reply_markup=pending_transaction_keyboard(tx.id),
                )
            return

        from finance.extractor import find_amount
        found = find_amount(text)
        if found is None or found[0] <= Decimal("0"):
            await message.answer("❌ Summani tushunmadim. Masalan: <code>50 ming</code>. Bekor qilish: <code>bekor</code>", parse_mode="HTML")
            return

        edit_state.pop(owner_id, None)
        tx = await service.edit_transaction(tx_id, actor_id=owner_id, new_amount=found[0])
        if tx is None or tx.owner_id != owner_id:
            await message.answer("❌ Tranzaksiya topilmadi yoki bekor qilingan.")
            return
        await message.answer(
            _pending_card_text(service, tx, "✏️ <b>YANGILANDI</b>"),
            parse_mode="HTML",
            reply_markup=pending_transaction_keyboard(tx.id),
        )

    # Dashboard buttons that previously did nothing
    @router.callback_query(is_owner, F.data.in_({"fin:add_income", "fin:add_expense"}))
    async def cb_add_hint(callback: CallbackQuery) -> None:
        kind = "daromadni" if callback.data == "fin:add_income" else "xarajatni"
        example = "Oylik 3 million oldim" if callback.data == "fin:add_income" else "Tushlik 45 ming"
        await callback.answer()
        await callback.message.answer(
            f"✍️ {kind.capitalize()} oddiy xabar bilan yozing yoki ovoz yuboring.\nMasalan: <code>{example}</code>",
            parse_mode="HTML",
        )

    @router.callback_query(is_owner, F.data == "fin:analytics")
    async def cb_analytics(callback: CallbackQuery) -> None:
        from finance.intelligence import FinanceIntelligenceEngine
        intel = FinanceIntelligenceEngine(owner_id)
        report = intel.generate_full_comparison_report(await intel.compare_weekly_spending())
        await callback.answer()
        await callback.message.edit_text(report, parse_mode="HTML", reply_markup=back_to_finance_keyboard())

    @router.callback_query(is_owner, F.data == "fin:settings")
    async def cb_settings(callback: CallbackQuery) -> None:
        rate = await service.get_usd_rate(owner_id)
        rate_line = f"{service.format_money(rate)}" if rate else "belgilanmagan"
        await callback.answer()
        await callback.message.edit_text(
            "⚙️ <b>Moliya sozlamalari</b>\n\n"
            f"• Dollar kursi: <b>{rate_line}</b> — <code>/setrate 12650</code>\n"
            "• Oylik byudjet — <code>/setbudget 4 million</code>\n"
            "• Oylik hisobot Excelda — <code>/export</code>",
            parse_mode="HTML",
            reply_markup=back_to_finance_keyboard(),
        )

    return router


def _pending_card_text(service: FinanceService, tx: Any, header: str) -> str:
    t_icon = "💰" if tx.type == "INCOME" else "💸"
    return (
        f"{header}\n"
        f"{t_icon} <b>Turi:</b> {'Daromad' if tx.type == 'INCOME' else 'Xarajat'}\n"
        f"📌 <b>Kategoriya:</b> {tx.category_name}\n"
        f"💰 <b>Summa:</b> {service.format_money(tx.amount, tx.currency)}\n"
        f"📅 <b>Sana:</b> {tx.transaction_date.strftime('%d.%m.%Y')}\n"
        f"📝 <b>Izoh:</b> {tx.description or '—'}\n\n"
        f"<b>Hisobga qo'shishni tasdiqlaysizmi?</b>"
    )


async def _process_finance_text(
    message: Message,
    text: str,
    service: FinanceService,
    extractor: FinanceExtractor,
    owner_id: int,
    source: str = "text",
) -> bool:
    """Process extracted transactions from text and prompt owner for confirmation.

    Returns True when the message was handled as a finance message (card or question sent).
    """
    result = await extractor.extract(text)

    if result.is_ambiguous:
        question = result.clarification_question or "Qancha summa sarfladingiz yoki daromad oldingiz?"
        await message.answer(f"❓ {question}")
        return True

    if not result.transactions:
        # Not a financial message
        return False

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
        amt_str = service.format_money(tx.amount, tx.currency)

        text_card = (
            f"📥 <b>{t_label}</b>{dup_warning}\n"
            f"{t_icon} <b>Turi:</b> {tx.type}\n"
            f"📌 <b>Kategoriya:</b> {tx.category_name}\n"
            f"💰 <b>Summa:</b> {amt_str}\n"
            f"📅 <b>Sana:</b> {tx.transaction_date.strftime('%d.%m.%Y')}\n"
            f"📝 <b>Izoh:</b> {tx.description or '—'}\n\n"
            f"<b>Hisobga qo'shishni tasdiqlaysizmi?</b>"
        )
        await message.answer(text_card, parse_mode="HTML", reply_markup=pending_transaction_keyboard(tx.id))
    else:
        # Compound transactions
        lines = [f"📥 <b>YANGI TRANZAKSIYALAR ({len(created)} ta)</b>{dup_warning}\n"]
        totals: dict[str, Decimal] = {}
        for i, tx in enumerate(created, 1):
            amt_str = service.format_money(tx.amount, tx.currency)
            totals[tx.currency] = totals.get(tx.currency, Decimal("0.00")) + tx.amount
            lines.append(f"{i}. {tx.category_name} — <b>{amt_str}</b> <i>({tx.description or ''})</i>")

        total_str = " + ".join(service.format_money(v, c) for c, v in totals.items())
        lines.append(f"\n<b>Jami summa:</b> <b>{total_str}</b>")
        lines.append("\n<b>Hisobga qo'shishni tasdiqlaysizmi?</b>")
        await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=compound_pending_keyboard())
    return True


async def _usd_equivalent_note(service: FinanceService, owner_id: int, summary: dict[str, Any]) -> str:
    """Approximate so'm equivalent of USD amounts using the manually set rate (shown separately)."""
    usd = (summary.get("by_currency") or {}).get("USD")
    rate = await service.get_usd_rate(owner_id) if usd else None
    if not usd or not rate:
        return ""
    return (
        f"\n\n<i>Kurs {service.format_money(rate)} bo'yicha USD ≈ "
        f"daromad {service.format_money(usd['income'] * rate)}, "
        f"xarajat {service.format_money(usd['expense'] * rate)}</i>"
    )


def _other_currency_lines(summary: dict[str, Any], service: FinanceService) -> list[str]:
    """Separate lines for non-UZS currencies; they are never added into the so'm totals."""
    lines: list[str] = []
    for cur, vals in (summary.get("by_currency") or {}).items():
        if cur == "UZS":
            continue
        lines.append(
            f"💵 <b>{cur}:</b> daromad {service.format_money(vals['income'], cur)}, "
            f"xarajat {service.format_money(vals['expense'], cur)}"
        )
    return lines


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

    lines.extend(_other_currency_lines(summary, service))
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

    lines.extend(_other_currency_lines(summary, service))
    return "\n".join(lines)


def _format_monthly_summary_text(summary: dict[str, Any], service: FinanceService) -> str:
    month_name = month_label(summary["year"], summary["month"])
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

    lines.extend(_other_currency_lines(summary, service))

    return "\n".join(lines)
