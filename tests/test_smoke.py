"""Smoke tests — verify the skeleton imports cleanly and dry-run works.

Run:
    pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_imports_cleanly():
    from src.agents.base import BaseAgent, AgentRun  # noqa: F401
    from src.data import store  # noqa: F401
    from src.data.scrapers import base as scraper_base  # noqa: F401
    assert True


def test_schema_initialises(tmp_path: Path, monkeypatch):
    from src.data import store

    db_path = tmp_path / "test.sqlite"
    monkeypatch.setenv("BRIDGE_DB_PATH", str(db_path))
    store.init_schema(db_path)

    with store.connect(db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"posts", "sentiment", "traces"} <= tables


def test_issue_landscape_dry_run(tmp_path):
    from scripts.run_agent import IssueLandscapeAgent, load_fact_pack

    facts_dir = REPO_ROOT / "data" / "facts"
    facts = load_fact_pack(facts_dir)
    assert "timeline" in facts, "expected data/facts/timeline.md to exist"
    assert "motorcycle_lane" in facts

    agent = IssueLandscapeAgent()
    os.environ["BRIDGE_DRY_RUN"] = "1"
    try:
        run = agent.run({"facts": facts}, subject_id="tamsui_bridge", dry_run=True)
    finally:
        os.environ.pop("BRIDGE_DRY_RUN", None)

    assert run.response is None  # dry-run leaves response empty
    assert "淡江大橋" in run.user_message
    assert run.system_prompt.startswith("# 議題地景")

    # trace can be saved
    out = run.save(tmp_path)
    assert out.exists()
    assert "# Agent Run: issue_landscape" in out.read_text(encoding="utf-8")


def test_prompts_exist():
    prompts_dir = REPO_ROOT / "src" / "agents" / "prompts"
    for name in ("issue_landscape.md", "sentiment_extractor.md", "strategic_dialectic.md"):
        p = prompts_dir / name
        assert p.exists(), f"missing prompt: {p}"
        text = p.read_text(encoding="utf-8")
        # Quality sanity check — every prompt should have dialectical framing cue
        assert "假設" in text or "反駁" in text or "辯證" in text or "抽取" in text


def test_seed_urls_valid_json():
    import json

    p = REPO_ROOT / "data" / "sources" / "seed_urls.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "news_articles" in data
    assert len(data["news_articles"]) >= 10
    assert any("2.5" in art["title"] for art in data["news_articles"]), \
        "expected at least one title mentioning 2.5m motorcycle lane"
