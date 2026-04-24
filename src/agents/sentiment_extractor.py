"""Sentiment extractor — batch-mode LLM feature extraction over posts table.

設計原則：
- 一次 LLM call 處理一批貼文（預設 10 筆），省 system prompt 的重複成本
- 只處理還沒 extract 過的貼文（idempotent — 可以中斷重跑）
- 預先過濾極短的 PTT 推噓（< 5 字，訊噪比低，給 LLM 是浪費錢）
- 用 `python-json` robust parser：LLM 有時會回 markdown 圍欄，要剝掉

輸出：寫入 `sentiment` 表，每筆 post 對應一筆 sentiment。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

from src.agents.base import BaseAgent, DEFAULT_MODEL
from src.data import store

AGENT_VERSION_V1 = "sentiment_extractor@v1"
AGENT_VERSION_V2 = "sentiment_extractor@v2"
# Default kept as V1 for backward compat
AGENT_VERSION = AGENT_VERSION_V1

PROMPT_FILE_V1 = "sentiment_extractor.md"
PROMPT_FILE_V2 = "sentiment_extractor_v2.md"

# Prefilter: PTT push 文字 < MIN_LEN 跳過
MIN_PUSH_LEN = 5
MIN_ARTICLE_LEN = 20

# Layer-2 sub-category codes that v2 can produce (for dashboard display)
V2_UNKNOWN_SUBCATEGORIES = {
    "sarcastic_noise",
    "generic_critique",
    "generic_support",
    "pure_emotion",
    "off_topic",
    "short_fact",
    "meta_discussion",
}


def _clean_json(text: str) -> str:
    """Strip markdown fences / leading explanations LLM sometimes adds."""
    text = text.strip()
    # Strip ```json ... ``` fences if present
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if m:
        text = m.group(1).strip()
    return text


class SentimentExtractorAgent(BaseAgent):
    NAME = "sentiment_extractor"
    PROMPT_FILE = "sentiment_extractor.md"

    def __init__(self, model: str = None, version: str = "v1"):
        from src.agents.base import DEFAULT_MODEL
        super().__init__(model=model or DEFAULT_MODEL)
        if version == "v2":
            self.NAME = "sentiment_extractor_v2"
            self.PROMPT_FILE = PROMPT_FILE_V2
            self.version = "v2"
        else:
            self.PROMPT_FILE = PROMPT_FILE_V1
            self.version = "v1"

    def build_user_message(self, evidence: dict[str, Any]) -> str:
        posts = evidence["posts"]
        intro = (
            "以下是待抽取的貼文批次（JSON 陣列）。"
            if self.version == "v1"
            else "以下是 v1 分類為 unknown 的貼文批次（JSON 陣列）。"
                 "請用 v2 的三層分類系統（Layer 1 → Layer 2 → Layer 3）重新分類。"
        )
        return (
            intro +
            "請按 system prompt 指示對每一則獨立抽取，"
            "回傳等長、順序一致的 JSON 陣列：\n\n"
            + json.dumps(posts, ensure_ascii=False, indent=2)
        )


def fetch_unextracted_posts(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    sources: tuple[str, ...] | None = None,
    agent_version: str = AGENT_VERSION_V1,
) -> list[dict]:
    """Return posts that don't yet have a sentiment row for `agent_version`."""
    sql = """
    SELECT p.id, p.source, p.board, p.author, p.title, p.body, p.published_at
    FROM posts p
    LEFT JOIN sentiment s
      ON s.post_id = p.id AND s.agent_version = ?
    WHERE s.id IS NULL
    """
    params: list[Any] = [agent_version]

    if sources:
        placeholders = ",".join("?" * len(sources))
        sql += f" AND p.source IN ({placeholders})"
        params.extend(sources)

    # Prefilter by body length
    sql += " AND LENGTH(TRIM(p.body)) >= ?"
    params.append(MIN_PUSH_LEN)

    sql += " ORDER BY p.id"
    if limit:
        sql += f" LIMIT {limit}"

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return [
        {
            "post_id": r["id"],
            "source": r["source"],
            "board": r["board"],
            "author": r["author"],
            "title": r["title"],
            "body": r["body"][:800],
            "published_at": r["published_at"],
        }
        for r in rows
        if not (r["source"] == "ptt_push" and len(r["body"].strip()) < MIN_PUSH_LEN)
    ]


def fetch_v1_unknown_posts(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Return posts that v1 classified as unknown demographic AND haven't been
    re-processed by v2 yet. Used by v2 re-extraction."""
    sql = """
    SELECT p.id, p.source, p.board, p.author, p.title, p.body, p.published_at
    FROM posts p
    JOIN sentiment s1
      ON s1.post_id = p.id
      AND s1.agent_version = ?
      AND (s1.demographic IS NULL OR s1.demographic = 'unknown' OR TRIM(s1.demographic) = '')
    LEFT JOIN sentiment s2
      ON s2.post_id = p.id AND s2.agent_version = ?
    WHERE s2.id IS NULL
    ORDER BY p.id
    """
    params: list[Any] = [AGENT_VERSION_V1, AGENT_VERSION_V2]
    if limit:
        sql += f" LIMIT {limit}"

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return [
        {
            "post_id": r["id"],
            "source": r["source"],
            "board": r["board"],
            "author": r["author"],
            "title": r["title"],
            "body": r["body"][:800],
            "published_at": r["published_at"],
        }
        for r in rows
    ]


def batched(iterable: list, n: int) -> Iterable[list]:
    for i in range(0, len(iterable), n):
        yield iterable[i : i + n]


def extract_all(
    *,
    batch_size: int = 10,
    limit: int | None = None,
    sources: tuple[str, ...] | None = None,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
    version: str = "v1",
) -> tuple[int, int]:
    """Main entry point. Returns (processed_count, failed_count).

    version='v1': processes posts not yet extracted by v1
    version='v2': processes posts that v1 marked unknown and not yet re-run by v2
    """
    agent = SentimentExtractorAgent(model=model, version=version)
    active_version = AGENT_VERSION_V2 if version == "v2" else AGENT_VERSION_V1

    with store.connect() as conn:
        if version == "v2":
            posts = fetch_v1_unknown_posts(conn, limit=limit)
        else:
            posts = fetch_unextracted_posts(conn, limit=limit, sources=sources,
                                             agent_version=active_version)
        if not posts:
            print("No unextracted posts. Nothing to do.")
            return 0, 0

        print(f"Extracting sentiment (version={version}) from {len(posts)} posts "
              f"(batch_size={batch_size}, model={model})")

        # Try to use tqdm if installed
        try:
            from tqdm import tqdm
            batches = list(batched(posts, batch_size))
            iterator = tqdm(batches, desc="batches")
        except ImportError:
            iterator = batched(posts, batch_size)

        processed = 0
        failed = 0

        for batch in iterator:
            evidence = {"posts": batch}
            subject_id = f"batch_{batch[0]['post_id']}_to_{batch[-1]['post_id']}"

            try:
                run = agent.run(
                    evidence,
                    subject_id=subject_id,
                    dry_run=dry_run,
                    max_tokens=4096,
                )
            except Exception as e:  # noqa: BLE001
                print(f"[LLM FAIL] batch starting post {batch[0]['post_id']}: {e}",
                      file=sys.stderr)
                failed += len(batch)
                continue

            if dry_run:
                processed += len(batch)
                continue

            try:
                results = json.loads(_clean_json(run.response or "[]"))
            except json.JSONDecodeError as e:
                print(f"[PARSE FAIL] batch {subject_id}: {e}\n"
                      f"  response (first 300 chars): {(run.response or '')[:300]}",
                      file=sys.stderr)
                failed += len(batch)
                continue

            if not isinstance(results, list) or len(results) != len(batch):
                print(f"[SHAPE FAIL] batch {subject_id}: "
                      f"expected list of {len(batch)}, got {type(results).__name__} "
                      f"of len {len(results) if hasattr(results, '__len__') else 'n/a'}",
                      file=sys.stderr)
                failed += len(batch)
                continue

            # Insert sentiment rows
            for post, result in zip(batch, results):
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO sentiment
                          (post_id, agent_version, demographic, demographic_reason,
                           stance, stance_reason, emotion, topics, quote)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            post["post_id"],
                            active_version,
                            result.get("demographic"),
                            result.get("demographic_reason"),
                            result.get("stance"),
                            result.get("stance_reason"),
                            result.get("emotion"),
                            ",".join(result.get("topics", [])),
                            result.get("quote"),
                        ),
                    )
                    processed += 1
                except Exception as e:  # noqa: BLE001
                    print(f"[INSERT FAIL] post {post['post_id']}: {e}",
                          file=sys.stderr)
                    failed += 1

            conn.commit()

        return processed, failed
