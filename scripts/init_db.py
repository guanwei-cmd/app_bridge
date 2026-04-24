"""Initialize SQLite schema + register fact files.

Run:
    python -m scripts.init_db

What it does:
1. Creates the schema defined in src/data/store.py
2. Prints a sanity-check summary (tables, row counts, facts files)

Does NOT load scrape data — that's done by the Day 2 scrapers.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow "python -m scripts.init_db" from repo root without installing
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from src.data import store  # noqa: E402


FACTS_DIR = REPO_ROOT / "data" / "facts"
EXPECTED_FACTS = [
    "timeline.md",
    "budget_history.md",
    "stakeholders.md",
    "motorcycle_lane.md",
    "political_landscape.md",
    "npp_framing_candidates.md",
]


def main() -> int:
    db_path = store.resolve_db_path()
    print(f"Initializing SQLite at: {db_path}")
    store.init_schema(db_path)

    with store.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        tables = [r[0] for r in rows]
        print(f"  tables: {tables}")

        for t in ("posts", "sentiment", "traces"):
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"    {t}: {count} rows")

    print("\nFact files check:")
    missing = []
    for name in EXPECTED_FACTS:
        p = FACTS_DIR / name
        status = "OK " if p.exists() else "MISS"
        size = f"{p.stat().st_size:>6d} B" if p.exists() else "  — "
        print(f"  [{status}] {size}  {name}")
        if not p.exists():
            missing.append(name)

    if missing:
        print(f"\nWARN: missing {len(missing)} fact file(s): {missing}")
        return 1

    print("\nDone. Next: scrape data (src.data.scrapers.news / ptt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
