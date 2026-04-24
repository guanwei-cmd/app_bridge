"""PTT web scraper (web.ptt.cc).

設計決定：
- **每則推噓文 = 獨立 post row**（source_id = `{article_aid}#push_{N}`）。
  這樣 sentiment_extractor 的粒度才夠細，才能做「機車族 vs 汽車族」
  分布。
- 18 禁版（如 HatePolitics、Gossiping）用 `over18=1` cookie 繞過。
- 時間格式 PTT 原文是 `Wed Apr 20 14:23:01 2026`，轉 ISO 8601 存。

Usage:
    python -m src.data.scrapers.ptt --board HatePolitics --keyword 淡江大橋 --pages 3
    python -m src.data.scrapers.ptt --board biker --keyword 淡江大橋 --pages 2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from src.data import store
from src.data.scrapers.base import fetch

PTT_BASE = "https://www.ptt.cc"
OVER18_COOKIE = {"over18": "1"}

# PTT meta line labels
META_AUTHOR = "作者"
META_BOARD = "看板"
META_TITLE = "標題"
META_TIME = "時間"


def search_url(board: str, keyword: str, page: int = 1) -> str:
    return f"{PTT_BASE}/bbs/{board}/search?q={quote(keyword)}&page={page}"


def parse_search_page(html: str) -> list[str]:
    """Return list of article URLs (absolute) from a PTT search page."""
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for ent in soup.select("div.r-ent"):
        a = ent.select_one("div.title a")
        if not a or not a.get("href"):
            continue
        urls.append(urljoin(PTT_BASE, a["href"]))
    return urls


def _parse_ptt_time(raw: str) -> str | None:
    """`Wed Apr 20 14:23:01 2026` → ISO 8601 string."""
    raw = raw.strip()
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b  %d %H:%M:%S %Y"):
        try:
            return datetime.strptime(raw, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


PUSH_TAG_MAP = {"推": "push", "噓": "boo", "→": "arrow"}


def parse_article(html: str, url: str) -> dict:
    """Extract main post + pushes from a PTT article page.

    Returns:
        {
            "aid": "M.xxxxx.A.yyy",
            "author": "...",
            "title": "...",
            "published_at": "2026-04-20T...",
            "body": "本文內容（不含推噓）",
            "pushes": [
                {"tag": "push", "user": "abc", "content": "樓主加油", "ts": "04/20 14:25"},
                ...
            ]
        }
    """
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("#main-content")
    if main is None:
        return {}

    # --- meta lines (top of article) ---
    meta = {}
    for tag in main.select(".article-metaline, .article-metaline-right"):
        label = tag.select_one(".article-meta-tag")
        val = tag.select_one(".article-meta-value")
        if label and val:
            meta[label.get_text(strip=True)] = val.get_text(strip=True)
        tag.decompose()  # remove so they don't pollute body

    # --- pushes (strip from main before extracting body) ---
    pushes = []
    for push in main.select("div.push"):
        tag = push.select_one(".push-tag")
        user = push.select_one(".push-userid")
        content = push.select_one(".push-content")
        ts = push.select_one(".push-ipdatetime")
        if not (tag and user and content):
            push.decompose()
            continue
        raw_tag = tag.get_text(strip=True)
        pushes.append(
            {
                "tag": PUSH_TAG_MAP.get(raw_tag, "unknown"),
                "user": user.get_text(strip=True),
                "content": content.get_text(strip=True).lstrip(":").strip(),
                "ts": ts.get_text(strip=True) if ts else "",
            }
        )
        push.decompose()

    # --- body (remaining text in main) ---
    body_text = main.get_text(separator="\n", strip=True)
    # strip the signature / IP line that PTT appends
    body_text = re.sub(r"※ 發信站:.*$", "", body_text, flags=re.S).strip()

    # aid from URL
    aid_match = re.search(r"/([MG]\.[0-9A-Za-z.]+)\.html", url)
    aid = aid_match.group(1) if aid_match else url

    return {
        "aid": aid,
        "author": meta.get(META_AUTHOR, "").split("(")[0].strip(),
        "title": meta.get(META_TITLE, ""),
        "published_at": _parse_ptt_time(meta.get(META_TIME, "")),
        "body": body_text,
        "pushes": pushes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PTT scraper.")
    parser.add_argument("--board", required=True)
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write DB")
    parser.add_argument("--skip-pushes", action="store_true", help="Don't store pushes as separate posts")
    args = parser.parse_args()

    all_article_urls: list[str] = []
    for page in range(1, args.pages + 1):
        url = search_url(args.board, args.keyword, page)
        print(f"[search] page {page}: {url}")
        try:
            r = fetch(url, cookies=OVER18_COOKIE)
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] {e}", file=sys.stderr)
            continue
        links = parse_search_page(r.text)
        print(f"  → {len(links)} articles on this page")
        all_article_urls.extend(links)

    # Dedupe while preserving order
    seen = set()
    article_urls = [u for u in all_article_urls if not (u in seen or seen.add(u))]
    print(f"\n[articles] {len(article_urls)} unique article URLs to fetch\n")

    ok_articles = 0
    ok_pushes = 0
    fail = 0

    with store.connect() as conn:
        for aurl in article_urls:
            try:
                r = fetch(aurl, cookies=OVER18_COOKIE)
                parsed = parse_article(r.text, aurl)
                if not parsed or not parsed.get("body"):
                    print(f"  [empty] {aurl}", file=sys.stderr)
                    fail += 1
                    continue

                if args.dry_run:
                    print(
                        f"  [ok] {parsed['title'][:40]} — "
                        f"{len(parsed['pushes'])} pushes"
                    )
                    ok_articles += 1
                    ok_pushes += len(parsed["pushes"])
                    continue

                # Store article
                store.upsert_post(
                    conn,
                    source="ptt",
                    source_id=parsed["aid"],
                    title=parsed["title"],
                    body=parsed["body"],
                    url=aurl,
                    board=args.board,
                    author=parsed["author"],
                    published_at=parsed["published_at"],
                    raw_json=json.dumps(
                        {"push_count": len(parsed["pushes"])}, ensure_ascii=False
                    ),
                )
                ok_articles += 1

                # Store each push as an independent post
                if not args.skip_pushes:
                    for idx, p in enumerate(parsed["pushes"]):
                        if not p["content"]:
                            continue
                        store.upsert_post(
                            conn,
                            source="ptt_push",
                            source_id=f"{parsed['aid']}#push_{idx}",
                            title=None,
                            body=p["content"],
                            url=aurl,
                            board=args.board,
                            author=p["user"],
                            published_at=None,  # PTT push 只有 mm/dd HH:MM，不含年
                            raw_json=json.dumps(
                                {"tag": p["tag"], "parent_aid": parsed["aid"]},
                                ensure_ascii=False,
                            ),
                        )
                        ok_pushes += 1

                print(
                    f"  [saved] {parsed['title'][:40]} + {len(parsed['pushes'])} pushes"
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [FAIL] {aurl}: {e}", file=sys.stderr)
                fail += 1

    print(
        f"\nDone. articles_ok={ok_articles}  pushes_ok={ok_pushes}  fail={fail}"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
