"""Quick stats on the sentiment table — run after sentiment_extractor finishes.

Usage:
    python -m scripts.stats
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from src.data import store  # noqa: E402


def main() -> int:
    with store.connect() as conn:
        # Total counts
        total_posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        total_sentiment = conn.execute("SELECT COUNT(*) FROM sentiment").fetchone()[0]
        print(f"Posts total: {total_posts}")
        print(f"Sentiment total: {total_sentiment}  ({total_sentiment/max(total_posts,1)*100:.1f}% coverage)")

        # By source
        print("\n=== 貼文來源分布 ===")
        for row in conn.execute(
            "SELECT source, COUNT(*) FROM posts GROUP BY source ORDER BY COUNT(*) DESC"
        ):
            print(f"  {row[0]:<12} {row[1]:>5}")

        # Demographic
        print("\n=== 族群分布（9-code）===")
        for row in conn.execute("""
            SELECT demographic, COUNT(*) as n
            FROM sentiment
            GROUP BY demographic
            ORDER BY n DESC
        """):
            print(f"  {(row[0] or 'NULL'):<20} {row[1]:>5}")

        # Stance
        print("\n=== 立場分布 ===")
        for row in conn.execute("""
            SELECT stance, COUNT(*) as n
            FROM sentiment GROUP BY stance ORDER BY n DESC
        """):
            print(f"  {(row[0] or 'NULL'):<15} {row[1]:>5}")

        # Emotion
        print("\n=== 情緒分布 ===")
        for row in conn.execute("""
            SELECT emotion, COUNT(*) as n
            FROM sentiment GROUP BY emotion ORDER BY n DESC
        """):
            print(f"  {(row[0] or 'NULL'):<15} {row[1]:>5}")

        # Cross: demographic × stance
        print("\n=== 族群 × 立場（heatmap 資料）===")
        print(f"  {'demographic':<18} {'support':>8} {'critical':>9} {'neutral':>8} {'ambi':>6}")
        for row in conn.execute("""
            SELECT
              demographic,
              SUM(CASE WHEN stance='support' THEN 1 ELSE 0 END) AS support,
              SUM(CASE WHEN stance='critical' THEN 1 ELSE 0 END) AS critical,
              SUM(CASE WHEN stance='neutral' THEN 1 ELSE 0 END) AS neutral,
              SUM(CASE WHEN stance='ambivalent' THEN 1 ELSE 0 END) AS ambi
            FROM sentiment
            GROUP BY demographic
            ORDER BY support+critical+neutral+ambi DESC
        """):
            print(f"  {(row[0] or 'NULL'):<18} "
                  f"{row[1]:>8} {row[2]:>9} {row[3]:>8} {row[4]:>6}")

        # Top topics (topics is CSV string)
        print("\n=== Top 主題（split CSV）===")
        topic_counts: dict[str, int] = {}
        for row in conn.execute("SELECT topics FROM sentiment WHERE topics IS NOT NULL"):
            for t in (row[0] or "").split(","):
                t = t.strip()
                if t:
                    topic_counts[t] = topic_counts.get(t, 0) + 1
        for t, n in sorted(topic_counts.items(), key=lambda x: -x[1])[:15]:
            print(f"  {t:<25} {n:>5}")

        # Sample quotes by stance
        print("\n=== 樣本引用（每類 stance 取 3 筆）===")
        for stance in ("critical", "support", "ambivalent"):
            print(f"\n  --- {stance} ---")
            for row in conn.execute("""
                SELECT s.quote, s.demographic, p.source, p.board
                FROM sentiment s JOIN posts p ON p.id = s.post_id
                WHERE s.stance = ? AND s.quote IS NOT NULL AND LENGTH(s.quote) > 10
                ORDER BY RANDOM() LIMIT 3
            """, (stance,)):
                print(f"    [{row[1]}, {row[2]}/{row[3] or ''}] {row[0][:80]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
