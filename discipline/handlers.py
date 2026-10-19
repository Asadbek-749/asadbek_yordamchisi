"""Telegram event handlers and router for the Discipline system."""
from __future__ import annotations

import datetime
import logging
from typing import Optional
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from discipline.keyboards import (
    category_keyboard,
    focus_menu_keyboard,
    habits_keyboard,
    main_discipline_keyboard,
    priority_keyboard,
    reason_categories_keyboard,
    settings_keyboard,
    skip_or_cancel_keyboard,
    today_tasks_keyboard,
)
from discipline.schemas import TaskCreate
from discipline.service import DisciplineService
from discipline.states import HabitCreationForm, ReasonDetailForm, TaskCreationForm

logger = logging.getLogger(__name__)


def build_discipline_router(service: DisciplineService, owner_id: int) -> Router:
    """Build and configure aiogram router for all discipline commands and callbacks."""
    router = Router(name="discipline_router")

    # Helper filter for owner
    is_owner = F.from_user.id == owner_id

    # ==================== MAIN DASHBOARD ====================
    @router.message(is_owner, Command("discipline"))
    @router.message(is_owner, F.text == "🎯 Intizom paneli")
    async def cmd_discipline(message: Message) -> None:
        text = (
            "🤖 <b>NARGIS — Shaxsiy Intizom va Hosildorlik Tizimi</b>\n\n"
            "Bu yerda siz o'z rejalaringizni, odatlaringizni va diqqatni "
            "boshqarishingiz mumkin. Kerakli bo'limni tanlang:"
        )
        await message.answer(text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")

    @router.callback_query(is_owner, F.data == "disc:main")
    async def cb_disc_main(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        text = (
            "🤖 <b>NARGIS — Shaxsiy Intizom va Hosildorlik Tizimi</b>\n\n"
            "Kerakli bo'limni tanlang:"
        )
        await callback.message.edit_text(text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")
        await callback.answer()

    # ==================== TODAY'S PLAN & TASKS ====================
    @router.message(is_owner, Command("today"))
    @router.message(is_owner, F.text == "📋 Bugungi reja")
    async def cmd_today(message: Message) -> None:
        await _show_today_plan(message, service, owner_id)

    @router.callback_query(is_owner, F.data == "disc:today")
    async def cb_disc_today(callback: CallbackQuery) -> None:
        await _show_today_plan(callback.message, service, owner_id, edit=True)
        await callback.answer()

    # ==================== ADD TASK WIZARD (FSM) ====================
    @router.message(is_owner, Command("addtask"))
    async def cmd_addtask(message: Message, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(TaskCreationForm.waiting_for_title)
        await message.answer(
            "📝 <b>Yangi vazifa yaratish:</b>\n\n"
            "1️⃣ Vazifa nomini yozing (masalan: <i>Backend API testlarini yozish</i>):",
            reply_markup=skip_or_cancel_keyboard(allow_skip=False),
            parse_mode="HTML",
        )

    @router.callback_query(is_owner, F.data == "disc:addtask")
    async def cb_disc_addtask(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await state.set_state(TaskCreationForm.waiting_for_title)
        await callback.message.edit_text(
            "📝 <b>Yangi vazifa yaratish:</b>\n\n"
            "1️⃣ Vazifa nomini yozing (masalan: <i>Backend API testlarini yozish</i>):",
            reply_markup=skip_or_cancel_keyboard(allow_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.message(is_owner, TaskCreationForm.waiting_for_title)
    async def process_task_title(message: Message, state: FSMContext) -> None:
        title = message.text.strip()
        if not title:
            await message.answer("Vazifa nomi bo'sh bo'lishi mumkin emas. Qayta kiriting:")
            return
        await state.update_data(title=title)
        await state.set_state(TaskCreationForm.waiting_for_expected_result)
        await message.answer(
            "2️⃣ <b>Kutilayotgan aniq natija</b> qanday? (yoki o'tkazib yuboring):",
            reply_markup=skip_or_cancel_keyboard(allow_skip=True),
            parse_mode="HTML",
        )

    @router.message(is_owner, TaskCreationForm.waiting_for_expected_result)
    async def process_expected_result(message: Message, state: FSMContext) -> None:
        await state.update_data(expected_result=message.text.strip())
        await _ask_priority(message, state)

    @router.callback_query(is_owner, TaskCreationForm.waiting_for_expected_result, F.data == "step_skip")
    async def skip_expected_result(callback: CallbackQuery, state: FSMContext) -> None:
        await _ask_priority(callback.message, state, edit=True)
        await callback.answer()

    async def _ask_priority(target: Message, state: FSMContext, edit: bool = False) -> None:
        await state.set_state(TaskCreationForm.waiting_for_priority)
        text = "3️⃣ <b>Ustuvorlik darajasi</b>ni tanlang:"
        if edit:
            await target.edit_text(text, reply_markup=priority_keyboard(), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=priority_keyboard(), parse_mode="HTML")

    @router.callback_query(is_owner, TaskCreationForm.waiting_for_priority, F.data.startswith("task_prio:"))
    async def process_priority(callback: CallbackQuery, state: FSMContext) -> None:
        prio = callback.data.split(":")[1]
        await state.update_data(priority=prio)
        await state.set_state(TaskCreationForm.waiting_for_duration)
        text = (
            "4️⃣ <b>Taxminiy davomiyligi</b> (daqiqalarda, masalan: <i>25</i>, <i>45</i>, <i>60</i>)? "
            "Yoki o'tkazib yuboring (standart: 30 daqiqa):"
        )
        await callback.message.edit_text(text, reply_markup=skip_or_cancel_keyboard(allow_skip=True), parse_mode="HTML")
        await callback.answer()

    @router.message(is_owner, TaskCreationForm.waiting_for_duration)
    async def process_duration(message: Message, state: FSMContext) -> None:
        try:
            minutes = int(message.text.strip())
            if minutes < 5 or minutes > 720:
                raise ValueError()
        except ValueError:
            await message.answer("Iltimos, 5 dan 720 gacha bo'lgan son kiriting:")
            return
        await state.update_data(estimated_minutes=minutes)
        await _ask_deadline(message, state)

    @router.callback_query(is_owner, TaskCreationForm.waiting_for_duration, F.data == "step_skip")
    async def skip_duration(callback: CallbackQuery, state: FSMContext) -> None:
        await state.update_data(estimated_minutes=30)
        await _ask_deadline(callback.message, state, edit=True)
        await callback.answer()

    async def _ask_deadline(target: Message, state: FSMContext, edit: bool = False) -> None:
        await state.set_state(TaskCreationForm.waiting_for_deadline)
        text = (
            "5️⃣ <b>Topshirish muddati (Deadline)</b>:\n"
            "Format: <code>HH:MM</code> (masalan <i>19:30</i>) yoki <code>YYYY-MM-DD HH:MM</code>.\n"
            "Yoki o'tkazib yuborish uchun tugmani bosing:"
        )
        if edit:
            await target.edit_text(text, reply_markup=skip_or_cancel_keyboard(allow_skip=True), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=skip_or_cancel_keyboard(allow_skip=True), parse_mode="HTML")

    @router.message(is_owner, TaskCreationForm.waiting_for_deadline)
    async def process_deadline(message: Message, state: FSMContext) -> None:
        text = message.text.strip()
        settings = await service.get_settings(owner_id)
        tz = ZoneInfo(settings.timezone)
        now_local = datetime.datetime.now(tz)

        parsed_dt: Optional[datetime.datetime] = None
        # Format HH:MM
        try:
            parts = text.split(":")
            if len(parts) == 2:
                hour = int(parts[0])
                minute = int(parts[1])
                local_dt = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if local_dt < now_local:
                    local_dt += datetime.timedelta(days=1)
                parsed_dt = local_dt.astimezone(datetime.timezone.utc)
        except Exception:
            pass

        # Format YYYY-MM-DD HH:MM
        if not parsed_dt:
            try:
                dt_naive = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
                parsed_dt = dt_naive.replace(tzinfo=tz).astimezone(datetime.timezone.utc)
            except Exception:
                pass

        if not parsed_dt:
            await message.answer(
                "❌ Vaqt formati noto'g'ri. Masalan: <code>18:00</code> yoki <code>2026-10-16 18:00</code>.\n"
                "Qaytadan kiriting yoki o'tkazib yuboring:",
                reply_markup=skip_or_cancel_keyboard(allow_skip=True),
                parse_mode="HTML",
            )
            return

        await state.update_data(deadline_iso=parsed_dt.isoformat())
        await _ask_category(message, state)

    @router.callback_query(is_owner, TaskCreationForm.waiting_for_deadline, F.data == "step_skip")
    async def skip_deadline(callback: CallbackQuery, state: FSMContext) -> None:
        await _ask_category(callback.message, state, edit=True)
        await callback.answer()

    async def _ask_category(target: Message, state: FSMContext, edit: bool = False) -> None:
        await state.set_state(TaskCreationForm.waiting_for_category)
        text = "6️⃣ <b>Vazifa turi</b>ni tanlang:"
        if edit:
            await target.edit_text(text, reply_markup=category_keyboard(), parse_mode="HTML")
        else:
            await target.answer(text, reply_markup=category_keyboard(), parse_mode="HTML")

    @router.callback_query(is_owner, TaskCreationForm.waiting_for_category, F.data.startswith("task_cat:"))
    async def process_category_and_save(callback: CallbackQuery, state: FSMContext) -> None:
        cat = callback.data.split(":")[1]
        data = await state.get_data()
        await state.clear()

        deadline = None
        if "deadline_iso" in data:
            deadline = datetime.datetime.fromisoformat(data["deadline_iso"])

        task_in = TaskCreate(
            title=data["title"],
            expected_result=data.get("expected_result"),
            priority=data.get("priority", "medium"),
            category=cat,
            estimated_minutes=data.get("estimated_minutes", 30),
            deadline=deadline,
        )
        task = await service.create_task(owner_id, task_in)

        await callback.message.edit_text(
            f"✅ <b>Vazifa muvaffaqiyatli saqlandi!</b> (#{task.id})\n\n"
            f"📌 <b>Nomi:</b> {task.title}\n"
            f"🎯 <b>Ustuvorlik:</b> {task.priority.upper()}\n"
            f"💼 <b>Kategoriya:</b> {task.category}\n"
            f"⏱ <b>Vaqt:</b> {task.estimated_minutes} daqiqa\n\n"
            f"Vazifalar ro'yxati: /today",
            reply_markup=main_discipline_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.callback_query(is_owner, F.data == "step_cancel")
    async def cancel_step(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        await callback.message.edit_text("❌ Amaliyot bekor qilindi.", reply_markup=main_discipline_keyboard())
        await callback.answer()

    # ==================== COMPLETE / DONE TASK ====================
    @router.message(is_owner, Command("done"))
    async def cmd_done(message: Message) -> None:
        parts = message.text.split()
        if len(parts) > 1 and parts[1].isdigit():
            task_id = int(parts[1])
            task = await service.complete_task(task_id, owner_id)
            if task:
                await message.answer(f"🎉 <b>Ajoyib!</b> #{task.id} '{task.title}' bajarildi deb belgilandi!", parse_mode="HTML")
            else:
                await message.answer("❌ Vazifa topilmadi yoki sizga tegishli emas.")
            return

        # Show pending tasks
        tasks = await service.list_active_tasks(owner_id)
        if not tasks:
            await message.answer("Hozirda barcha vazifalar bajarilgan! Yangi vazifa qo'shish uchun: /addtask")
            return

        text = "Qaysi vazifani yakunladingiz? Tugmani bosing yoki <code>/done ID</code> yozing:"
        await message.answer(text, reply_markup=today_tasks_keyboard(tasks), parse_mode="HTML")

    @router.callback_query(is_owner, F.data.startswith("task_done:"))
    async def cb_task_done(callback: CallbackQuery) -> None:
        task_id = int(callback.data.split(":")[1])
        task = await service.complete_task(task_id, owner_id)
        if task:
            await callback.answer(f"✅ Vazifa #{task.id} bajarildi!")
            await _show_today_plan(callback.message, service, owner_id, edit=True)
        else:
            await callback.answer("❌ Vazifa topilmadi.", show_alert=True)

    # ==================== ACCOUNTABILITY & REASONS ====================
    @router.callback_query(is_owner, F.data.startswith("task_reason:"))
    async def cb_task_reason(callback: CallbackQuery) -> None:
        task_id = int(callback.data.split(":")[1])
        task = await service.get_task(task_id, owner_id)
        if not task:
            await callback.answer("❌ Vazifa topilmadi.", show_alert=True)
            return

        text = (
            f"⚠️ <b>Vazifa kechikishi sababi</b>\n\n"
            f"Vazifa: <b>{task.title}</b>\n\n"
            f"Asadbek, vazifa nima sababdan kechikmoqda yoki bajarilmadi? "
            f"Haqiqiy sababni tanlang, shunda AI murabbiy aniq yechim taklif qiladi:"
        )
        await callback.message.edit_text(text, reply_markup=reason_categories_keyboard(task_id), parse_mode="HTML")
        await callback.answer()

    @router.callback_query(is_owner, F.data.startswith("reason_set:"))
    async def cb_reason_set(callback: CallbackQuery) -> None:
        _, task_id_str, category = callback.data.split(":")
        task_id = int(task_id_str)

        await callback.message.edit_text("⏳ AI Murabbiy vaziyatni tahlil qilmoqda...")

        log, ai_feedback = await service.record_task_delay(
            user_id=owner_id,
            task_id=task_id,
            reason_category=category,
        )

        reply_text = (
            f"📌 <b>Sabab qayd etildi:</b> <code>{category}</code>\n\n"
            f"🤖 <b>AI Intizom Murabbiyi:</b>\n"
            f"{ai_feedback}\n\n"
            f"Rejani davom ettirish uchun: /today"
        )
        await callback.message.edit_text(reply_text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")
        await callback.answer()

    # ==================== FOCUS TIMER ====================
    @router.message(is_owner, Command("focus"))
    @router.message(is_owner, F.text == "⏳ Fokus (Pomodoro)")
    async def cmd_focus(message: Message) -> None:
        await _show_focus_menu(message, service, owner_id)

    @router.callback_query(is_owner, F.data == "disc:focus")
    async def cb_disc_focus(callback: CallbackQuery) -> None:
        await _show_focus_menu(callback.message, service, owner_id, edit=True)
        await callback.answer()

    @router.callback_query(is_owner, F.data.startswith("focus:start:"))
    async def cb_focus_start(callback: CallbackQuery) -> None:
        minutes = int(callback.data.split(":")[2])
        session = await service.start_focus(owner_id, minutes)

        settings = await service.get_settings(owner_id)
        tz = ZoneInfo(settings.timezone)
        end_local = session.target_end_at.astimezone(tz).strftime("%H:%M")

        text = (
            f"🎯 <b>Fokus sessiyasi boshlandi!</b>\n\n"
            f"⏱ <b>Davomiyligi:</b> {minutes} daqiqa\n"
            f"🔔 <b>Yakunlanish vaqti:</b> {end_local}\n\n"
            f"<i>Barcha chalg'ituvchi omillarni chetga suring. Faqat bitta vazifaga diqqatni qarating.</i>"
        )
        await callback.message.edit_text(text, reply_markup=focus_menu_keyboard(has_active=True), parse_mode="HTML")
        await callback.answer("Fokus boshlandi! Muvaffaqiyat!")

    @router.callback_query(is_owner, F.data == "focus:finish")
    async def cb_focus_finish(callback: CallbackQuery) -> None:
        session = await service.finish_focus(owner_id)
        if session:
            await callback.message.edit_text(
                f"🎉 <b>Fokus muvaffaqiyatli yakunlandi!</b> ({session.duration_minutes} daqiqa yozildi)\n\n"
                f"Zo'r intizom! Yangi sessiya uchun: /focus",
                reply_markup=main_discipline_keyboard(),
                parse_mode="HTML",
            )
        else:
            await callback.answer("Faol fokus sessiyasi yo'q.", show_alert=True)

    @router.callback_query(is_owner, F.data == "focus:cancel")
    async def cb_focus_cancel(callback: CallbackQuery) -> None:
        session = await service.cancel_focus(owner_id)
        await callback.message.edit_text(
            "🛑 Fokus sessiyasi to'xtatildi.",
            reply_markup=main_discipline_keyboard(),
        )
        await callback.answer()

    # ==================== HABITS MODULE ====================
    @router.message(is_owner, Command("habits"))
    @router.message(is_owner, F.text == "🔥 Odatlar")
    async def cmd_habits(message: Message) -> None:
        await _show_habits(message, service, owner_id)

    @router.callback_query(is_owner, F.data == "disc:habits")
    async def cb_disc_habits(callback: CallbackQuery) -> None:
        await _show_habits(callback.message, service, owner_id, edit=True)
        await callback.answer()

    @router.callback_query(is_owner, F.data.startswith("habit_toggle:"))
    async def cb_habit_toggle(callback: CallbackQuery) -> None:
        habit_id = int(callback.data.split(":")[1])
        log, habit = await service.toggle_habit_today(habit_id, owner_id)
        status_word = "bajarildi ✅" if log.completed else "bekor qilindi ⬜"
        await callback.answer(f"Odat {status_word}!")
        await _show_habits(callback.message, service, owner_id, edit=True)

    @router.callback_query(is_owner, F.data == "disc:addhabit")
    async def cb_addhabit(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(HabitCreationForm.waiting_for_name)
        await callback.message.edit_text(
            "🌱 <b>Yangi odat yaratish:</b>\n\n"
            "Odat nomini kiriting (masalan: <i>20 daqiqa kitob o'qish</i> yoki <i>AI yangiliklarini o'rganish</i>):",
            reply_markup=skip_or_cancel_keyboard(allow_skip=False),
            parse_mode="HTML",
        )
        await callback.answer()

    @router.message(is_owner, HabitCreationForm.waiting_for_name)
    async def process_habit_name(message: Message, state: FSMContext) -> None:
        name = message.text.strip()
        await state.update_data(name=name)
        await state.set_state(HabitCreationForm.waiting_for_goal)
        await message.answer(
            "Ushbu odatdan maqsadingiz nima? (yoki o'tkazib yuboring):",
            reply_markup=skip_or_cancel_keyboard(allow_skip=True),
        )

    @router.message(is_owner, HabitCreationForm.waiting_for_goal)
    async def process_habit_goal(message: Message, state: FSMContext) -> None:
        goal = message.text.strip()
        data = await state.get_data()
        await state.clear()
        habit = await service.create_habit(owner_id, name=data["name"], goal=goal)
        await message.answer(
            f"✅ <b>Odat qo'shildi:</b> {habit.name}\n\nRo'yxat: /habits",
            reply_markup=main_discipline_keyboard(),
            parse_mode="HTML",
        )

    @router.callback_query(is_owner, HabitCreationForm.waiting_for_goal, F.data == "step_skip")
    async def skip_habit_goal(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        await state.clear()
        habit = await service.create_habit(owner_id, name=data["name"])
        await callback.message.edit_text(
            f"✅ <b>Odat qo'shildi:</b> {habit.name}\n\nRo'yxat: /habits",
            reply_markup=main_discipline_keyboard(),
            parse_mode="HTML",
        )
        await callback.answer()

    # ==================== REPORTS ====================
    @router.message(is_owner, Command("report"))
    @router.message(is_owner, F.text == "📊 Kunlik hisobot")
    async def cmd_report(message: Message) -> None:
        await _show_daily_report(message, service, owner_id)

    @router.callback_query(is_owner, F.data == "disc:report")
    async def cb_disc_report(callback: CallbackQuery) -> None:
        await _show_daily_report(callback.message, service, owner_id, edit=True)
        await callback.answer()

    @router.message(is_owner, Command("week"))
    async def cmd_week(message: Message) -> None:
        await _show_weekly_report(message, service, owner_id)

    @router.callback_query(is_owner, F.data == "disc:week")
    async def cb_disc_week(callback: CallbackQuery) -> None:
        await _show_weekly_report(callback.message, service, owner_id, edit=True)
        await callback.answer()

    # ==================== SETTINGS & TOGGLES ====================
    @router.message(is_owner, Command("settings"))
    @router.message(is_owner, F.text == "⚙️ Sozlamalar")
    async def cmd_settings(message: Message) -> None:
        await _show_settings(message, service, owner_id)

    @router.callback_query(is_owner, F.data == "disc:settings")
    async def cb_disc_settings(callback: CallbackQuery) -> None:
        await _show_settings(callback.message, service, owner_id, edit=True)
        await callback.answer()

    @router.message(is_owner, Command("discipline_off"))
    async def cmd_discipline_off(message: Message) -> None:
        await service.set_reminders_enabled(owner_id, False)
        await message.answer("🔕 Intizom eslatmalari vaqtincha O'CHIRILDI.\nQayta yoqish uchun: /discipline_on")

    @router.message(is_owner, Command("discipline_on"))
    async def cmd_discipline_on(message: Message) -> None:
        await service.set_reminders_enabled(owner_id, True)
        await message.answer("🔔 Intizom eslatmalari YOQILDI.\nBarcha bildirishnomalar o'z vaqtida yuboriladi.")

    @router.callback_query(is_owner, F.data == "settings:toggle_reminders")
    async def cb_toggle_reminders(callback: CallbackQuery) -> None:
        settings = await service.get_settings(owner_id)
        new_val = not settings.reminders_enabled
        await service.set_reminders_enabled(owner_id, new_val)
        await _show_settings(callback.message, service, owner_id, edit=True)
        await callback.answer("Eslatmalar holati o'zgartirildi!")

    @router.callback_query(is_owner, F.data == "settings:toggle_sunday")
    async def cb_toggle_sunday(callback: CallbackQuery) -> None:
        settings = await service.get_settings(owner_id)
        new_mode = "work" if settings.sunday_mode == "rest" else "rest"
        await service.update_settings(owner_id, sunday_mode=new_mode)
        await _show_settings(callback.message, service, owner_id, edit=True)
        await callback.answer(f"Yakshanba rejimi: {new_mode}")

    # ==================== NARGIS 2.0 BOSS COMMANDS ====================
    @router.message(is_owner, Command("postpone"))
    async def cmd_postpone(message: Message) -> None:
        """Strict postponement guard: 3+ delays require reason."""
        parts = message.text.split(maxsplit=2)
        if len(parts) > 1 and parts[1].isdigit():
            task_id = int(parts[1])
            reason = parts[2] if len(parts) > 2 else None
            from nargis.boss import AIBossEngine
            boss = AIBossEngine(owner_id)
            res = await boss.handle_postponement_request(task_id, reason=reason)
            await message.answer(res["message"], parse_mode="HTML")
            return
        await message.answer(
            "ℹ️ Vazifani ko'chirish uchun: <code>/postpone [task_id] [sabab]</code> yozing.\n"
            "<i>(Eslatma: 3 martadan ortiq ko'chirilgan vazifalar uchun sabab ko'rsatish shart!)</i>",
            parse_mode="HTML",
        )

    @router.message(is_owner, Command("goals"))
    async def cmd_goals(message: Message) -> None:
        """Display goals and milestones hierarchy."""
        from nargis.goals import GoalsEngine
        goals_engine = GoalsEngine(owner_id)
        summary = await goals_engine.get_active_goals_summary()
        await message.answer(summary, parse_mode="HTML")

    @router.message(is_owner, Command("memories"))
    async def cmd_memories(message: Message) -> None:
        """Display persistent memory context."""
        from nargis.memory import MemoryEngine
        mem_engine = MemoryEngine(owner_id)
        ctx = await mem_engine.get_relevant_context()
        if ctx:
            await message.answer(f"🧠 <b>Nargis Xotirasi:</b>\n\n<pre>{ctx}</pre>", parse_mode="HTML")
        else:
            await message.answer("🧠 Nargis xotirasida hali faol kontekst yozuvlari mavjud emas.", parse_mode="HTML")

    return router


# ==================== HELPER PRESENTERS ====================
async def _show_today_plan(target: Message, service: DisciplineService, owner_id: int, edit: bool = False) -> None:
    tasks = await service.list_today_tasks(owner_id)
    settings = await service.get_settings(owner_id)
    tz = ZoneInfo(settings.timezone)
    now_local = datetime.datetime.now(tz)

    lines = [
        f"📅 <b>Bugungi kun tartibi ({now_local.strftime('%d.%m.%Y')}):</b>\n",
        f"• 💼 Asosiy bandlik: {settings.work_start}–{settings.work_end} (ish & darslar)",
        f"• ⚡ Erkin fokus vaqti: {settings.free_time_start} dan boshlab\n",
        "<b>Vazifalar ro'yxati:</b>",
    ]

    if not tasks:
        lines.append("<i>Bugun uchun hali vazifa rejalashtirilmagan.</i>\n➕ /addtask orqali vazifa qo'shing.")
    else:
        for idx, t in enumerate(tasks, start=1):
            status_icon = "✅" if t.status == "completed" else ("⚠️ Kechikkan" if t.status == "missed" else "⏳")
            deadline_str = ""
            if t.deadline:
                local_dl = t.deadline.astimezone(tz)
                deadline_str = f" [Muddat: {local_dl.strftime('%H:%M')}]"
            lines.append(f"{idx}. {status_icon} <b>{t.title}</b> ({t.priority}){deadline_str}")

    text = "\n".join(lines)
    markup = today_tasks_keyboard(tasks)
    if edit:
        await target.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


async def _show_focus_menu(target: Message, service: DisciplineService, owner_id: int, edit: bool = False) -> None:
    active = await service.get_active_focus(owner_id)
    settings = await service.get_settings(owner_id)
    tz = ZoneInfo(settings.timezone)

    if active:
        end_str = active.target_end_at.astimezone(tz).strftime("%H:%M")
        text = (
            f"⏳ <b>Faol fokus sessiyasi davom etmoqda!</b>\n\n"
            f"⏱ Davomiyligi: {active.duration_minutes} daqiqa\n"
            f"🎯 Yakunlash vaqti: <b>{end_str}</b>\n\n"
            f"Sessiyani yakunlash yoki bekor qilish uchun tugmalardan foydalaning:"
        )
        markup = focus_menu_keyboard(has_active=True)
    else:
        text = (
            "⏳ <b>Fokus (Pomodoro) Sessiyasi</b>\n\n"
            "Chalg'imasdan diqqatni jamlash uchun kerakli vaqt oralig'ini tanlang:"
        )
        markup = focus_menu_keyboard(has_active=False)

    if edit:
        await target.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


async def _show_habits(target: Message, service: DisciplineService, owner_id: int, edit: bool = False) -> None:
    habits = await service.get_today_habit_status(owner_id)
    lines = ["🔥 <b>Kundalik Odatlar Nazorati:</b>\n"]
    if not habits:
        lines.append("<i>Hozircha odatlar qo'shilmagan.</i>")
    else:
        lines.append("Bugungi bajarilgan odatlarni belgilang (ustiga bosing):")

    text = "\n".join(lines)
    markup = habits_keyboard(habits)
    if edit:
        await target.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")


async def _show_daily_report(target: Message, service: DisciplineService, owner_id: int, edit: bool = False) -> None:
    metrics = await service.get_daily_metrics(owner_id)
    text = (
        f"📊 <b>Kunlik Intizom Hisoboti ({metrics.report_date.strftime('%d.%m.%Y')}):</b>\n\n"
        f"• Rejalashtirilgan vazifalar: <b>{metrics.planned_count}</b>\n"
        f"• Bajarildi: ✅ <b>{metrics.completed_count}</b> ({metrics.completion_rate_pct}%)\n"
        f"• Kechikkan/bajarilmagan: ⚠️ <b>{metrics.missed_count}</b>\n"
        f"• Fokus vaqti: ⏳ <b>{metrics.focus_minutes} daqiqa</b>\n"
        f"• Odatlar: 🔥 <b>{metrics.habits_completed}/{metrics.habits_total}</b>\n\n"
        f"<i>To'liq tahlil har kuni 21:00 da AI Murabbiy tomonidan taqdim etiladi.</i>"
    )
    if edit:
        await target.edit_text(text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")


async def _show_weekly_report(target: Message, service: DisciplineService, owner_id: int, edit: bool = False) -> None:
    stats, ai_review = await service.generate_weekly_report(owner_id)
    text = (
        f"📈 <b>Haftalik Intizom Tahlili:</b>\n\n"
        f"• Jami reja: <b>{stats.get('total_planned', 0)}</b> vazifa\n"
        f"• Bajarildi: ✅ <b>{stats.get('total_completed', 0)}</b> ({stats.get('completion_rate_pct', 0)}%)\n"
        f"• Qolib ketgan: ⚠️ <b>{stats.get('total_missed', 0)}</b>\n"
        f"• Fokus bloklari: ⏳ <b>{stats.get('total_focus_minutes', 0)} daqiqa</b>\n\n"
        f"🤖 <b>AI Murabbiy Tavsiyalari:</b>\n"
        f"{ai_review}"
    )
    if edit:
        await target.edit_text(text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=main_discipline_keyboard(), parse_mode="HTML")


async def _show_settings(target: Message, service: DisciplineService, owner_id: int, edit: bool = False) -> None:
    settings = await service.get_settings(owner_id)
    text = (
        f"⚙️ <b>Intizom Tizimi Sozlamalari:</b>\n\n"
        f"• Vaqt zonasi: <b>{settings.timezone}</b>\n"
        f"• Ertalabki reja vaqti: <b>{settings.morning_reminder_time}</b>\n"
        f"• Kechki hisobot vaqti: <b>{settings.evening_report_time}</b>\n"
        f"• Ish va dars jadvali: <b>{settings.work_start}–{settings.work_end}</b>\n"
        f"• Bo'sh vaqt boshlanishi: <b>{settings.free_time_start}</b>\n"
        f"• Eslatmalar holati: <b>{'Yoqilgan' if settings.reminders_enabled else 'O‘chirilgan'}</b>\n"
        f"• Yakshanba rejimi: <b>{settings.sunday_mode}</b>\n\n"
        f"Kerakli parametrni o'zgartirish uchun tugmalardan foydalaning:"
    )
    markup = settings_keyboard(settings.reminders_enabled, settings.sunday_mode)
    if edit:
        await target.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await target.answer(text, reply_markup=markup, parse_mode="HTML")
