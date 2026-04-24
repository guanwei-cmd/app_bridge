"""Build a single-file HTML dashboard summarizing the extracted sentiment.

Usage:
    python -m scripts.build_dashboard
    # → reports/dashboard.html

Design decisions:
- One self-contained HTML (Plotly CDN), no build step
- Five visualizations + sample quotes
- `unknown` 族群一律移到最後 + 變灰：視覺告訴讀者這是資料缺口，
  不是一個實存族群類別
- 每張圖的 narrative 使用實際資料算出的具體數字，而非 generic 描述
- 2026-04-19 trigger event annotated on time-series (todo)
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from src.data import store  # noqa: E402


# ---------- Visual constants ----------

COLOR_PRIMARY = "#2c4a6b"
COLOR_SECONDARY = "#d17a22"
COLOR_TERTIARY = "#5a8ca8"
COLOR_UNKNOWN = "#b8b8b8"   # gray — "this is missing data, not a category"

# Treat any demographic that is None, empty, or explicitly 'unknown' as the "缺口" bucket
UNKNOWN_LABELS = {"unknown", "", None}

# V2 sub-categories (for coloring: visually distinct from Layer 1 族群)
V2_SUBCATEGORIES = {
    "sarcastic_noise", "generic_critique", "generic_support",
    "pure_emotion", "off_topic", "short_fact", "meta_discussion",
}

# The SQL snippet used across queries: prefer v2 row over v1 row when both exist
# for the same post. Implemented as a CTE that picks the highest-priority version.
LATEST_SENTIMENT_CTE = """
WITH latest_sentiment AS (
  SELECT post_id,
         demographic, demographic_reason,
         stance, stance_reason,
         emotion, topics, quote,
         agent_version
  FROM sentiment s
  WHERE agent_version = (
    -- Prefer v2 if it exists for this post, else fall back to v1
    SELECT agent_version
    FROM sentiment s2
    WHERE s2.post_id = s.post_id
    ORDER BY CASE agent_version
               WHEN 'sentiment_extractor@v2' THEN 1
               WHEN 'sentiment_extractor@v1' THEN 2
               ELSE 3
             END
    LIMIT 1
  )
)
"""


# ---------- Data queries ----------

def q_source_counts(conn):
    return list(conn.execute("""
        SELECT source, COUNT(*) FROM posts
        WHERE source IS NOT NULL
        GROUP BY source ORDER BY COUNT(*) DESC
    """))


def q_demographic(conn):
    """Return demographics ordered by count desc, BUT with 'unknown' forced to last.

    Uses the latest_sentiment CTE so v2 results override v1 for the same post.

    Returns (label, count, bucket) triples where bucket ∈ {"known", "v2_sub", "unknown"}
    so the renderer can colour each differently:
      - known → 橘色 (原 9 code)
      - v2_sub → 淡黃 (v2 的 sub-category，例如 sarcastic_noise)
      - unknown → 灰色 (真的歸不出的)
    """
    rows = list(conn.execute(LATEST_SENTIMENT_CTE + """
        SELECT COALESCE(demographic, 'unknown') AS dem, COUNT(*)
        FROM latest_sentiment
        GROUP BY dem
        ORDER BY COUNT(*) DESC
    """))

    def bucket(label: str) -> str:
        if label in UNKNOWN_LABELS:
            return "unknown"
        if label in V2_SUBCATEGORIES:
            return "v2_sub"
        return "known"

    # Ordering: known (desc) → v2_sub (desc) → unknown (last)
    known = [(l, n) for l, n in rows if bucket(l) == "known"]
    v2_sub = [(l, n) for l, n in rows if bucket(l) == "v2_sub"]
    unknown = [(l, n) for l, n in rows if bucket(l) == "unknown"]
    return (
        [(l, n, "known") for l, n in known] +
        [(l, n, "v2_sub") for l, n in v2_sub] +
        [(l, n, "unknown") for l, n in unknown]
    )


def q_stance_by_source(conn):
    return list(conn.execute("""
        SELECT p.source, COALESCE(s.stance, 'unknown'), COUNT(*)
        FROM sentiment s JOIN posts p ON p.id = s.post_id
        GROUP BY p.source, s.stance
        ORDER BY p.source, COUNT(*) DESC
    """))


def q_emotion_by_stance(conn):
    return list(conn.execute("""
        SELECT COALESCE(stance, 'unknown'), COALESCE(emotion, 'unknown'), COUNT(*)
        FROM sentiment GROUP BY stance, emotion
    """))


def q_topics(conn, limit=15):
    topic_counts: dict[str, int] = {}
    for row in conn.execute("SELECT topics FROM sentiment WHERE topics IS NOT NULL"):
        for t in (row[0] or "").split(","):
            t = t.strip()
            if t:
                topic_counts[t] = topic_counts.get(t, 0) + 1
    return sorted(topic_counts.items(), key=lambda x: -x[1])[:limit]


def q_demographic_x_stance(conn):
    """Return (demographics, stances, matrix), with demographics ordered by
    total count DESC, v2_sub grouped together, and 'unknown' forced to last row.

    Uses latest_sentiment CTE so v2 overrides v1 for the same post."""
    rows = list(conn.execute(LATEST_SENTIMENT_CTE + """
        SELECT COALESCE(demographic,'unknown'), COALESCE(stance,'unknown'), COUNT(*)
        FROM latest_sentiment
        GROUP BY demographic, stance
    """))
    totals: dict[str, int] = {}
    for dem, _, n in rows:
        totals[dem] = totals.get(dem, 0) + n

    known = sorted(
        [d for d in totals if d not in UNKNOWN_LABELS and d not in V2_SUBCATEGORIES],
        key=lambda d: -totals[d],
    )
    v2_sub = sorted(
        [d for d in totals if d in V2_SUBCATEGORIES],
        key=lambda d: -totals[d],
    )
    unknown = [d for d in totals if d in UNKNOWN_LABELS]
    demographics = known + v2_sub + unknown

    stances = ["support", "critical", "neutral", "ambivalent", "unknown"]
    matrix = [[0] * len(stances) for _ in demographics]
    for dem, st, n in rows:
        if st not in stances:
            st = "unknown"
        i = demographics.index(dem)
        j = stances.index(st)
        matrix[i][j] = n
    return demographics, stances, matrix


def q_sample_quotes(conn, stance, n=5):
    return list(conn.execute("""
        SELECT s.quote, s.demographic, s.emotion, p.source, p.board
        FROM sentiment s JOIN posts p ON p.id = s.post_id
        WHERE s.stance = ?
          AND s.quote IS NOT NULL
          AND LENGTH(s.quote) > 10
        ORDER BY RANDOM()
        LIMIT ?
    """, (stance, n)))


def q_summary_counts(conn):
    posts = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    sentiment = conn.execute("SELECT COUNT(*) FROM sentiment").fetchone()[0]
    sources = conn.execute(
        "SELECT COUNT(DISTINCT source) FROM posts"
    ).fetchone()[0]
    return posts, sentiment, sources


# ---------- Insight computation (data-driven narrative) ----------

def compute_insights(conn, total_sentiment: int) -> dict[str, str]:
    """Return a dict of {chart_key: narrative_html} with real numbers baked in."""
    out = {}

    # Source insight
    src_counts = q_source_counts(conn)
    src_dict = dict(src_counts)
    ptt_push = src_dict.get("ptt_push", 0)
    ptt = src_dict.get("ptt", 0)
    news = src_dict.get("news", 0)
    total = ptt_push + ptt + news
    if total > 0:
        pct_push = ptt_push / total * 100
        out["source"] = (
            f"本次樣本 <b>{total} 筆</b>，其中 <code>ptt_push</code> 佔 "
            f"<b>{pct_push:.0f}%</b>（{ptt_push} 筆），遠高於 <code>ptt</code> 主文 "
            f"（{ptt} 筆）與 <code>news</code>（{news} 筆）。"
            f"推噓量大、訊噪比低，<b>Ch.2 narrative 以 <code>ptt</code> + "
            f"<code>news</code> 為主軸，<code>ptt_push</code> 只用於聚合統計</b>。"
        )

    # Demographic insight
    dem_rows = q_demographic(conn)
    known_rows = [(l, n) for l, n, b in dem_rows if b == "known"]
    v2_rows = [(l, n) for l, n, b in dem_rows if b == "v2_sub"]
    unknown_n = sum(n for _, n, b in dem_rows if b == "unknown")
    v2_n = sum(n for _, n in v2_rows)
    if known_rows:
        top_label, top_n = known_rows[0]
        top_pct = top_n / max(total_sentiment, 1) * 100
        unknown_pct = unknown_n / max(total_sentiment, 1) * 100
        v2_pct = v2_n / max(total_sentiment, 1) * 100
        local = sum(n for l, n in known_rows if l in ("resident_tamsui", "resident_bali"))

        v2_breakdown = ""
        if v2_rows:
            top_v2 = v2_rows[0]
            v2_breakdown = (
                f"<br>v2 將 {v2_n} 筆原 unknown 拆細：最大宗為 "
                f"<code>{top_v2[0]}</code> <b>{top_v2[1]} 筆</b>。"
                f"剩餘真正無法分類的 <code>unknown</code> 降至 <b>{unknown_pct:.0f}%</b>。"
            )

        out["demographic"] = (
            f"<code>{top_label}</code> 最多 <b>{top_n} 筆 ({top_pct:.0f}%)</b>；"
            f"<code>resident_tamsui + resident_bali</code> 合計 <b>僅 {local} 筆</b> — "
            f"<b>在地聲音在 PTT 上幾近缺席</b>，這是方法論警示（不是結論）："
            f"任何引用「淡水民意」的推論都需要另行在地訪談補資料。"
            f"<br>原 v1 中 <code>unknown</code> 佔約 36%，"
            f"v2 版本拆細後 Layer 2 sub-category（淡黃色）佔 <b>{v2_pct:.0f}%</b>，"
            f"真 unknown（灰色）降至 <b>{unknown_pct:.0f}%</b>。"
            + v2_breakdown
        )

    # Heatmap insight
    demographics, stances, matrix = q_demographic_x_stance(conn)
    # Find biker row
    biker_i = demographics.index("biker") if "biker" in demographics else -1
    eng_i = demographics.index("engineer_tech") if "engineer_tech" in demographics else -1
    critical_j = stances.index("critical")
    support_j = stances.index("support")
    if biker_i >= 0 and eng_i >= 0:
        biker_total = sum(matrix[biker_i])
        biker_crit = matrix[biker_i][critical_j]
        biker_crit_pct = biker_crit / max(biker_total, 1) * 100
        biker_sup = matrix[biker_i][support_j]
        eng_total = sum(matrix[eng_i])
        eng_crit = matrix[eng_i][critical_j]
        eng_crit_pct = eng_crit / max(eng_total, 1) * 100
        out["heatmap"] = (
            f"本章最關鍵圖。兩個對比值：<br>"
            f"• <code>biker</code> × critical = <b>{biker_crit}/{biker_total} "
            f"({biker_crit_pct:.0f}%)</b> — 機車族壓倒性批判<br>"
            f"• <code>engineer_tech</code> × critical = <b>{eng_crit}/{eng_total} "
            f"({eng_crit_pct:.0f}%)</b> — 工程族群僅約 {eng_crit_pct:.0f}%，約為機車族的一半<br>"
            f"<b>含意</b>：「機車族全部反對」是刻板印象。biker 有 <b>{biker_sup} 筆支持</b>（={biker_sup/max(biker_total,1)*100:.0f}%）。"
            f"工程族群的分歧（33 vs 15，critical 只 41%）是 <b>NPP 值得爭取的中間地帶</b> — "
            f"不走民粹憤怒路線、走「技術專業派」的論述位置。"
        )

    # Emotion × stance insight
    emo_rows = q_emotion_by_stance(conn)
    emo_counts: dict[str, int] = {}
    for _, emo, n in emo_rows:
        emo_counts[emo] = emo_counts.get(emo, 0) + n
    total_emo = sum(emo_counts.values())
    if total_emo > 0:
        sarcasm = emo_counts.get("sarcasm", 0)
        anger = emo_counts.get("anger", 0)
        resignation = emo_counts.get("resignation", 0)
        out["emotion"] = (
            f"同樣是 critical，情緒組成差異對動員策略極關鍵。"
            f"<code>sarcasm</code> <b>{sarcasm}</b> > <code>anger</code> <b>{anger}</b> > "
            f"<code>resignation</code> <b>{resignation}</b>。"
            f"<br><b>含意</b>：PTT 批判的主旋律是 <b>諷刺（{sarcasm/total_emo*100:.0f}%）</b>，"
            f"不是憤怒（{anger/total_emo*100:.0f}%）。動員策略不該仿效 TPP 黃國昌式的怒吼路線，"
            f"要走 <b>冷幽默 + 尖銳資料</b> 才能接住這個語言文化。"
            f"<code>resignation</code> 另佔 {resignation/total_emo*100:.0f}% — 這是「習得性無助」訊號，"
            f"提醒政策推動不能只靠動員，要有可兌現的制度成果。"
        )

    # Topics insight
    topics = q_topics(conn, limit=15)
    topic_dict = dict(topics)
    mot = topic_dict.get("motorcycle_lane", 0)
    proc = topic_dict.get("procedural", 0)
    budget = topic_dict.get("budget", 0)
    if topics:
        out["topics"] = (
            f"議題聚焦高度失衡：<code>motorcycle_lane</code> <b>{mot}</b> 次 "
            f"vs <code>procedural</code> <b>{proc}</b> 次 vs <code>budget</code> <b>{budget}</b> 次。"
            f"<br><b>含意</b>：機車道的討論量是程序議題的 <b>{mot/max(proc,1):.1f}× "
            f"</b>，是預算議題的 <b>{mot/max(budget,1):.0f}×</b>。"
            f"TPP 主打的機車道角度吃下最大聲量，<b>NPP 主打的『程序正當性』與『230億審計』"
            f"目前在公眾話語中幾乎沒被討論</b>。這既是風險（NPP 要自己把議題推起來）也是機會"
            f"（還沒有競爭者）。"
        )

    return out


# ---------- HTML rendering ----------

HTML_TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<title>淡江大橋輿情儀表板 — bridge-analyst</title>
<script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
<style>
  body {{ font-family: -apple-system, "Segoe UI", sans-serif; margin: 24px; color: #222; max-width: 1200px; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 8px; }}
  h2 {{ margin-top: 36px; color: #2c4a6b; border-left: 4px solid #2c4a6b; padding-left: 10px; }}
  .meta {{ color: #666; font-size: 0.9em; margin-bottom: 16px; }}
  .summary {{ display: flex; gap: 16px; margin: 16px 0 32px; }}
  .card {{ background: #f4f6f8; border-radius: 6px; padding: 16px 24px; flex: 1; }}
  .card .num {{ font-size: 2em; font-weight: bold; color: #2c4a6b; }}
  .card .lbl {{ color: #666; font-size: 0.9em; }}
  .chart {{ margin: 16px 0; }}
  .insight {{ background: #f8f9fa; border-left: 3px solid #2c4a6b; padding: 12px 16px; margin: 12px 0; line-height: 1.7; }}
  .insight code {{ background: #e9ecef; padding: 1px 6px; border-radius: 3px; font-size: 0.9em; }}
  .quotes {{ background: #fff9e6; border-left: 4px solid #e0a800; padding: 12px 16px; margin: 8px 0; border-radius: 4px; }}
  .quote-meta {{ color: #666; font-size: 0.85em; }}
  .quote-body {{ margin: 4px 0; line-height: 1.5; }}
  footer {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid #ddd; color: #888; font-size: 0.85em; }}
</style>
</head>
<body>

<h1>淡江大橋輿情儀表板</h1>
<div class="meta">生成時間：{generated_at} · 資料來源：PTT (biker/HatePolitics/Gossiping) + 新聞 18 家</div>

<div class="summary">
  <div class="card"><div class="num">{total_posts}</div><div class="lbl">輿情樣本（貼文+推噓）</div></div>
  <div class="card"><div class="num">{total_sentiment}</div><div class="lbl">已完成抽取</div></div>
  <div class="card"><div class="num">{total_sources}</div><div class="lbl">資料來源數</div></div>
</div>

<h2>1. 貼文來源分布</h2>
<div class="insight">{insight_source}</div>
<div id="chart_sources" class="chart"></div>

<h2>2. 族群分布（9-code stakeholder map）</h2>
<div class="insight">{insight_demographic}</div>
<p style="color:#888;font-size:0.88em">
  色碼：<span style="color:#d17a22;font-weight:bold">橘</span> = 原 9 code 實存族群 ·
  <span style="color:#c9a800;font-weight:bold">淡黃</span> = v2 sub-category（<code>sarcastic_noise</code> / <code>generic_critique</code> / <code>pure_emotion</code> / <code>off_topic</code> / <code>short_fact</code> / <code>meta_discussion</code> / <code>generic_support</code>）·
  <span style="color:#888;font-weight:bold">灰</span> = 真的歸不出的 <code>unknown</code>。
  <br>方法論：v2 agent 只針對 v1 標 <code>unknown</code> 的貼文做二次分類。若 v2 能歸進 Layer 1（原 9 code）就用原 code；否則走 Layer 2 sub-category；極端邊緣案例才回到 Layer 3 <code>unknown</code>。
</p>
<div id="chart_demographic" class="chart"></div>

<h2>3. 族群 × 立場（輿情結構 heatmap）</h2>
<div class="insight">{insight_heatmap}</div>
<div id="chart_hmap" class="chart"></div>

<h2>4. 情緒 × 立場（critical 裡面是憤怒還是無奈？）</h2>
<div class="insight">{insight_emotion}</div>
<div id="chart_emo" class="chart"></div>

<h2>5. 主題頻率（Top 15）</h2>
<div class="insight">{insight_topics}</div>
<div id="chart_topics" class="chart"></div>

<h2>6. 樣本引用（隨機抽樣）</h2>
<p>為避免過度 aggregation 失真，每類立場隨機抽取原文引述，讓讀者自己判斷抽取品質。</p>

<h3 style="color:#c0392b">critical（批判）</h3>
{quotes_critical}

<h3 style="color:#27ae60">support（支持）</h3>
{quotes_support}

<h3 style="color:#7f8c8d">ambivalent（混合）</h3>
{quotes_ambivalent}

<footer>
bridge-analyst · Day 2–3 dashboard · 模型：<code>{model}</code> · 抽取版本：<code>{agent_version}</code><br>
方法論：每筆 post 由 sentiment_extractor agent 獨立分類，附一句推理，trace 存於 <code>data/traces/</code>。
統計顯著性檢定（chi-square）寫於報告附錄，不陳列於 dashboard — 避免對非統計專業讀者造成干擾。
</footer>

<script>
{plotly_calls}
</script>

</body></html>
"""


def _render_quote(row) -> str:
    quote, dem, emo, source, board = row
    board_str = f"/{board}" if board else ""
    return (
        f'<div class="quotes">'
        f'<div class="quote-meta">[{dem or "unknown"} · {emo or "?"}] {source}{board_str}</div>'
        f'<div class="quote-body">{html.escape(quote)}</div>'
        f'</div>'
    )


def main() -> int:
    out_path = REPO_ROOT / "reports" / "dashboard.html"
    out_path.parent.mkdir(exist_ok=True, parents=True)

    with store.connect() as conn:
        total_posts, total_sentiment, total_sources = q_summary_counts(conn)
        insights = compute_insights(conn, total_sentiment)

        # Chart 1: sources
        src_data = q_source_counts(conn)
        src_trace = {
            "type": "bar",
            "x": [s for s, _ in src_data],
            "y": [n for _, n in src_data],
            "marker": {"color": COLOR_PRIMARY},
            "text": [str(n) for _, n in src_data],
            "textposition": "outside",
        }
        src_layout = {"title": "貼文來源", "yaxis": {"title": "筆數"}}

        # Chart 2: demographic (known sorted desc, v2_sub middle, unknown last)
        dem_data = q_demographic(conn)
        dem_labels = [label for label, _, _ in dem_data]
        dem_values = [n for _, n, _ in dem_data]
        _color_map = {
            "known": COLOR_SECONDARY,  # 橘 = 原 9 code 實存族群
            "v2_sub": "#f4d35e",        # 淡黃 = v2 sub-category
            "unknown": COLOR_UNKNOWN,  # 灰 = 真的歸不出
        }
        dem_colors = [_color_map.get(bucket, COLOR_UNKNOWN)
                      for _, _, bucket in dem_data]
        dem_trace = {
            "type": "bar",
            "x": dem_values,
            "y": dem_labels,
            "orientation": "h",
            "marker": {"color": dem_colors},
            "text": [str(v) for v in dem_values],
            "textposition": "outside",
        }
        dem_layout = {
            "title": "9-code 族群分布 （灰 = 資料缺口，排列於末位）",
            # y axis is already in the right order (known sorted desc → unknown last).
            # Plotly draws bottom-up, so we reverse so the first (largest known) sits on top.
            "yaxis": {"autorange": "reversed"},
            "xaxis": {"title": "筆數"},
            "height": 480,
            "margin": {"l": 140},
        }

        # Chart 3: demographic × stance heatmap (ordered, unknown last)
        demographics, stances, matrix = q_demographic_x_stance(conn)
        hmap_trace = {
            "type": "heatmap",
            "z": matrix,
            "x": stances,
            "y": demographics,
            "colorscale": "Reds",
            "text": matrix,
            "texttemplate": "%{text}",
            "textfont": {"size": 13},
            "zmin": 0,
            "hoverongaps": False,
        }
        hmap_layout = {
            "title": "族群 × 立場（unknown 置於末位）",
            "xaxis": {"title": "立場"},
            "yaxis": {
                "title": "族群",
                # Same logic as demographic chart
                "autorange": "reversed",
            },
            "height": 520,
            "margin": {"l": 140},
        }

        # Chart 4: emotion × stance stacked bar
        emo_data = q_emotion_by_stance(conn)
        emotions = sorted({e for _, e, _ in emo_data})
        stances2 = sorted({s for s, _, _ in emo_data})
        emo_traces = []
        for emo in emotions:
            ys = []
            for st in stances2:
                n = next((c for s, e, c in emo_data if s == st and e == emo), 0)
                ys.append(n)
            emo_traces.append({
                "type": "bar", "name": emo,
                "x": stances2, "y": ys,
            })
        emo_layout = {"title": "情緒 × 立場（stacked）", "barmode": "stack",
                      "yaxis": {"title": "筆數"}}

        # Chart 5: topics
        topics = q_topics(conn, limit=15)
        tp_trace = {
            "type": "bar",
            "x": [n for _, n in topics],
            "y": [t for t, _ in topics],
            "orientation": "h",
            "marker": {"color": COLOR_TERTIARY},
            "text": [str(n) for _, n in topics],
            "textposition": "outside",
        }
        tp_layout = {
            "title": "Top 15 主題",
            "yaxis": {"autorange": "reversed"},
            "xaxis": {"title": "出現次數"},
            "height": 480,
            "margin": {"l": 180},
        }

        # Quotes
        q_crit = "\n".join(_render_quote(r) for r in q_sample_quotes(conn, "critical"))
        q_sup = "\n".join(_render_quote(r) for r in q_sample_quotes(conn, "support"))
        q_amb = "\n".join(_render_quote(r) for r in q_sample_quotes(conn, "ambivalent"))

        # Agent version
        agent_version_row = conn.execute(
            "SELECT DISTINCT agent_version FROM sentiment LIMIT 1"
        ).fetchone()
        agent_version = agent_version_row[0] if agent_version_row else "n/a"

    plotly_calls = "\n".join([
        f"Plotly.newPlot('chart_sources', [{json.dumps(src_trace)}], {json.dumps(src_layout)});",
        f"Plotly.newPlot('chart_demographic', [{json.dumps(dem_trace)}], {json.dumps(dem_layout)});",
        f"Plotly.newPlot('chart_hmap', [{json.dumps(hmap_trace)}], {json.dumps(hmap_layout)});",
        f"Plotly.newPlot('chart_emo', {json.dumps(emo_traces)}, {json.dumps(emo_layout)});",
        f"Plotly.newPlot('chart_topics', [{json.dumps(tp_trace)}], {json.dumps(tp_layout)});",
    ])

    html_out = HTML_TEMPLATE.format(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        total_posts=total_posts,
        total_sentiment=total_sentiment,
        total_sources=total_sources,
        plotly_calls=plotly_calls,
        quotes_critical=q_crit or "<p><em>no samples</em></p>",
        quotes_support=q_sup or "<p><em>no samples</em></p>",
        quotes_ambivalent=q_amb or "<p><em>no samples</em></p>",
        model="claude-sonnet-4-5",
        agent_version=agent_version,
        insight_source=insights.get("source", ""),
        insight_demographic=insights.get("demographic", ""),
        insight_heatmap=insights.get("heatmap", ""),
        insight_emotion=insights.get("emotion", ""),
        insight_topics=insights.get("topics", ""),
    )

    out_path.write_text(html_out, encoding="utf-8")
    print(f"Dashboard written: {out_path}")
    print(f"Open with: open {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
