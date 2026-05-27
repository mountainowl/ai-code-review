from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import tomllib


ROOT = Path(os.environ.get("LLM_CODE_REVIEW_ROOT", Path.home() / ".local" / "share" / "llm-reviewer"))
PROMPT_FILE = Path(os.environ.get("LLM_CODE_REVIEW_PROMPT", ROOT / "prompts" / "00-meta.md"))
LOG_DIR = ROOT / "var" / "log" / "codex"
CODEX_PROFILE = os.environ.get("CODEX_REVIEW_PROFILE", "llm-reviewer")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def configured_max_findings() -> int:
    for key in ("LLM_REVIEW_MAX_FINDINGS", "MAX_FINDINGS_PER_REVIEW"):
        value = os.environ.get(key)
        if value:
            try:
                parsed = int(value)
            except ValueError:
                break
            if parsed > 0:
                return parsed
    config = ROOT / "config" / "poller.toml"
    if config.is_file():
        try:
            value = tomllib.loads(config.read_text(encoding="utf-8")).get("max_findings_per_review", 5)
            parsed = int(value)
            if parsed > 0:
                return parsed
        except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
            pass
    return 5


def render_prompt_file(prompt_file: Path, max_findings: int) -> Path:
    text = prompt_file.read_text(encoding="utf-8").replace("{{MAX_FINDINGS_PER_REVIEW}}", str(max_findings))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rendered = LOG_DIR / f"rendered-meta-max-{max_findings}.md"
    if not rendered.exists() or rendered.read_text(encoding="utf-8") != text:
        rendered.write_text(text, encoding="utf-8")
    return rendered


def review_task_prompt(review_task: str, prompt_file: Path | None = None) -> str:
    prompt_path = prompt_file or PROMPT_FILE
    return f"""/using-superpowers
$code-reviewer

Fetch the GitLab merge request using GitLab MCP and local git/glab as needed, then pass the code changes to the $code-reviewer skill for analysis.
Use this rendered meta prompt file for the review contract: {prompt_path}

Review task:
{review_task}

Use diff review unless the task explicitly asks for full code review.
Do not post comments to GitLab.
Return only the final review findings allowed by the review task and the $code-reviewer skill.
Do not include CLI transcript, tool logs, token counts, status commentary, or summaries."""


def codex_command() -> list[str]:
    return [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--profile",
        CODEX_PROFILE,
        "--skip-git-repo-check",
    ]


def main() -> int:
    if not PROMPT_FILE.is_file():
        print(f"code-review prompt is not readable: {PROMPT_FILE}", file=sys.stderr)
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rendered_prompt = render_prompt_file(PROMPT_FILE, configured_max_findings())
    review_task = " ".join(sys.argv[1:]).strip() or "Review the current changes."
    prompt = review_task_prompt(review_task, rendered_prompt)
    transcript_path = LOG_DIR / f"codex-transcript-{stamp()}-{os.getpid()}.log"
    cmd = codex_command() + [prompt]
    env = os.environ.copy()
    env["LLM_CODE_REVIEW_PROMPT"] = str(rendered_prompt)

    result = subprocess.run(
        cmd,
        cwd=os.getcwd(),
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    output = result.stdout or ""
    transcript_path.write_text(output, encoding="utf-8")
    if output:
        print(output.rstrip())

    if result.returncode:
        print(f"codex exited {result.returncode}; transcript={transcript_path}", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
