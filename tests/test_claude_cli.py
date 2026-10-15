"""Tests for ClaudeCLI wrapper, failure classification, and isolation."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import sys
import time

import pytest

from assistant.claude_cli import (
    ClaudeAuthError,
    ClaudeCLI,
    ClaudeError,
    ClaudeLimitError,
    ClaudeTimeout,
    build_env,
    classify_failure,
    parse_result,
    redact_tokens,
)


@pytest.fixture
def fake_claude(tmp_path: Path):
    """Create a mock claude binary script that records execution details."""
    dump_file = tmp_path / "claude_call.json"
    py_script = tmp_path / "fake_claude.py"

    code = f"""import json, os, sys, time

dump_file = {repr(str(dump_file))}
mode = os.environ.get("FAKE_MODE", "ok")

stdin_data = sys.stdin.read() if not sys.stdin.isatty() else ""

# Read system prompt file if present in args
sys_prompt_content = None
sys_prompt_stat = None
if "--system-prompt-file" in sys.argv:
    idx = sys.argv.index("--system-prompt-file")
    if idx + 1 < len(sys.argv):
        sp_path = sys.argv[idx + 1]
        if os.path.exists(sp_path):
            with open(sp_path, "r", encoding="utf-8") as f:
                sys_prompt_content = f.read()
            sys_prompt_stat = oct(os.stat(sp_path).st_mode & 0o777)

info = {{
    "argv": sys.argv[1:],
    "stdin": stdin_data,
    "env": dict(os.environ),
    "cwd": os.getcwd(),
    "pid": os.getpid(),
    "sys_prompt": sys_prompt_content,
    "sys_prompt_mode": sys_prompt_stat,
}}
with open(dump_file, "w", encoding="utf-8") as f:
    json.dump(info, f)

if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
elif mode == "limit_epoch":
    print(json.dumps({{"type": "result", "is_error": True, "result": "usage limit reached |1700003600"}}))
    sys.exit(1)
elif mode == "limit_text":
    print(json.dumps({{"type": "result", "is_error": True, "result": "You hit your usage limit resets at 5pm UTC"}}))
    sys.exit(1)
elif mode == "auth":
    print(json.dumps({{"type": "result", "is_error": True, "result": "not logged in sk-ant-oat01-secret123456"}}))
    sys.exit(1)
elif mode == "crash":
    sys.stderr.write("fatal internal error in claude\\n")
    sys.exit(1)
elif mode == "empty":
    sys.exit(0)
elif mode == "echo_token":
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "NONE")
    print(json.dumps({{"type": "result", "subtype": "success", "result": tok}}))
    sys.exit(0)
else:
    # "ok" mode
    if "--output-format" in sys.argv and sys.argv[sys.argv.index("--output-format") + 1] == "stream-json":
        print(json.dumps({{"type": "system", "subtype": "init", "tools": [], "mcp_servers": [], "plugins": [], "skills": [], "apiKeySource": None, "apiProvider": "firstParty", "model": "claude-test"}}))
        print(json.dumps({{"type": "result", "subtype": "success", "result": "OK"}}))
    else:
        print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "  salom dunyo  "}}))
    sys.exit(0)
"""
    py_script.write_text(code, encoding="utf-8")

    if sys.platform == "win32":
        launcher = tmp_path / "claude.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{py_script}" %*\n', encoding="utf-8"
        )
        binary_path = str(launcher)
    else:
        launcher = tmp_path / "claude"
        launcher.write_text(
            f'#!{sys.executable}\n' + code, encoding="utf-8"
        )
        launcher.chmod(0o755)
        binary_path = str(launcher)

    return binary_path, dump_file


@pytest.mark.asyncio
async def test_successful_generate_and_stripping(fake_claude, tmp_path: Path):
    bin_path, dump_file = fake_claude
    cli = ClaudeCLI(
        claude_bin=bin_path,
        workdir=tmp_path / "work",
        model="claude-3-5-sonnet",
        effort="low",
    )

    res = await cli.generate("system instructions", "user message $(touch injected)")
    assert res == "salom dunyo"  # properly stripped

    dump = json.loads(dump_file.read_text(encoding="utf-8"))
    # User message in stdin, not argv
    assert dump["stdin"] == "user message $(touch injected)"
    assert "user message" not in " ".join(dump["argv"])
    # System prompt in file, not in argv
    assert dump["sys_prompt"] == "system instructions"
    assert "system instructions" not in " ".join(dump["argv"])
    # Shell injection literal was not evaluated
    assert not (tmp_path / "injected").exists()


@pytest.mark.asyncio
async def test_isolation_flags_and_env(fake_claude, monkeypatch, tmp_path: Path):
    bin_path, dump_file = fake_claude

    # Set forbidden env vars in parent process
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-bad")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:BOT")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")

    oauth_token = "sk-ant-oat01-mysecrettoken"
    cli = ClaudeCLI(
        claude_bin=bin_path,
        workdir=tmp_path / "work",
        oauth_token=oauth_token,
    )

    await cli.generate("sys", "usr")
    dump = json.loads(dump_file.read_text(encoding="utf-8"))
    child_env = dump["env"]

    # Verify forbidden env vars stripped
    assert "ANTHROPIC_API_KEY" not in child_env
    assert "TELEGRAM_BOT_TOKEN" not in child_env
    assert "CLAUDE_CODE_USE_BEDROCK" not in child_env
    assert "CLAUDE_CODE_SIMPLE" not in child_env

    # Isolation env set
    assert child_env.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY") == "1"
    assert child_env.get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") == "1"
    assert child_env.get("CLAUDE_CODE_OAUTH_TOKEN") == oauth_token

    # Verify flags in argv
    argv = dump["argv"]
    assert "--safe-mode" in argv
    assert "--tools" in argv
    assert "--strict-mcp-config" in argv
    assert "--disable-slash-commands" in argv
    assert "--no-session-persistence" in argv
    assert "--bare" not in argv
    assert "--mcp-config" not in argv

    # Settings json disabled hooks and plugins
    settings_idx = argv.index("--settings")
    settings_obj = json.loads(argv[settings_idx + 1])
    assert settings_obj["disableAllHooks"] is True
    assert settings_obj["enabledPlugins"]["cc-plugin-telemetry@builtin"] is False

    # Repr does not contain secret token
    assert oauth_token not in repr(cli)


@pytest.mark.asyncio
async def test_redaction_and_inspect(fake_claude, tmp_path: Path):
    bin_path, dump_file = fake_claude
    oauth_token = "sk-ant-oat01-testsecret"
    cli = ClaudeCLI(
        claude_bin=bin_path,
        workdir=tmp_path / "work",
        oauth_token=oauth_token,
    )

    insp = await cli.inspect()
    assert insp["auth"] == "subscription token"
    assert insp["reply"] == "OK"
    assert insp["init"]["tools"] == []


@pytest.mark.asyncio
async def test_failure_modes(fake_claude, monkeypatch, tmp_path: Path):
    bin_path, _ = fake_claude
    cli = ClaudeCLI(
        claude_bin=bin_path,
        workdir=tmp_path / "work",
        timeout=2,
    )

    # 1. Limit with epoch
    monkeypatch.setenv("FAKE_MODE", "limit_epoch")
    with pytest.raises(ClaudeLimitError) as exc_info:
        await cli.generate("s", "u")
    assert exc_info.value.resets_at == 1700003600

    # 2. Limit with text
    monkeypatch.setenv("FAKE_MODE", "limit_text")
    with pytest.raises(ClaudeLimitError) as exc_info2:
        await cli.generate("s", "u")
    assert "5pm UTC" in (exc_info2.value.reset_text or "")

    # 3. Auth error with token redacted
    monkeypatch.setenv("FAKE_MODE", "auth")
    with pytest.raises(ClaudeAuthError) as exc_info3:
        await cli.generate("s", "u")
    assert "sk-ant-oat01-secret123456" not in str(exc_info3.value)
    assert "[REDACTED]" in str(exc_info3.value)

    # 4. Crash
    monkeypatch.setenv("FAKE_MODE", "crash")
    with pytest.raises(ClaudeError):
        await cli.generate("s", "u")

    # 5. Empty
    monkeypatch.setenv("FAKE_MODE", "empty")
    with pytest.raises(ClaudeError):
        await cli.generate("s", "u")

    # 6. Timeout
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t0 = time.time()
    with pytest.raises(ClaudeTimeout):
        await cli.generate("s", "u")
    assert time.time() - t0 < 5.0


def test_classify_failure_and_parse_result():
    # 429 status
    err = classify_failure("Rate limit reached", status=429)
    assert isinstance(err, ClaudeLimitError)

    # 401 status with oauth
    err2 = classify_failure("Unauthorized", status=401, oauth_used=True)
    assert isinstance(err2, ClaudeAuthError)
    assert "claude setup-token" in str(err2)

    # parse_result success
    valid_json = json.dumps({"type": "result", "subtype": "success", "result": "done"})
    assert parse_result(0, valid_json, "") == "done"

    # parse_result error
    err_json = json.dumps({"type": "result", "subtype": "error", "is_error": True, "result": "fail"})
    with pytest.raises(ClaudeError):
        parse_result(1, err_json, "")
