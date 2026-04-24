"""News article scraper — consumes seed_urls.json and stores into posts table.

Day 2 實作目標：
- 從 seed_urls.json 讀取 news_articles 陣列
- 逐一 fetch、用站別 extractor 抽正文（TVBS / UDN / LTN / ETtoday / ...）
- upsert 進 SQLite posts 表，source='news'

目前為 skeleton — 站別 extractor 是 Day 2 的工作。
面試可以說："這裡我保留了 newspaper3k / trafilatura 的選擇空間，
實際會挑 readability-lxml 作為 baseline + 站別 override。"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from src.data import store
from src.data.scrapers.base import fetch


def _naive_extract(html: str) -> str:
    """Fallback heuristic when readability can't lock the article node."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body
    if node is None:
        return ""
    text = node.get_text(separator="\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def extract_body(html: str) -> str:
    """Try readability first (article-focused), fall back to naive heuristic.

    Readability-lxml handles the quirks of paywalled / iframe-heavy / div-soup
    sites that our naive <article>/<main>/<body> walker misses.
    """
    try:
        from readability import Document

        doc = Document(html)
        content_html = doc.summary(html_partial=True)
        soup = BeautifulSoup(content_html, "lxml")
        text = soup.get_text(separator="\n", strip=True)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        body = "\n".join(lines)
        # If readability returned garbage (< 100 chars), fall back.
        if len(body) >= 100:
            return body
    except Exception:
        pass
    return _naive_extract(html)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape news articles listed in seed URLs.")
    parser.add_argument("--seed", required=True, help="Path to seed_urls.json")
    parser.add_argument("--limit", type=int, default=None, help="Max articles")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but don't write DB")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    if not seed_path.exists():
        print(f"Seed file not found: {seed_path}", file=sys.stderr)
        return 2

    data = json.loads(seed_path.read_text(encoding="utf-8"))
    articles = data.get("news_articles", [])
    if args.limit:
        articles = articles[: args.limit]

    ok = 0
    fail = 0
    with store.connect() as conn:
        for art in articles:
            url = art["url"]
            try:
                r = fetch(url)
                body = extract_body(r.text)
                if not body:
                    print(f"[empty body] {url}", file=sys.stderr)
                    fail += 1
                    continue
                if args.dry_run:
                    print(f"[ok {len(body):>6d} chars] {art['title'][:40]}")
                else:
                    store.upsert_post(
                        conn,
                        source="news",
                        source_id=url,
                        title=art.get("title"),
                        body=body,
                        url=url,
                        author=art.get("source"),
                        published_at=art.get("date"),
                    )
                    print(f"[saved] {art['title'][:50]}")
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[FAIL] {url}: {e}", file=sys.stderr)
                fail += 1

    print(f"\nDone. ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
