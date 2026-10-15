"""Subprocess execution wrapper for Claude Code CLI."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from assistant.store import ensure_private_dir

# Built-in plugins to disable explicitly in isolation mode
ISOLATION_SETTINGS = {
    "disableAllHooks": True,
    "enabledPlugins": {
        "cc-plugin-agents-md@builtin": False,
        "cc-plugin-telemetry@builtin": False,
        "cc-plugin-plugin-authoring@builtin": False,
    },
}

TOKEN_PATTERN = re.compile(r"sk-ant-(?:oat01|api03)-[A-Za-z0-9_-]+")
EPOCH_PATTERN = re.compile(r"\|(\d{10})")
RESET_TEXT_PATTERN = re.compile(r"(?i)resets\s+(?:at\s+)?([^\n|]+)")
LIMIT_REGEX = re.compile(
    r"(?i)usage\s+limit|hit\s+your\s+(?:usage\s+)?limit|limit\s+reached|rate[ _-]?limit|out\s+of\s+(?:extra\s+)?usage|quota"
)
AUTH_REGEX = re.compile(
    r"(?i)not\s+logged\s+in|/login|invalid\s+api\s+key|authentication|unauthori[sz]ed|oauth\s+token"
)


class ClaudeError(Exception):
    """Base error for Claude CLI interactions."""


class ClaudeTimeout(ClaudeError):
    """Raised when Claude CLI execution times out."""


class ClaudeAuthError(ClaudeError):
    """Raised when authentication fails."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class ClaudeLimitError(ClaudeError):
    """Raised when usage limits or quotas are hit."""

    def __init__(
        self,
        detail: str,
        resets_at: Optional[int] = None,
        reset_text: Optional[str] = None,
    ) -> None:
        self.detail = detail
        self.resets_at = resets_at
        self.reset_text = reset_text

        if reset_text:
            msg = f"Claude subscription usage limit reached (resets {reset_text})"
        elif resets_at:
            t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(resets_at))
            msg = f"Claude subscription usage limit reached (resets at {t_str} UTC)"
        else:
            msg = "Claude subscription usage limit reached"
        super().__init__(msg)


def redact_tokens(text: str, token: Optional[str] = None) -> str:
    """Redact secret tokens from output text."""
    if not text:
        return ""
    res = text
    if token:
        res = res.replace(token, "[REDACTED]")
    return TOKEN_PATTERN.sub("[REDACTED]", res)


def build_env(oauth_token: Optional[str] = None) -> Dict[str, str]:
    """Build isolated child environment without host secrets or external providers."""
    env = dict(os.environ)

    # Remove Anthropic and third-party provider keys
    for k in list(env.keys()):
        if k.upper().startswith("ANTHROPIC_"):
            del env[k]

    forbidden_keys = (
        "TELEGRAM_BOT_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SIMPLE",
    )
    for k in forbidden_keys:
        env.pop(k, None)

    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] = "1"

    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token

    return env


def classify_failure(
    message: str,
    status: Optional[int] = None,
    oauth_used: bool = False,
) -> ClaudeError:
    """Classify execution failure into specific ClaudeError subclass."""
    # Check limit condition
    is_limit = status == 429 or bool(LIMIT_REGEX.search(message))
    if is_limit:
        resets_at: Optional[int] = None
        epoch_m = EPOCH_PATTERN.search(message)
        if epoch_m:
            try:
                resets_at = int(epoch_m.group(1))
            except ValueError:
                pass

        reset_text: Optional[str] = None
        text_m = RESET_TEXT_PATTERN.search(message)
        if text_m:
            reset_text = text_m.group(1).strip()

        cleaned_detail = EPOCH_PATTERN.sub("", message).strip()
        return ClaudeLimitError(cleaned_detail, resets_at=resets_at, reset_text=reset_text)

    # Check authentication condition
    is_auth = (status in (401, 403)) or bool(AUTH_REGEX.search(message))
    if is_auth:
        detail = message[:300].strip()
        if oauth_used:
            msg = (
                "Claude subscription token was rejected or expired: run 'claude setup-token' "
                f"on a computer with a browser, and update CLAUDE_CODE_OAUTH_TOKEN. CLI said: {detail}"
            )
        else:
            msg = (
                "Claude CLI is not authenticated. Please run 'claude' and perform /login. "
                f"CLI said: {detail}"
            )
        return ClaudeAuthError(msg, detail=detail)

    detail = message[:500].strip()
    return ClaudeError(detail)


def parse_result(
    returncode: int,
    stdout: str,
    stderr: str,
    oauth_used: bool = False,
) -> str:
    """Parse JSON result from claude -p stdout."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        err_msg = stderr.strip() or f"exit code {returncode}"
        raise classify_failure(err_msg, oauth_used=oauth_used)

    last_line = lines[-1]
    try:
        obj = json.loads(last_line)
    except json.JSONDecodeError:
        err_msg = stderr.strip() or stdout.strip() or f"exit code {returncode}"
        raise classify_failure(err_msg, oauth_used=oauth_used)

    if not isinstance(obj, dict):
        raise ClaudeError(f"Unexpected JSON output structure: {type(obj).__name__}")

    if obj.get("is_error") or obj.get("subtype") != "success":
        api_status = obj.get("api_error_status")
        result_text = obj.get("result") or ""
        combined = f"{result_text} {stderr}".strip()
        raise classify_failure(combined, status=api_status, oauth_used=oauth_used)

    result = obj.get("result")
    if result is None:
        raise ClaudeError("Missing 'result' field in Claude response")

    return str(result).strip()


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Cross-platform process group termination."""
    try:
        if sys.platform != "win32" and hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass


class ClaudeCLI:
    """Async wrapper around Claude Code CLI."""

    def __init__(
        self,
        claude_bin: str,
        *,
        workdir: Path,
        model: Optional[str] = None,
        effort: str = "low",
        timeout: int = 120,
        oauth_token: Optional[str] = None,
        auth_label: Optional[str] = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.workdir = Path(workdir).resolve()
        self.model = model
        self.effort = effort
        self.timeout = max(1, timeout)
        self._oauth_token = oauth_token
        self._auth_label = auth_label

    def __repr__(self) -> str:
        return (
            f"ClaudeCLI(bin={self.claude_bin!r}, model={self.model!r}, "
            f"effort={self.effort!r}, timeout={self.timeout}, "
            f"auth_mode={self.auth_mode!r})"
        )

    @property
    def auth_mode(self) -> str:
        if self._auth_label:
            return self._auth_label
        if self._oauth_token:
            return "subscription token"
        return "this machine's claude login"

    def _resolve_binary(self) -> str:
        # Check explicit path
        p = Path(self.claude_bin)
        if p.is_file():
            return str(p)
        found = shutil.which(self.claude_bin)
        if found:
            return found
        raise ClaudeError(f"Claude binary not found: {self.claude_bin}")

    def _build_command(
        self,
        system_prompt_path: str,
        output_format: str = "json",
    ) -> List[str]:
        cmd = [
            self._resolve_binary(),
            "-p",
            "--safe-mode",
            "--settings",
            json.dumps(ISOLATION_SETTINGS),
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--system-prompt-file",
            system_prompt_path,
            "--output-format",
            output_format,
        ]
        if output_format == "stream-json":
            cmd.append("--verbose")
        if self.model:
            cmd.extend(["--model", self.model])
        cmd.extend(["--effort", self.effort])
        return cmd

    async def _run_process(
        self,
        cmd: List[str],
        user_prompt: str,
    ) -> tuple[int, str, str]:
        ensure_private_dir(self.workdir)
        env = build_env(self._oauth_token)

        kwargs: Dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(self.workdir),
            "env": env,
        }
        if sys.platform != "win32":
            kwargs["start_new_session"] = True

        proc = await asyncio.create_subprocess_exec(*cmd, **kwargs)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=user_prompt.encode("utf-8")),
                timeout=float(self.timeout),
            )
        except asyncio.TimeoutError:
            _kill_process_group(proc)
            raise ClaudeTimeout(f"Claude CLI timed out after {self.timeout}s")
        except asyncio.CancelledError:
            _kill_process_group(proc)
            raise

        rc = proc.returncode if proc.returncode is not None else 1
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        # Redact token from outputs
        stdout = redact_tokens(stdout, self._oauth_token)
        stderr = redact_tokens(stderr, self._oauth_token)

        return rc, stdout, stderr

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Run generation with json output format."""
        ensure_private_dir(self.workdir)
        # Create temp file for system prompt with 0600 permissions
        fd, tmp_file = tempfile.mkstemp(
            dir=str(self.workdir), prefix="sys_", suffix=".txt", text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(system_prompt)
            if os.name == "posix":
                try:
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    pass

            cmd = self._build_command(tmp_file, output_format="json")
            rc, stdout, stderr = await self._run_process(cmd, user_prompt)
            return parse_result(
                rc, stdout, stderr, oauth_used=bool(self._oauth_token)
            )
        finally:
            if os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except OSError:
                    pass

    async def inspect(self) -> Dict[str, Any]:
        """Perform inspect query with stream-json to verify environment isolation."""
        ensure_private_dir(self.workdir)
        fd, tmp_file = tempfile.mkstemp(
            dir=str(self.workdir), prefix="insp_", suffix=".txt", text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("Reply with exactly: OK")
            if os.name == "posix":
                try:
                    os.chmod(tmp_file, 0o600)
                except OSError:
                    pass

            cmd = self._build_command(tmp_file, output_format="stream-json")
            rc, stdout, stderr = await self._run_process(cmd, "ping")

            init_data: Dict[str, Any] = {}
            reply_text = ""

            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    if obj.get("type") == "system" and obj.get("subtype") == "init":
                        init_data = {
                            "tools": obj.get("tools", []),
                            "mcp_servers": obj.get("mcp_servers", []),
                            "plugins": obj.get("plugins", []),
                            "skills": obj.get("skills", []),
                            "apiKeySource": obj.get("apiKeySource"),
                            "apiProvider": obj.get("apiProvider"),
                            "model": obj.get("model"),
                        }
                    elif obj.get("type") == "result":
                        reply_text = str(obj.get("result", "")).strip()

            return {
                "auth": self.auth_mode,
                "init": init_data,
                "reply": reply_text,
            }
        finally:
            if os.path.exists(tmp_file):
                try:
                    os.unlink(tmp_file)
                except OSError:
                    pass
