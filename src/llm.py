"""Local LLM access via the `claude -p` CLI — no API key, uses the local session.

All intelligence generation (highlights, metrics) runs locally, so we drive the
installed Claude Code CLI in print mode rather than the Anthropic SDK. Runs from a
neutral cwd so the project's own CLAUDE.md / tools don't bleed into generations.
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404
import tempfile

logger = logging.getLogger("edgar-llm")


def claude_available() -> bool:
    return shutil.which("claude") is not None


def claude_prompt(
    prompt: str,
    system: str | None = None,
    model: str = "sonnet",
    timeout: int = 300,
) -> str | None:
    """Run one headless `claude -p` turn; return stdout text, or None on failure.

    Prompt is passed on stdin; system prompt and model via flags.
    """
    if not claude_available():
        logger.error("`claude` CLI not found on PATH — cannot generate intelligence")
        return None

    # --strict-mcp-config with no --mcp-config: skip the user's global MCP servers
    # (playwright/gmail/etc.) — pure text generation needs none, and loading them
    # adds large startup latency.
    cmd = [
        "claude", "-p", "--output-format", "text",
        "--model", model, "--strict-mcp-config",
    ]
    if system:
        cmd += ["--system-prompt", system]
    try:
        result = subprocess.run(  # nosec B603 B607
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),  # neutral cwd: no project context bleed
            check=True,
        )
    except subprocess.TimeoutExpired:
        logger.error("claude -p timed out after %ds", timeout)
        return None
    except subprocess.CalledProcessError as e:
        logger.error("claude -p failed (%s): %s", e.returncode, (e.stderr or "")[:300])
        return None
    return result.stdout.strip()
