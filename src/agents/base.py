"""Base class for bridge-analyst reasoning agents.

Design goals (same as district-analyst — keep the two in lockstep):

- **Reasoning ≠ data**. Agents receive a pre-built evidence package; they do
  not touch the database or the network themselves. This keeps reasoning
  auditable and lets us swap the evidence package to test robustness.
- **Prompts are first-class artifacts** — loaded from Markdown files in
  `prompts/`, versionable in git, editable by campaign staff without
  touching Python.
- **Every run saves a reasoning trace**: the exact evidence + prompt +
  response. When the agent produces something surprising (good or bad),
  we can re-read exactly what it saw.
- **Dry-run is a first-class mode**: every agent can be invoked with
  `--dry-run` to produce the prompt package without calling the LLM.
  Used for debugging prompts and for CI.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_MODEL = os.environ.get("BRIDGE_MODEL", "claude-sonnet-4-5")


@dataclass
class AgentRun:
    """A single invocation of an agent — captures everything needed for audit."""

    agent_name: str
    subject_id: str                 # e.g. "tamsui_bridge" / "motorcycle_lane"
    timestamp: str
    system_prompt: str
    evidence: dict[str, Any]
    user_message: str
    response: str | None = None
    model: str = DEFAULT_MODEL
    extras: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Human-reviewable reasoning trace."""
        lines = [
            f"# Agent Run: {self.agent_name}",
            "",
            f"- **Subject**: `{self.subject_id}`",
            f"- **Timestamp**: {self.timestamp}",
            f"- **Model**: {self.model}",
        ]
        if self.extras.get("usage"):
            u = self.extras["usage"]
            lines.append(
                f"- **Tokens**: in={u.get('input_tokens')}, out={u.get('output_tokens')}"
            )
        lines += [
            "",
            "---",
            "",
            "## System prompt",
            "",
            self.system_prompt,
            "",
            "---",
            "",
            "## Evidence package",
            "",
            "```json",
            json.dumps(self.evidence, ensure_ascii=False, indent=2),
            "```",
            "",
            "---",
            "",
            "## User message (sent to model)",
            "",
            self.user_message,
            "",
            "---",
            "",
            "## Agent output",
            "",
            self.response or "(not run — dry-run mode)",
        ]
        return "\n".join(lines)

    def save(self, traces_dir: Path) -> Path:
        traces_dir.mkdir(parents=True, exist_ok=True)
        safe_ts = self.timestamp.replace(":", "-")
        path = traces_dir / f"{self.agent_name}_{self.subject_id}_{safe_ts}.md"
        path.write_text(self.to_markdown(), encoding="utf-8")
        return path


class BaseAgent:
    """Subclass and set `NAME` + `PROMPT_FILE`, implement `build_user_message()`."""

    NAME: str = "base"
    PROMPT_FILE: str = ""  # filename in prompts/, e.g. "issue_landscape.md"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model

    # --- hooks subclasses override ---------------------------------

    def build_user_message(self, evidence: dict[str, Any]) -> str:
        raise NotImplementedError

    # --- shared machinery ------------------------------------------

    def load_system_prompt(self) -> str:
        path = PROMPTS_DIR / self.PROMPT_FILE
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8")

    def run(
        self,
        evidence: dict[str, Any],
        subject_id: str,
        *,
        dry_run: bool = False,
        max_tokens: int = 8192,
    ) -> AgentRun:
        system_prompt = self.load_system_prompt()
        user_message = self.build_user_message(evidence)

        run = AgentRun(
            agent_name=self.NAME,
            subject_id=subject_id,
            timestamp=datetime.now().isoformat(timespec="seconds"),
            system_prompt=system_prompt,
            evidence=evidence,
            user_message=user_message,
            model=self.model,
        )

        if dry_run or os.environ.get("BRIDGE_DRY_RUN") == "1":
            return run

        # Lazy import — dry-run works without anthropic installed.
        from anthropic import Anthropic

        client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        run.response = text
        run.extras["usage"] = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        return run
