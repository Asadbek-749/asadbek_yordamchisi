"""Inline keyboards and UI menus for Discipline system."""
from __future__ import annotations

from typing import Sequence
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from discipline.models import DisciplineHabit, DisciplineTask


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Bottom persistent keyboard with sections for easy app-like navigation."""
    keyboard = [
        [
            KeyboardButton(text="🎯 Intizom paneli"),
            KeyboardButton(text="📋 Bugungi reja"),
        ],
        [
            KeyboardButton(text="⏳ Fokus (Pomodoro)"),
            KeyboardButton(text="🔥 Odatlar"),
        ],
        [
            KeyboardButton(text="📥 Xabarlar"),
            KeyboardButton(text="🏆 Nargis ball"),
        ],
        [
            KeyboardButton(text="📊 Kunlik hisobot"),
            KeyboardButton(text="⚙️ Sozlamalar"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def main_discipline_keyboard() -> InlineKeyboardMarkup:
    """Main dashboard inline keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="📋 Bugungi reja", callback_data="disc:today"),
            InlineKeyboardButton(text="➕ Yangi vazifa", callback_data="disc:addtask"),
        ],
        [
            InlineKeyboardButton(text="⏳ Fokus taymer", callback_data="disc:focus"),
            InlineKeyboardButton(text="🔥 Odatlar", callback_data="disc:habits"),
        ],
        [
            InlineKeyboardButton(text="📊 Kunlik hisobot", callback_data="disc:report"),
            InlineKeyboardButton(text="📈 Haftalik tahlil", callback_data="disc:week"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="disc:settings"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def today_tasks_keyboard(tasks: Sequence[DisciplineTask]) -> InlineKeyboardMarkup:
    """Keyboard for today's tasks list."""
    buttons = []
    for task in tasks:
        if task.status != "completed":
            status_icon = "⚠️" if task.status == "missed" else "⏳"
            buttons.append([
                InlineKeyboardButton(
                    text=f"{status_icon} ✅ Bajarildi #{task.id}",
                    callback_data=f"task_done:{task.id}",
                ),
                InlineKeyboardButton(
                    text=f"❓ Sabab #{task.id}",
                    callback_data=f"task_reason:{task.id}",
                ),
            ])

    buttons.append([
        InlineKeyboardButton(text="➕ Yangi vazifa qo'shish", callback_data="disc:addtask"),
        InlineKeyboardButton(text="🔄 Yangilash", callback_data="disc:today"),
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="disc:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def focus_menu_keyboard(has_active: bool = False) -> InlineKeyboardMarkup:
    """Focus session control keyboard."""
    if has_active:
        buttons = [
            [
                InlineKeyboardButton(text="✅ Yakunlash", callback_data="focus:finish"),
                InlineKeyboardButton(text="🛑 Bekor qilish", callback_data="focus:cancel"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="disc:main"),
            ]
        ]
    else:
        buttons = [
            [
                InlineKeyboardButton(text="⚡ 10 daqiqa", callback_data="focus:start:10"),
                InlineKeyboardButton(text="⚡ 15 daqiqa", callback_data="focus:start:15"),
            ],
            [
                InlineKeyboardButton(text="🎯 25 daqiqa (Standart)", callback_data="focus:start:25"),
            ],
            [
                InlineKeyboardButton(text="🔥 45 daqiqa", callback_data="focus:start:45"),
                InlineKeyboardButton(text="🚀 60 daqiqa", callback_data="focus:start:60"),
            ],
            [
                InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="disc:main"),
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def habits_keyboard(habits_data: list[dict]) -> InlineKeyboardMarkup:
    """Checklist keyboard for habits."""
    buttons = []
    for h in habits_data:
        check = "✅" if h["completed_today"] else "⬜"
        streak = f"🔥 {h['streak']}" if h['streak'] > 0 else ""
        text = f"{check} {h['name']} {streak}"
        buttons.append([
            InlineKeyboardButton(text=text, callback_data=f"habit_toggle:{h['id']}")
        ])

    buttons.append([
        InlineKeyboardButton(text="➕ Yangi odat qo'shish", callback_data="disc:addhabit"),
        InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="disc:main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def reason_categories_keyboard(task_id: int) -> InlineKeyboardMarkup:
    """Reason categories for delayed/missed task."""
    categories = [
        ("⏰ Vaqt yetmadi", "time_shortage"),
        ("📱 Chalg'idim (Internet/Ijtimoiy tarmoq)", "distraction"),
        ("😴 Charchoq / Energiya yo'qligi", "fatigue"),
        ("🧩 Vazifa noaniq yoki juda katta", "unclear_task"),
        ("⚡ Kutilmagan favqulodda ish", "unexpected_event"),
        ("📉 Motivatsiya / Xohish yo'qligi", "low_motivation"),
        ("❓ Boshqa sabab", "other"),
    ]
    buttons = []
    for label, code in categories:
        buttons.append([
            InlineKeyboardButton(
                text=label, callback_data=f"reason_set:{task_id}:{code}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data="disc:today")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def priority_keyboard() -> InlineKeyboardMarkup:
    """Priority selection for new task."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Yuqori (High)", callback_data="task_prio:high"),
            InlineKeyboardButton(text="🟡 O'rta (Medium)", callback_data="task_prio:medium"),
            InlineKeyboardButton(text="🟢 Past (Low)", callback_data="task_prio:low"),
        ]
    ])


def category_keyboard() -> InlineKeyboardMarkup:
    """Category selection for new task."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💼 Ish", callback_data="task_cat:work"),
            InlineKeyboardButton(text="📚 O'qish", callback_data="task_cat:study"),
        ],
        [
            InlineKeyboardButton(text="🚀 Loyiha", callback_data="task_cat:project"),
            InlineKeyboardButton(text="🌱 Odat / Shaxsiy", callback_data="task_cat:habit"),
        ],
    ])


def skip_or_cancel_keyboard(allow_skip: bool = True) -> InlineKeyboardMarkup:
    """Helper skip/cancel row."""
    row = []
    if allow_skip:
        row.append(InlineKeyboardButton(text="⏭ O'tkazib yuborish", callback_data="step_skip"))
    row.append(InlineKeyboardButton(text="❌ Bekor qilish", callback_data="step_cancel"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def settings_keyboard(reminders_enabled: bool, sunday_mode: str) -> InlineKeyboardMarkup:
    """Settings toggle keyboard."""
    rem_text = "🔔 Eslatmalar: YOQILGAN" if reminders_enabled else "🔕 Eslatmalar: O'CHIRILGAN"
    rem_cb = "settings:toggle_reminders"

    sun_text = "🏖 Yakshanba: Dam olish" if sunday_mode == "rest" else "💼 Yakshanba: Ish rejimi"
    sun_cb = "settings:toggle_sunday"

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=rem_text, callback_data=rem_cb)],
        [InlineKeyboardButton(text=sun_text, callback_data=sun_cb)],
        [InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="disc:main")],
    ])
