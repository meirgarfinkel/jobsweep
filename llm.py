"""Model backend: `claude -p` by default, the Anthropic API optionally."""
import base64
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

API = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

_NOT_LOGGED_IN = "Claude Code isn't logged in: run 'claude' once in a terminal."


class LLMError(Exception):
    """A user-readable failure from the model backend."""


def _looks_like_auth(text: str) -> bool:
    t = (text or "").lower()
    return "login" in t or "log in" in t or "auth" in t


# The prompt holds personal data and an untrusted job description, so the CLI
# runs without the user's MCP servers, hooks, plugins, settings or CLAUDE.md,
# saves no transcript, and denies anything that would ask for permission.
# It runs from an empty temp dir so no project CLAUDE.md applies.
_ISOLATION = ["--strict-mcp-config", "--no-session-persistence", "--safe-mode",
              "--restricted", "--permission-prompts", "none"]


def _ask_cli(prompt: str, timeout: int, tools: str, add_dirs=()) -> str:
    argv = ["claude", "-p", "--output-format", "json", "--tools", tools] + _ISOLATION
    for d in add_dirs:
        argv += ["--add-dir", str(d)]
    model = os.environ.get("JOBSWEEP_MODEL")
    if model:
        argv += ["--model", model]
    try:
        with tempfile.TemporaryDirectory(prefix="jobsweep-") as cwd:
            proc = subprocess.run(argv, input=prompt, capture_output=True,
                                  text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        raise LLMError("Claude Code isn't installed (no 'claude' command).")
    except subprocess.TimeoutExpired:
        raise LLMError(f"Claude took longer than {timeout}s; try again.")
    data = None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        pass
    if not isinstance(data, dict):
        data = {}
    msg = str(data.get("result") or "") if data.get("is_error") else ""
    if proc.returncode != 0 or data.get("is_error"):
        detail = msg or (proc.stderr or "").strip() or (proc.stdout or "").strip()
        if _looks_like_auth(detail):
            raise LLMError(_NOT_LOGGED_IN)
        raise LLMError(f"Claude failed: {detail[:300] or 'unknown error'}")
    if "result" not in data:
        raise LLMError("Claude returned an unreadable reply; try again.")
    return str(data["result"])


def _ask_api(prompt: str, timeout: int, pdf_path: str | None = None) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("Set ANTHROPIC_API_KEY or use apply.backend: claude_cli.")
    content = prompt
    if pdf_path:
        with open(pdf_path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        content = [{"type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": data}},
                   {"type": "text", "text": prompt}]
    body = json.dumps({
        "model": os.environ.get("JOBSWEEP_MODEL", DEFAULT_MODEL),
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": content}],
    }).encode()
    req = urllib.request.Request(
        API, data=body, method="POST",
        headers={"content-type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise LLMError(f"The Anthropic API returned HTTP {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"Could not reach the Anthropic API: {e}")
    if payload.get("stop_reason") == "max_tokens":
        raise LLMError("The model's answer was cut off (too long); try again.")
    return "".join(b.get("text", "") for b in payload.get("content", []))


def ask(prompt: str, backend: str = "claude_cli", timeout: int = 120,
        tools: str = "", pdf_path: str | None = None, add_dirs=()) -> str:
    """add_dirs: directories the CLI may read (it runs from an empty temp dir)."""
    if backend == "api":
        return _ask_api(prompt, timeout, pdf_path)
    if backend == "claude_cli":
        return _ask_cli(prompt, timeout, tools, add_dirs)
    raise LLMError(f"Unknown backend '{backend}': use claude_cli or api.")
