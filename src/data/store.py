"""SQLite data access — 輿情 posts + agent traces pointer.

Schema 設計原則：
1. **一張 `posts` 表存所有爬到的帖**（新聞、PTT、YT 留言一視同仁），
   用 `source` 欄位分流。便於跨源查詢「各源對機車道的情緒分布」。
2. **`sentiment` 獨立一張表，FK 回 posts.id**。同一則貼文可由不同
   agent 版本標注多次（`agent_version` 區分）— 未來若要 A/B 兩版
   prompt 對照，schema 已支援。
3. **族群分類跟 `stakeholders.md` 的 9 code 對齊**。
4. **`traces` 表只存指針（檔名），實際 markdown 落地到 data/traces/**。
   這樣 trace 體積不撐爆 DB，但查詢時能 join 回去。

DB 路徑：`BRIDGE_DB_PATH` env var；預設 `data/bridge.sqlite`。
在 FUSE 掛載環境 (cowork sandbox) 改走 `/tmp/bridge-analyst/bridge.sqlite`。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,          -- 'news' | 'ptt' | 'youtube' | 'official'
    source_id   TEXT NOT NULL,          -- URL 或 PTT aid，unique per source
    board       TEXT,                   -- PTT 版名，其他源為 NULL
    author      TEXT,                   -- 作者名／媒體名
    title       TEXT,
    body        TEXT NOT NULL,
    published_at TEXT,                  -- ISO 8601；缺值時為 NULL
    url         TEXT,
    scraped_at  TEXT NOT NULL DEFAULT (datetime('now')),
    raw_json    TEXT,                   -- 備份原始回應（debug 用）
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source);
CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published_at);
CREATE INDEX IF NOT EXISTS idx_posts_board ON posts(board);

CREATE TABLE IF NOT EXISTS sentiment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    agent_version   TEXT NOT NULL,                      -- 'sentiment_extractor@v1'
    demographic     TEXT,                               -- 9-code stakeholder group
    demographic_reason TEXT,                            -- 一句話推理
    stance          TEXT,                               -- 'support' | 'critical' | 'neutral' | 'ambivalent'
    stance_reason   TEXT,
    emotion         TEXT,                               -- 'anger' | 'fear' | 'hope' | 'resignation' | 'sarcasm' | 'neutral'
    topics          TEXT,                               -- CSV: motorcycle_lane,budget,tpp_criticism
    quote           TEXT,                               -- 最有代表性的一句引用
    extracted_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(post_id, agent_version)
);

CREATE INDEX IF NOT EXISTS idx_sentiment_post ON sentiment(post_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_stance ON sentiment(stance);
CREATE INDEX IF NOT EXISTS idx_sentiment_demographic ON sentiment(demographic);

CREATE TABLE IF NOT EXISTS traces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name  TEXT NOT NULL,
    subject_id  TEXT NOT NULL,
    file_path   TEXT NOT NULL,       -- 相對於 repo root
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    model       TEXT
);
"""


def resolve_db_path() -> Path:
    """Return the DB path, respecting $BRIDGE_DB_PATH and FUSE workaround."""
    explicit = os.environ.get("BRIDGE_DB_PATH")
    if explicit:
        return Path(explicit)
    # Default: local dev path
    return Path("data/bridge.sqlite")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection with FK + row_factory set up."""
    path = db_path or resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


# --- insert helpers --------------------------------------------------

def upsert_post(
    conn: sqlite3.Connection,
    *,
    source: str,
    source_id: str,
    title: str | None,
    body: str,
    url: str | None = None,
    board: str | None = None,
    author: str | None = None,
    published_at: str | None = None,
    raw_json: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO posts(source, source_id, board, author, title, body,
                          published_at, url, raw_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            body=excluded.body,
            title=COALESCE(excluded.title, posts.title),
            published_at=COALESCE(excluded.published_at, posts.published_at)
        RETURNING id
        """,
        (source, source_id, board, author, title, body, published_at, url, raw_json),
    )
    row = cur.fetchone()
    return int(row[0])


def record_trace(
    conn: sqlite3.Connection,
    *,
    agent_name: str,
    subject_id: str,
    file_path: str,
    input_tokens: int | None,
    output_tokens: int | None,
    model: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO traces(agent_name, subject_id, file_path,
                           input_tokens, output_tokens, model)
        VALUES(?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (agent_name, subject_id, file_path, input_tokens, output_tokens, model),
    )
    row = cur.fetchone()
    return int(row[0])
