# Audit — fix/audit-and-hardening

Boshlang'ich holat: `pytest` — 88 ta o'tdi. Yakuniy holat: 125 ta o'tdi.
"Aniq xato" = kod o'qib yoki yiqiladigan test bilan isbotlangan. Qolganlari "shubhali".

## Aniq xatolar

| # | Joyi (tuzatishdan oldin) | Nima noto'g'ri | Og'irligi | Tuzatish |
|---|---|---|---|---|
| 1 | `finance/extractor.py` (`is_income_overall`) | Xabarda bitta daromad so'zi bo'lsa hamma bo'laklar daromad bo'lardi | Yuqori | Har bo'lak alohida; summasiz bo'lak ma'nosi keyingisiga o'tadi |
| 2 | `finance/extractor.py` (`parse_uzbek_amount`) | Yil, sana (`11.2026` -> 11202), telefon, vaqt pul deb olinardi | Yuqori | `mask_non_money`; birliksiz raqam faqat pul kontekstida; 1900–2100 yil rad |
| 3 | `nargis/brain.py` `analyze_intent` | "Reja: ... 2026 ..." pul tahlilidan keyin tekshirilardi | O'rta | `Reja:`/`Maqsad:` prefikslari eng birinchi |
| 4 | `finance/*`, `intelligence.py` | `date.today()` serverda UTC: 00:00–05:00 xarajat kechagi kunga tushardi | Yuqori | `finance/timeutil.today_local()` (Asia/Tashkent) |
| 5 | `discipline/scheduler.py` | Eslatma `HH:MM` aniq tengligiga bog'liq: shu daqiqada restart bo'lsa kun yo'qoladi | Yuqori | `is_reminder_due`, 90 daqiqalik oyna; idempotentlik kaliti saqlanadi |
| 6 | `discipline/db.py` `init_db` | PostgreSQL'da `BOOLEAN DEFAULT 0` xato; xato butun tranzaksiyani bekor qiladi; `TIMESTAMP` (tz'siz) ustunlar tz'li modelga mos emas | Yuqori | Dialektga mos ALTER (`IF NOT EXISTS`, `FALSE`, `TIMESTAMP WITH TIME ZONE`), har biri SAVEPOINT'da |
| 7 | `extractor.py` | "dollar/usd/$" taniladi, lekin so'm deb saqlanardi | Yuqori | Valyuta saqlanadi; hisobotlarda UZS va USD alohida; `/setrate` |
| 8 | `nargis/voice.py` oylik so'rov | `get_monthly_summary(owner, date, date)` — `TypeError`, "bu oy qancha sarfladim" ishlamasdi | Yuqori | To'g'ri imzo; oy nomi o'zbekcha |
| 9 | `finance/service.py` `confirm_transaction` | Bekor qilingan yozuv eski tugma bilan qayta tasdiqlanardi | O'rta | CANCELLED qaytmaydi; takror bosish zararsiz |
| 10 | `finance/service.py` `detect_duplicate` | Kategoriya kalitini ("FOOD") ko'rsatiladigan nomga ("🍔 Ovqat") solishtirardi — hech qachon topmasdi | Past | Kalit bo'yicha ham solishtiriladi |
| 11 | `finance/intelligence.py` | Faqat CONFIRMED sanardi (EDITED tashlab ketilardi), valyutalarni aralashtirardi | O'rta | CONFIRMED+EDITED, faqat UZS |
| 12 | `finance/keyboards.py` | ✏️ Tahrirlash, ➕/➖, 📈 Tahlil, ⚙️ Sozlamalar tugmalarida handler yo'q edi | O'rta | Handlerlar qo'shildi |
| 13 | `finance/handlers.py` | Kartada sana doim "Bugun" (kecha yozilsa ham); aralash valyuta jami qo'shilardi | Past | Haqiqiy sana; valyuta bo'yicha jami |
| 14 | `.env.example`, README, kod | `gemini-1.5-flash` (eskirgan), `gemini-3.8-flash` ro'yxatda tasdiqsiz | O'rta | Rasmiy hujjat bo'yicha yangilandi (quyida) |

## Shubhali (isbotlanmagan yoki o'zgartirilmagan)

- `discipline/scheduler.py`: `record_reminder_delivered` jo'natishdan OLDIN yoziladi. Telegram vaqtincha xato bersa, o'sha kunning eslatmasi qayta yuborilmaydi. (Takror yubormaslik xavfsizroq deb tegilmadi.)
- `_check_morning_and_evening_schedules` `reminders_enabled=False` bo'lsa darhol qaytadi — doimiy to'lov eslatmasi va tungi zaxira ham o'chadi.
- PostgreSQL yo'li **haqiqiy bazada sinalmadi** (Docker daemon ishlamayapti). Faqat SQL matni dialekt bo'yicha unit-test qilindi.
- `nargis/dispatcher.py`, `discipline/handlers.py`, `nargis/boss.py` to'liq o'qilmadi (oqim bo'yicha faqat chaqiruv joylari ko'rildi).
- Gemini modellarining audio kiritishni (`transcribe_audio`) qo'llashi hujjatdan tasdiqlanmadi; rasmiy sahifada alohida `gemini-3.5-transcribe` modeli ham bor.
- `finance/extractor.py` ning "ming" qoidasi: "2 ming dollar" kabi birikmalar faqat `ming`+`dollar` tartibida tushuniladi.

## Gemini model nomlari (ai.google.dev/gemini-api/docs/models va /deprecations, 2026-10-28 holati)

Barqaror: `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` (to'xtatilishi eng erta 2027-05-07). `gemini-2.0-flash-lite` 2026-06-01 da o'chirilgan. `gemini-1.5-flash` sahifada tilga olinmagan (tasdiqlanmadi, ishlatilmaydi). Standart: `gemini-3.5-flash-lite`; zaxira: `gemini-3.6-flash`, `gemini-3.1-flash-lite`, `gemini-2.5-flash-lite`.
