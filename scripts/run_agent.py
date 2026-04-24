"""CLI entry point for running a single agent.

Usage:
    python -m scripts.run_agent --agent issue_landscape [--dry-run]
    python -m scripts.run_agent --agent sentiment_extractor --batch-size 20
    python -m scripts.run_agent --agent strategic_dialectic

Day 3+ expands each branch. Today (Day 1) we just wire up the CLI and
prove issue_landscape can dry-run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env automatically so the script works regardless of terminal settings.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass  # dotenv is optional; env vars can still come from the shell

from src.agents.base import BaseAgent  # noqa: E402


class IssueLandscapeAgent(BaseAgent):
    NAME = "issue_landscape"
    PROMPT_FILE = "issue_landscape.md"

    def build_user_message(self, evidence: dict) -> str:
        """Stuff the fact-pack markdown files into the user message."""
        sections = []
        for name, content in evidence.get("facts", {}).items():
            sections.append(f"## {name}\n\n{content}\n")
        aggregates = evidence.get("sentiment_aggregates")
        if aggregates:
            sections.append(
                "## 輿情抽取聚合結果\n\n```json\n"
                + json.dumps(aggregates, ensure_ascii=False, indent=2)
                + "\n```\n"
            )
        return (
            "# 事實包\n\n"
            + "\n---\n\n".join(sections)
            + "\n\n---\n\n"
            "請依 system prompt 指示，對淡江大橋議題地景做出策略判斷。"
        )


def load_fact_pack(facts_dir: Path) -> dict[str, str]:
    out = {}
    for md in sorted(facts_dir.glob("*.md")):
        out[md.stem] = md.read_text(encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bridge-analyst agent.")
    parser.add_argument(
        "--agent",
        required=True,
        choices=["issue_landscape", "sentiment_extractor", "strategic_dialectic"],
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--subject", default="tamsui_bridge", help="Subject id for trace filename"
    )
    parser.add_argument(
        "--batch-size", type=int, default=10, help="(sentiment_extractor only)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="(sentiment_extractor only) process at most N posts"
    )
    parser.add_argument(
        "--sources", default=None,
        help="(sentiment_extractor only) comma-separated: news,ptt,ptt_push"
    )
    parser.add_argument(
        "--version", default="v1", choices=["v1", "v2"],
        help="(sentiment_extractor only) v2 re-processes v1-marked unknown posts"
    )
    args = parser.parse_args()

    facts_dir = REPO_ROOT / "data" / "facts"
    traces_dir = REPO_ROOT / "data" / "traces"

    if args.agent == "issue_landscape":
        evidence = {"facts": load_fact_pack(facts_dir)}
        agent = IssueLandscapeAgent()
        run = agent.run(evidence, subject_id=args.subject, dry_run=args.dry_run)
        out = run.save(traces_dir)
        print(f"Saved trace to: {out}")
        if run.response:
            print("\n=== Agent output ===\n")
            print(run.response)
        return 0

    if args.agent == "sentiment_extractor":
        from src.agents.sentiment_extractor import extract_all

        # Parse --limit from args.batch_size semantics: batch_size stays, add --limit
        limit = getattr(args, "limit", None)
        sources = getattr(args, "sources", None)
        src_tuple = tuple(sources.split(",")) if sources else None

        processed, failed = extract_all(
            batch_size=args.batch_size,
            limit=limit,
            sources=src_tuple,
            dry_run=args.dry_run,
            version=args.version,
        )
        print(f"\nDone. processed={processed}  failed={failed}")
        return 0 if failed == 0 else 1

    if args.agent == "strategic_dialectic":
        evidence = build_strategic_dialectic_evidence(facts_dir, REPO_ROOT)
        agent = StrategicDialecticAgent()
        run = agent.run(
            evidence,
            subject_id=args.subject,
            dry_run=args.dry_run,
            max_tokens=8192,
        )
        out = run.save(traces_dir)
        print(f"Saved trace to: {out}")
        if run.response:
            print("\n=== Agent output ===\n")
            print(run.response)
        return 0

    return 2


class StrategicDialecticAgent(BaseAgent):
    NAME = "strategic_dialectic"
    PROMPT_FILE = "strategic_dialectic.md"

    def build_user_message(self, evidence: dict) -> str:
        sections = []

        # 1. Fact pack
        sections.append("# 事實包")
        for name, content in evidence.get("facts", {}).items():
            sections.append(f"## {name}\n\n{content}\n")

        # 2. issue_landscape output (Ch.1 的結果)
        il = evidence.get("issue_landscape_output")
        if il:
            sections.append("\n---\n\n# 前序 agent 產出：議題地景分析（issue_landscape）\n")
            sections.append(il)

        # 3. Sentiment aggregates
        agg = evidence.get("sentiment_aggregates")
        if agg:
            sections.append("\n---\n\n# 輿情抽取聚合結果（sentiment_extractor）\n")
            sections.append("```json\n" + json.dumps(agg, ensure_ascii=False, indent=2) + "\n```")

        sections.append(
            "\n---\n\n"
            "請依 system prompt 指示，對 NPP 的 A/B/C/D framing 候選做獨立辯證驗證，"
            "並給出三週內的具體行動方案。"
        )
        return "\n\n".join(sections)


def build_strategic_dialectic_evidence(facts_dir, repo_root) -> dict:
    """Gather facts + issue_landscape latest trace + sentiment aggregates."""
    evidence: dict = {
        "facts": load_fact_pack(facts_dir),
    }

    # Latest issue_landscape trace — take the newest by filename timestamp
    traces_dir = repo_root / "data" / "traces"
    il_traces = sorted(
        traces_dir.glob("issue_landscape_*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if il_traces:
        content = il_traces[0].read_text(encoding="utf-8")
        # Extract just the "Agent output" section (after the last "## Agent output" header)
        marker = "## Agent output"
        if marker in content:
            evidence["issue_landscape_output"] = content.split(marker, 1)[1].strip()
        else:
            evidence["issue_landscape_output"] = content

    # Sentiment aggregates — query DB
    try:
        from src.data import store  # local import to avoid hard dep in dry-run

        with store.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM sentiment").fetchone()[0]
            if total == 0:
                evidence["sentiment_aggregates"] = {"note": "no sentiment data extracted yet"}
            else:
                agg: dict = {"total_extracted": total}

                agg["demographic"] = [
                    {"label": r[0], "n": r[1]}
                    for r in conn.execute(
                        "SELECT COALESCE(demographic,'unknown'), COUNT(*) "
                        "FROM sentiment GROUP BY demographic ORDER BY 2 DESC"
                    )
                ]
                agg["stance"] = [
                    {"label": r[0], "n": r[1]}
                    for r in conn.execute(
                        "SELECT COALESCE(stance,'unknown'), COUNT(*) "
                        "FROM sentiment GROUP BY stance ORDER BY 2 DESC"
                    )
                ]
                agg["emotion"] = [
                    {"label": r[0], "n": r[1]}
                    for r in conn.execute(
                        "SELECT COALESCE(emotion,'unknown'), COUNT(*) "
                        "FROM sentiment GROUP BY emotion ORDER BY 2 DESC"
                    )
                ]
                agg["demographic_x_stance"] = [
                    {"demographic": r[0], "stance": r[1], "n": r[2]}
                    for r in conn.execute(
                        "SELECT COALESCE(demographic,'unknown'), COALESCE(stance,'unknown'), COUNT(*) "
                        "FROM sentiment GROUP BY demographic, stance ORDER BY 3 DESC"
                    )
                ]

                # top topics
                topic_counts: dict[str, int] = {}
                for row in conn.execute(
                    "SELECT topics FROM sentiment WHERE topics IS NOT NULL"
                ):
                    for t in (row[0] or "").split(","):
                        t = t.strip()
                        if t:
                            topic_counts[t] = topic_counts.get(t, 0) + 1
                agg["top_topics"] = [
                    {"topic": t, "n": n}
                    for t, n in sorted(topic_counts.items(), key=lambda x: -x[1])[:15]
                ]

                # sample quotes per (stance, demographic) — limit 2 each for top 5 demographics
                samples = []
                for dem_row in conn.execute(
                    "SELECT demographic FROM sentiment WHERE demographic IS NOT NULL "
                    "GROUP BY demographic ORDER BY COUNT(*) DESC LIMIT 5"
                ):
                    dem = dem_row[0]
                    for stance in ("critical", "support", "ambivalent"):
                        for quote_row in conn.execute(
                            "SELECT quote, emotion FROM sentiment "
                            "WHERE demographic=? AND stance=? AND quote IS NOT NULL "
                            "  AND LENGTH(quote) > 10 "
                            "ORDER BY RANDOM() LIMIT 2",
                            (dem, stance),
                        ):
                            samples.append({
                                "demographic": dem, "stance": stance,
                                "emotion": quote_row[1], "quote": quote_row[0],
                            })
                agg["sample_quotes"] = samples

                evidence["sentiment_aggregates"] = agg
    except Exception as e:  # noqa: BLE001
        evidence["sentiment_aggregates"] = {"error": str(e)}

    return evidence


if __name__ == "__main__":
    raise SystemExit(main())
