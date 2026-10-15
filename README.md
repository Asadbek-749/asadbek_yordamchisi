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
   - Uses official Google Gemini API (default: `gemini-1.5-flash`).
   - Get a free key at [Google AI Studio](https://aistudio.google.com/).
   - Set in `.env`:
     ```env
     AI_PROVIDER=gemini
     GEMINI_API_KEY=AIzaSy...
     GEMINI_MODEL=gemini-1.5-flash
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
