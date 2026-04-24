# bridge-analyst

> Issue exploration + sentiment analysis agent for the 淡江大橋 case —
> built as the second application of the `district-analyst` methodology.

題目：針對淡江大橋做議題探索與輿情分析，在報告書中說明
AI 設計思路、並對時代力量的立場與下一步提出建議。

**賣點不是題目本身，是方法**：這套 pipeline 能處理選區分析，
換一份種子資料就處理公共議題；未來換一個橋、換一條法案都能再跑一次。

---

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                  人工事實包（data/facts/*.md）         │
│  timeline · budget · stakeholders · motorcycle_lane    │
│  political_landscape · npp_framing_candidates          │
└──────────────────────────┬─────────────────────────────┘
                           │ 先有事實、才有推理
                           ▼
  ┌────────────────────┐  ┌────────────────────────────┐
  │  Scraper 層        │  │ Agent 層（推理）           │
  │  news / PTT / YT   │  │ 1. issue_landscape         │
  │  → SQLite posts     │─▶│ 2. sentiment_extractor    │
  │                    │  │    (extraction only — 不推理) │
  └────────────────────┘  │ 3. strategic_dialectic     │
                          │    (辯證 NPP framings)     │
                          └──────────────┬─────────────┘
                                         │
                                         ▼
                        ┌──────────────────────────────┐
                        │  輸出                        │
                        │  ├─ HTML dashboard (Plotly)  │
                        │  ├─ PDF 報告                 │
                        │  └─ 每次 run 的 trace        │
                        │     (prompt + evidence +     │
                        │      response)               │
                        └──────────────────────────────┘
```

關鍵設計原則（**不可違背**，詳見 `CLAUDE.md`）：

1. **Extraction ≠ Reasoning**：`sentiment_extractor` 只抽事實（分類 / 情緒 /
   引用），**不做判斷**。判斷留給 `strategic_dialectic`。分層讓每一步都可審查。
2. **Prompts as first-class artifacts**：放在 `src/agents/prompts/*.md`，
   非工程師也能改，git 版控。
3. **辯證式推理**：所有戰略推理 agent 都用「假設 → 反證 → 判斷 + 信心等級」結構。
4. **每次 run 留 trace**：`data/traces/` 存完整 evidence + prompt + response。
5. **差異化輸出就是品質檢核**：同一份事實包給不同 framing（A/B/C/D），
   若輸出大同小異 → 推理失效。

---

## Quickstart

```bash
# 1. 建 venv + 裝依賴
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 設定 API key
cp .env.example .env
# 編輯 .env 填入 ANTHROPIC_API_KEY

# 3. 建 SQLite schema、載入事實
python -m scripts.init_db

# 4. 抓資料（Day 2 — 先新聞、後 PTT）
python -m src.data.scrapers.news --seed data/sources/seed_urls.json
python -m src.data.scrapers.ptt --board HatePolitics --keyword 淡江大橋 --pages 5
python -m src.data.scrapers.ptt --board biker        --keyword 淡江大橋 --pages 3
python -m src.data.scrapers.ptt --board Gossiping    --keyword 淡江大橋 --pages 3

# 5. 跑 agent pipeline
python -m scripts.run_agent --agent issue_landscape       # 議題地景
python -m scripts.run_agent --agent sentiment_extractor   # 逐貼文抽特徵
python -m scripts.run_agent --agent strategic_dialectic   # NPP framing 辯證

# 6. 產出 dashboard + 報告
python -m scripts.build_dashboard     # → reports/dashboard.html
python -m scripts.build_report        # → reports/bridge_report.pdf
```

---

## 目錄

```
bridge-analyst/
├── CLAUDE.md              # agent 進入點（包含最新事實 cutoff）
├── README.md              # 本檔（人類進入點）
├── requirements.txt
├── .env.example
├── .vscode/               # launch configs (F5 跑 pipeline)
│   ├── settings.json
│   └── launch.json
│
├── data/
│   ├── facts/             # 人工驗證的事實包（用於 agent evidence）
│   │   ├── timeline.md
│   │   ├── budget_history.md
│   │   ├── stakeholders.md
│   │   ├── motorcycle_lane.md
│   │   ├── political_landscape.md
│   │   └── npp_framing_candidates.md
│   ├── sources/
│   │   └── seed_urls.json  # scraper 種子（18 新聞 + 8 PTT 版 + 3 公文）
│   ├── raw/                # scraper 原始輸出（gitignored）
│   ├── traces/             # agent run trace（human-reviewable）
│   └── bridge.sqlite       # SQLite 主庫（gitignored）
│
├── src/
│   ├── agents/
│   │   ├── base.py         # BaseAgent + run trace 機制
│   │   ├── issue_landscape.py
│   │   ├── sentiment_extractor.py
│   │   ├── strategic_dialectic.py
│   │   └── prompts/        # first-class artifacts
│   │       ├── issue_landscape.md
│   │       ├── sentiment_extractor.md
│   │       └── strategic_dialectic.md
│   └── data/
│       ├── store.py        # SQLite DAO
│       └── scrapers/
│           ├── base.py
│           ├── news.py
│           └── ptt.py
│
├── scripts/
│   ├── init_db.py          # 建 schema + 載入 facts/*.md
│   ├── run_agent.py        # 跑單一 agent（CLI 入口）
│   ├── build_dashboard.py  # Plotly HTML
│   └── build_report.py     # PDF 報告
│
├── reports/                # 最終輸出（gitignored）
└── tests/
    └── ...
```

---

## 開發流程（10-day plan）

| Day | 目標 | Status |
|-----|------|--------|
| 1 | CLAUDE.md + 事實包 + VSCode env |  |
| 2 | scraper：news + PTT |  |
| 3 | DB schema + sentiment_extractor agent（extraction-only） |  |
| 4 | issue_landscape agent |  |
| 5 | 族群／立場 sentiment 分析跑完資料 |  |
| 6 | NPP 歷史主張蒐集（`data/facts/npp_positions.md`） |  |
| 7 | strategic_dialectic agent + A/B/C/D framing 驗證 |  |
| 8 | HTML dashboard |  |
| 9 | PDF 報告 |  |
| 10 | 整體重跑 + trace 清理 + README 潤飾 |  |

---

## Status



詳細設計理念與政治敏感性考量見 `CLAUDE.md`。
