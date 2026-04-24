# bridge-analyst

淡江大橋議題分析 pipeline — 為時代力量秘書長辦公室提案之政策分析工具。

> 面試題目：針對淡江大橋做議題探索與輿情分析，在報告書中說明 AI 設計思路、
> 並對時代力量的立場與下一步提出建議。

本專案之產出不僅為單一議題之分析報告，更為一套可複用於其他議題之方法論框架。
本案為同方法論繼「新竹縣議員選區分析」之第二次應用。

---

## 快速導覽

交付物位置：

| 交付物 | 路徑 | 說明 |
|--------|------|------|
| **主報告 PDF** | `submissions/bridge_analyst_v1_20260424.pdf` | 凍結版交件檔，含執行摘要與五章完整報告 |
| **報告章節草稿** | `reports/ch00_summary.md` ~ `reports/ch05_draft.md` | 可編輯之 markdown 草稿 |
| **輿情互動 dashboard** | `reports/dashboard.html` | 瀏覽器打開之互動式視覺化 |
| **AI 推理 trace** | `data/traces/*.md` | 每次 agent 執行之完整 evidence + prompt + response |
| **事實包** | `data/facts/*.md` | 人工驗證之事實基礎，供 agent 推理引用 |

---

## 系統架構

```
┌────────────────────────────────────────────────────────┐
│                事實包層（data/facts/*.md）             │
│  timeline · budget · stakeholders · motorcycle_lane    │
│  political_landscape · npp_positions                   │
└──────────────────────────┬─────────────────────────────┘
                           │ 先有事實、才有推理
                           ▼
  ┌────────────────────┐  ┌────────────────────────────┐
  │  抽取層 Scrapers    │  │ 推理層 Agents               │
  │  news / PTT        │  │ 1. issue_landscape         │
  │  → SQLite posts    │─▶│ 2. sentiment_extractor     │
  │                    │  │    （抽取層，不推理）        │
  └────────────────────┘  │ 3. strategic_dialectic     │
                          │    （戰略辯證）              │
                          │ 4. npp_position_research   │
                          │    （歷史語料補課）          │
                          └──────────────┬─────────────┘
                                         │
                                         ▼
                        ┌──────────────────────────────┐
                        │  輸出                         │
                        │  ├─ HTML dashboard（Plotly） │
                        │  ├─ Word / PDF 報告           │
                        │  └─ 每次 run 之 trace         │
                        └──────────────────────────────┘
```

---

## 五項核心設計原則

1. **抽取 ≠ 推理**：`sentiment_extractor` 僅抽取特徵（族群、情緒、引用），
   不做戰略判斷。判斷留給 `strategic_dialectic`。分層使每一步可審查。
2. **Prompt 為 first-class artifact**：`src/agents/prompts/*.md` 為 markdown 檔，
   非工程人員可直接編輯，版本控制可追溯。
3. **辯證式推理**：所有戰略 agent 採「假設 → 反證 → 判斷 + 信心等級」結構，
   強制列出反駁證據以對抗模型之合理化傾向。
4. **每次 run 留 trace**：`data/traces/` 存完整 evidence + prompt + response，
   供後續複查與重跑。
5. **Ensemble 交叉驗證**：同一戰略命題執行多次獨立推理，穩定項列為高信心建議，
   變動項列為需人類決策者介入之判斷。

詳細設計理念見報告 Ch.3 與 `CLAUDE.md`。

---

## Quickstart

### 環境建置

```bash
# 1. 建 venv（需 Python 3.10+）
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. 設定 API key
cp .env.example .env
# 編輯 .env 填入 ANTHROPIC_API_KEY

# 3. 建 SQLite schema + 驗證事實包
python -m scripts.init_db
```

### 執行 pipeline

```bash
# 4. 抓資料（新聞 + PTT）
python -m src.data.scrapers.news --seed data/sources/seed_urls.json
python -m src.data.scrapers.ptt --board biker        --keyword 淡江大橋 --pages 3
python -m src.data.scrapers.ptt --board HatePolitics --keyword 淡江大橋 --pages 3
python -m src.data.scrapers.ptt --board Gossiping    --keyword 淡江大橋 --pages 3

# 5. 執行 agent 推理鏈
python -m scripts.run_agent --agent issue_landscape       # 議題地景
python -m scripts.run_agent --agent sentiment_extractor   # 逐貼文抽取
python -m scripts.run_agent --agent sentiment_extractor --version v2   # Unknown 細分
python -m scripts.run_agent --agent strategic_dialectic   # 戰略辯證

# 6. 產出視覺化與報告
python -m scripts.build_dashboard   # → reports/dashboard.html
python -m scripts.build_docx        # → reports/bridge_report.docx
```

### 快速檢視 trace

```bash
# 查看統計摘要
python -m scripts.stats

# 閱讀 issue_landscape 最新執行 trace
ls -t data/traces/issue_landscape_*.md | head -1 | xargs cat
```

---

## 目錄結構

```
bridge-analyst/
├── CLAUDE.md                        # 專案核心文件（方法論 + 事實 cutoff）
├── README.md                        # 本檔
├── requirements.txt
├── .env.example                     # API key 範本
├── .vscode/                         # VSCode F5 一鍵執行設定
│
├── submissions/                     # 凍結版交件檔
│   └── bridge_analyst_v1_20260424.pdf
│
├── reports/                         # 報告草稿與建置暫存
│   ├── ch00_summary.md              # 一頁式執行摘要
│   ├── ch01_draft.md                # 議題地景
│   ├── ch02_draft.md                # 輿情結構
│   ├── ch03_draft.md                # AI 設計思路
│   ├── ch04_draft.md                # 時代力量的切入機會
│   ├── ch05_draft.md                # 展望 2026-05-12 之後
│   └── dashboard.html               # Plotly 互動式 dashboard
│
├── data/
│   ├── facts/                       # 人工驗證事實包
│   │   ├── timeline.md
│   │   ├── budget_history.md
│   │   ├── stakeholders.md          # 9-code 族群 schema（含設計聲明）
│   │   ├── motorcycle_lane.md
│   │   ├── political_landscape.md
│   │   ├── npp_framing_candidates.md
│   │   └── npp_positions.md         # 時代力量歷史主張語料
│   ├── sources/
│   │   └── seed_urls.json           # scraper 種子 URL
│   ├── traces/                      # agent run trace（gitignored）
│   └── bridge.sqlite                # 主資料庫（gitignored）
│
├── src/
│   ├── agents/
│   │   ├── base.py                  # BaseAgent + trace 機制
│   │   ├── sentiment_extractor.py
│   │   └── prompts/                 # first-class prompt artifacts
│   │       ├── issue_landscape.md
│   │       ├── sentiment_extractor.md
│   │       ├── sentiment_extractor_v2.md
│   │       └── strategic_dialectic.md
│   └── data/
│       ├── store.py                 # SQLite DAO
│       └── scrapers/                # news / PTT 抓取
│
├── scripts/
│   ├── init_db.py                   # 建 schema
│   ├── run_agent.py                 # agent CLI 入口
│   ├── stats.py                     # 統計摘要
│   ├── build_dashboard.py           # 產出 Plotly HTML
│   ├── build_docx.py                # 產出 Word 報告
│   ├── build_pdf.py                 # 產出 PDF（備用）
│   └── deploy_surge.sh              # 部署 dashboard 至 Surge
│
└── tests/
    └── test_smoke.py                # 基本 pipeline 測試
```

---

## 方法論亮點

本系統於本次分析中展示下列可複用之方法論特性：

**一、抽取層與推理層刻意分離**
除錯時可快速定位錯誤於資料層或推理層。

**二、三輪 ensemble 交叉驗證**
對同一戰略命題執行三次獨立推理，比對穩定與變動部分。本次分析發現
「制度改革立法」為首選建議於三輪皆穩定，而「決策審計」之戰略定位於三輪分歧。
此分層使決策者得以區分高信心建議與需人類判斷之選項。

**三、自我修正循環（self-correction loop）**
本次分析於第三輪推理前，執行歷史語料補課 agent 發現事實包之一項錯誤假設
（原誤以為時代力量未對本議題表態），即時修正事實包並重新執行推理。
此錯誤並非系統之缺陷，而是其修正機制之證明。

詳細方法論論述見報告 Ch.3。

---

## 資料 cutoff 與限制揭露

- 所有資料截至 **2026-04-22 / 2026-04-24**
- 輿情樣本 **1030 筆**，其中 97% 來自 PTT
- 淡水/八里在地居民樣本僅 4 筆（0.4%）—— 「在地民意」推論須輔以線下訪談
- 詳細限制揭露見報告 Ch.3.7

---

## 聯絡

- 報告作者：dorian（丁冠維）
- 聯絡信箱：sdfyou0088@gmail.com
- 本 repo：https://github.com/guanwei-cmd/app_bridge
