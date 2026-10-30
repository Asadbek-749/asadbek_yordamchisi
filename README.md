# Telegram Business Personal Assistant

A smart, personal Telegram assistant powered by your **Claude Code CLI** subscription (`claude -p`). The bot connects directly to your personal Telegram account using Telegram Business Chatbots feature and drafts replies in your style based on your `profile.md`.

---

## What It Does

- Connects to your Telegram account via **Telegram Business ("Chatbots")**.
- Operates in **PREVIEW mode by default (`AUTO_SEND=false`)**: incoming messages are logged and generated replies are printed directly to the logs (`previews.jsonl`) without being sent to the user.
- Once you are happy with the generated responses, flip `AUTO_SEND=true` to have the bot reply automatically.
- **No hardcoded allowlist**: Telegram itself determines which chats the bot can access via *Settings → Telegram Business → Chatbots*.

---

## Behaviour Summary

| Scenario | Behaviour |
|---|---|
| **Foreign account connection** | Connection from any user other than `OWNER_ID` is rejected (`foreign_connection`). Claude is never invoked. |
| **Chat filtering** | Telegram's native Business settings control which chats the bot receives. The bot processes all business messages it receives. |
| **Owner sends message** | Saved to history as `owner`. Silences bot replies in that chat for `OWNER_TAKEOVER_MINUTES` (default 15m). |
| **Bot messages** | Messages from bots or via business bots are ignored (`ignored_bot`). Bot never replies to itself. |
| **Duplicate messages** | Tracked before any async call using `SeenMessages`. Duplicate updates are safely dropped (`duplicate`). |
| **Service messages / media** | Service messages are ignored (`unsupported`). Media (voice, stickers, photos, documents) are described semantically. |
| **`/pause` and `/resume`** | Owner can pause the bot via direct command. State persists across restarts in `state.json`. |
| **Usage limits / quota** | `ClaudeLimitError` sets cooldown until reset time or `LIMIT_COOLDOWN_MINUTES`. In auto-send mode, notifies owner. |
| **Timeouts and auth errors** | Logged cleanly without spamming Telegram chats. Auth errors advise running `claude setup-token`. |
| **No reply needed (`[SKIP]`)** | If Claude outputs `[SKIP]` or empty text, the message is ignored (`skipped`). |
| **Pre-send verification** | In auto-send mode, re-verifies `can_reply` directly with Telegram before sending. |
| **Owner commands** | `/pause`, `/resume`, `/status`, `/start` (in Uzbek with setup instructions). |
| **Direct non-owner messages** | `/start` explains that this is a private personal assistant. Other direct messages are silently ignored. |

---

## AI Backends: Google Gemini or Claude Code

The assistant supports two AI backends:
1. **Google Gemini (Recommended if you don't have Claude subscription)**:
   - Uses official Google Gemini API (default: `gemini-3.5-flash-lite`; fallbacks: `gemini-3.6-flash`, `gemini-3.1-flash-lite`, `gemini-2.5-flash-lite`).
   - Get a free key at [Google AI Studio](https://aistudio.google.com/).
   - Set in `.env`:
     ```env
     AI_PROVIDER=gemini
     GEMINI_API_KEY=AIzaSy...
     GEMINI_MODEL=gemini-3.5-flash-lite
     ```
2. **Claude Code CLI (`claude -p`)**:
   - Uses your Claude subscription OAuth token (`sk-ant-oat...`) obtained from `claude setup-token`.
   - Runs isolated in subprocess mode.

---

## How Claude Code CLI is Invoked

When `AI_PROVIDER=claude` is used, the bot invokes the local `claude` executable via subprocess:
- Runs in headless print mode (`-p`) with `--safe-mode`.
- Hooks, telemetry, and unneeded plugins disabled via `--settings`.
- Native tools and MCP disabled via `--tools ""` and `--strict-mcp-config`.
- Subprocess environment is strictly isolated (`ANTHROPIC_*`, bot tokens, and host secrets stripped).
- System prompt is written to a temporary file (`--system-prompt-file`) with `0600` permissions.
- Subscription OAuth token (`sk-ant-oat...`) is passed via `CLAUDE_CODE_OAUTH_TOKEN` in child env only.
- Output tokens are redacted in logs (`[REDACTED]`).

---

## Deploy to Railway

Follow these steps to deploy to Railway:

1. **Obtain Claude OAuth Token**:
   Run `claude setup-token` in your local terminal. Authenticate via your browser. Copy the `sk-ant-oat...` token.
2. **Setup Telegram Bot**:
   - Open `@BotFather` on Telegram.
   - Run `/newbot` and follow prompts.
   - Go to *Bot Settings → Business Mode* and turn it **ON**.
   - Note your Telegram user ID from `@userinfobot`.
3. **Push to Private GitHub Repository**:
   - Create a **private** repository on GitHub (`profile.md` contains personal facts and will be in the Docker image).
   - Push this codebase to `main`.
4. **Create Project in Railway**:
   - Go to Railway dashboard → **New Project** → **Deploy from GitHub repo**.
   - Select your private repository.
5. **Set Service Variables**:
   In Railway → Service Settings → **Variables** (Raw Editor):
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
   OWNER_ID=your_numeric_telegram_user_id
   CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
   AUTO_SEND=false
   ```
   *(Optional: `CLAUDE_MODEL`, `CLAUDE_EFFORT=low`). Do NOT set `CLAUDE_BIN`, `DATA_DIR`, or `PROFILE_PATH`.*
6. **Add Persistent Volume**:
   - Right click on the Railway canvas (or press `Cmd+K` / `Ctrl+K`) → **Volume**.
   - Attach the volume to your service with Mount Path: `/app/data`.
   - *Crucial: Without this volume, conversation history, pause state, and limits are lost on redeploy.*
7. **Verify Deployment Logs**:
   Look for:
   ```
   Bot @your_bot is running
   Mode: PREVIEW (replies are only shown in the logs, not sent)
   Claude auth: subscription token from environment variables
   ```
8. **Connect Telegram Business**:
   - Open Telegram on phone or desktop → *Settings → Telegram Business → Chatbots* (requires Telegram Premium).
   - Enter your bot's username.
   - Select which chats the bot can see, and enable reply permissions.
9. **Review Previews & Enable Auto-Send**:
   - Have a friend message you or review logs when someone messages you.
   - Look for `=== PREVIEW (not sent) ===` in Railway logs.
   - When satisfied with replies, update `AUTO_SEND=true` in Railway Variables.

> **Important**:
> - Never run local `python bot.py` and Railway simultaneously with the same bot token (polling conflict).
> - Every git push will trigger an automatic redeploy. Updating `profile.md` is deployed via `git commit` and `git push`.
> - If your token expires or leaks, regenerate with `claude setup-token` and update Railway variables.

---

## Local Development & Testing

1. Create and activate virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Or on Windows: .\.venv\Scripts\activate
   pip install -r requirements-dev.txt
   ```
2. Setup `.env`:
   ```bash
   cp .env.example .env
   chmod 600 .env  # On Linux/macOS
   ```
3. Test Claude CLI security isolation:
   ```bash
   python bot.py --check-claude
   ```
4. Test response generation with sample message:
   ```bash
   python bot.py --try "Salom, bugun bo'shmisan?"
   ```
5. Run the offline test suite:
   ```bash
   pytest -v
   ```

---

## CLI Commands and Exit Codes

- `python bot.py`: Start bot using `.env` settings.
- `python bot.py --settings-from-env`: Start bot in container mode (reads and sanitizes `os.environ`).
- `python bot.py --check-claude`: Run security audit on Claude CLI (Exit code 0 on success, 1 on check failure, 2 on config error).
- `python bot.py --try "<text>"`: Generate a one-off test reply (Exit code 0 on success, 1 on error, 2 on config error).

---

## Project Structure

```
bot.py                              # Main application entry point and CLI runner
assistant/
  __init__.py                       # Package definition
  config.py                         # Settings dataclass and .env / env-var loading
  claude_cli.py                     # Subprocess isolation wrapper for Claude Code CLI
  language.py                       # Language detector (Uzbek Latin/Cyrillic, Russian, English)
  prompts.py                        # System and user prompt formatters
  store.py                          # Atomic JSON stores: history, seen IDs, runtime state
  service.py                        # Business logic, takeover timers, outcome pipeline
  handlers.py                       # Aiogram router and event handlers
profile.md                          # Personal profile and response template
.env.example                        # Template for environment variables
Dockerfile                          # Production Docker container definition
railway.json                        # Railway deployment specification
requirements.txt                    # Production dependencies
requirements-dev.txt                # Development and test dependencies
pytest.ini                          # Pytest configuration
README.md                           # Documentation
tests/
  __init__.py
  conftest.py                       # Mock harness, fake generator, and offline recording session
  test_business_updates.py          # Business updates, takeover, and auto-send tests
  test_claude_cli.py                # ClaudeCLI isolation, redaction, and error tests
  test_config_and_language.py       # Configuration loading, environment wiping, and language tests
```

---

## Moliya modulining qo'shimcha buyruqlari

| Buyruq | Vazifasi |
|---|---|
| `/export` yoki `/export 2026-10` | Oylik hisobot Excel (.xlsx) faylda: tranzaksiyalar, kategoriya bo'yicha jamlama, xulosa |
| `/setrate 12650` | 1 USD kursini qo'lda belgilash (dollar summalari so'm hisobotiga qo'shilmaydi, alohida ko'rsatiladi) |
| `/addrecurring 5 Ijara 2 million` | Har oyning 5-kuni uchun doimiy to'lov; soat 09:00 da eslatma + "Xarajatga qo'shish" tugmasi |
| `/recurring`, `/delrecurring 3` | Doimiy to'lovlar ro'yxati / o'chirish |

Boshqa o'zgarishlar:
- Tasdiqlash kartasidagi **✏️ Tahrirlash** tugmasi summa va kategoriyani bot ichida o'zgartiradi.
- Qoidalar tushunmagan, lekin pul haqidagi gaplar Gemini orqali tahlil qilinadi va doim tasdiqlash tugmasi bilan ko'rsatiladi.
- Gemini kvotasi/kaliti muammosi yoki kutilmagan xato bo'lsa egasiga qisqa xabar boradi (soatiga ko'pi bilan 1 ta).
- SQLite ishlatilsa, baza har kecha 03:00 (Toshkent) da egasiga `.db.gz` fayl sifatida yuboriladi (PostgreSQL uchun Railway zaxirasidan foydalaning).
- Vaqt: barcha "bugun/kecha" hisoblari Asia/Tashkent bo'yicha.

---

## Dashboard va Google Sheets

**Dashboard** (rasm + raqamlar): `/dashboard` (joriy oy), `/dashboard kun`, `/dashboard hafta`, `/dashboard 2026-09`,
yoki oddiy yozing: "dashboard ko'rsat", "haftalik dashboard". `/finance` menyusida ham "🖼 Dashboard" tugmasi bor.
Rasmda: daromad / xarajat / sof balans / o'rtacha kunlik xarajat, kunlik ustunlar, kategoriyalar, yig'ma xarajat
(byudjet va oy oxirigacha taxmin bilan). Faqat tasdiqlangan yozuvlar hisoblanadi; dollar alohida ko'rsatiladi.

**Google Sheets** (har tasdiqlangan, tahrirlangan yoki bekor qilingan yozuvdan keyin ~20 soniyada yangilanadi):
varaqlar: `YYYY-MM` (oyning barcha yozuvlari), `Kunlik` (kunlik jami), `Oylik` (oylik jami).

Sozlash (bir marta):
1. Google Cloud Console'da loyiha oching, **Google Sheets API** va **Google Drive API** ni yoqing.
2. *IAM → Service accounts* da yangi service account yarating, **Keys → Add key → JSON** ni yuklab oling.
3. Google Sheets'da yangi bo'sh jadval oching va uni service account email'iga (`...iam.gserviceaccount.com`) **Editor** sifatida ulashing.
4. Railway Variables:
   - `GOOGLE_SHEETS_ID` — jadval havolasidagi `/d/` va `/edit` orasidagi qism;
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — yuklab olingan JSON fayl matni (yoki uning base64 ko'rinishi).
5. Deploydan keyin botda `/sheetsync` yuboring (mavjud barcha oylarni yozadi) va `/sheet` bilan havolani oling.

JSON kalit hech qachon gitga qo'yilmasin. Sinxronlash xatosi bo'lsa egasiga qisqa xabar boradi (soatiga 1 ta).

### Google Sheets — kalitsiz (oddiyroq) yo'l: Apps Script

Google Cloud'da kalit yaratish bloklangan bo'lsa (tashkilot siyosati), service account kerak emas:
1. Jadvalda **Extensions (Kengaytmalar) → Apps Script** ni oching, [docs/google_apps_script.gs](docs/google_apps_script.gs) ichidagi kodni qo'ying va `SECRET` ga uzun maxfiy so'z yozing.
2. **Deploy → New deployment → Web app**: *Execute as: Me*, *Who has access: Anyone* → **Deploy**, ruxsat bering.
3. Chiqqan `https://script.google.com/macros/s/.../exec` havolani oling.
4. Railway Variables: `GOOGLE_APPS_SCRIPT_URL` (havola) va `GOOGLE_APPS_SCRIPT_SECRET` (1-qadamdagi so'z).
5. Deploydan keyin botga `/sheetsync` yuboring.

Ikkala usul ham sozlangan bo'lsa, Apps Script ishlatiladi.
