"""跨境电商选品分析 Web 小工具 (FastAPI + 内嵌 HTML)

功能：在网页输入商品中文名、进价(元)、售价(美元)，点击后展示：
  - 跨境电商英文标题
  - 精准利润数据（本地函数计算）
  - AI 选品分析建议

运行：
  1. pip install fastapi uvicorn
  2. $env:DEEPSEEK_API_KEY="sk-你的key"
  3. python web_app.py
  4. 浏览器打开 http://127.0.0.1:8000
"""

import io
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from openai import OpenAI

# 复用已有的本地利润函数与 tools 定义
from deepseek_function_call import (
    AVAILABLE_FUNCTIONS,
    TOOLS,
    calculate_real_profit,
)

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-chat"

app = FastAPI(title="跨境电商选品分析")


# ---------- LLM + Function Calling ----------

def analyze_with_ai(title: str, cost_cny: float, price_usd: float):
    """调用 DeepSeek：翻译标题 + 调用本地利润函数 + 生成分析建议。

    返回 (english_title, profit_data, analysis)。
    profit_data 始终用本地函数的精确结果，保证展示数据正确。
    """
    # 本地精确计算（用于展示的利润数据来源）
    profit_data = calculate_real_profit(cost_cny, price_usd)

    if not API_KEY:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY 环境变量")

    client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")

    prompt = (
        "你是跨境电商选品顾问。请完成以下任务：\n"
        "1. 将中文商品标题翻译成适合跨境电商平台的英文标题（地道、利于搜索，80 字符以内）。\n"
        "2. 调用 calculate_real_profit 工具计算扣除平台佣金和换汇后的人民币净利润（必须使用工具返回的数值，不要自己心算）。\n"
        "3. 结合利润结果，用中文给出 3~5 条选品分析建议。\n\n"
        "最终只输出 JSON，不要任何额外解释，格式严格为：\n"
        '{"english_title": "...", "analysis": "..."}\n\n'
        f"商品信息：\n中文标题: {title}\n进货成本(人民币): {cost_cny}\n海外售价(美元): {price_usd}"
    )

    messages = [
        {"role": "system", "content": "你可以调用工具获取精确计算结果，再给出分析。"},
        {"role": "user", "content": prompt},
    ]

    final_content = None
    for _ in range(5):
        response = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            final_content = msg.content
            break

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = AVAILABLE_FUNCTIONS[tc.function.name](**args)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    # 解析最终 JSON；失败则回退
    english_title, analysis = "", final_content or ""
    if final_content:
        try:
            obj = json.loads(final_content)
            english_title = obj.get("english_title", "")
            analysis = obj.get("analysis", final_content)
        except json.JSONDecodeError:
            analysis = final_content

    return english_title, profit_data, analysis


# ---------- HTTP 接口 ----------

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(INDEX_HTML)


@app.post("/analyze")
def analyze(title: str = Form(...), cost_cny: float = Form(...), price_usd: float = Form(...)):
    try:
        english_title, profit_data, analysis = analyze_with_ai(title, cost_cny, price_usd)
        return {
            "ok": True,
            "english_title": english_title,
            "profit": profit_data,
            "analysis": analysis,
        }
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ---------- 批量处理 ----------

def _find_column(columns, keywords):
    """按关键词模糊匹配列名。"""
    for col in columns:
        name = str(col).strip().lower()
        if any(k in name for k in keywords):
            return col
    return None


def parse_uploaded_rows(df: pd.DataFrame):
    """从 DataFrame 中识别 标题/进价/售价 三列并解析为行列表。"""
    cols = list(df.columns)
    title_col = _find_column(cols, ["标题", "title", "商品", "品名"])
    cost_col = _find_column(cols, ["进价", "成本", "cost", "进货"])
    price_col = _find_column(cols, ["售价", "价格", "price", "定价"])
    if not (title_col and cost_col and price_col):
        raise ValueError(
            f"未识别到必需列，请确保包含 标题、进价、售价 三列。当前列名：{cols}"
        )

    rows = []
    for _, r in df.iterrows():
        title = str(r[title_col]).strip()
        try:
            cost = float(r[cost_col])
            price = float(r[price_col])
        except (TypeError, ValueError):
            continue
        if title and title.lower() != "nan":
            rows.append({"title": title, "cost": cost, "price": price})
    return rows


@app.get("/template")
def template():
    """下载批量处理模板（示例 xlsx）。"""
    df = pd.DataFrame([
        {"标题": "纯棉短袖T恤 男士夏季宽松透气圆领", "进价": 35, "售价": 12.99},
        {"标题": "不锈钢保温杯 316大容量便携车载水杯", "进价": 42.5, "售价": 18.99},
        {"标题": "无线蓝牙耳机 入耳式降噪长续航运动耳机", "进价": 78, "售价": 29.99},
    ])
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template.xlsx"'},
    )


@app.post("/batch")
def batch(file: UploadFile = File(...)):
    """批量处理：解析上传的 xlsx/csv，并发调用 AI，返回结果 Excel 供下载。"""
    try:
        content = file.file.read()
        name = (file.filename or "").lower()

        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        else:
            df = pd.read_excel(io.BytesIO(content))

        rows = parse_uploaded_rows(df)
        if not rows:
            return JSONResponse({"ok": False, "error": "没有可处理的数据行"}, status_code=400)

        def work(r):
            try:
                en, profit, analysis = analyze_with_ai(r["title"], r["cost"], r["price"])
                return {**r, "english_title": en, "profit": profit,
                        "analysis": analysis, "error": ""}
            except Exception as e:
                return {**r, "english_title": "", "profit": None,
                        "analysis": "", "error": str(e)}

        # 限制并发，避免触发 API 限流
        with ThreadPoolExecutor(max_workers=3) as ex:
            results = list(ex.map(work, rows))

        out_df = pd.DataFrame([
            {
                "中文标题": r["title"],
                "进价(元)": r["cost"],
                "售价(美元)": r["price"],
                "英文标题": r["english_title"],
                "收入(元)": r["profit"]["revenue_cny"] if r["profit"] else "",
                "佣金(元)": r["profit"]["commission_cny"] if r["profit"] else "",
                "净利润(元)": r["profit"]["net_profit_cny"] if r["profit"] else "",
                "净利润率(%)": r["profit"]["net_margin_pct"] if r["profit"] else "",
                "AI建议": r["analysis"],
                "错误": r["error"],
            }
            for r in results
        ])

        buf = io.BytesIO()
        out_df.to_excel(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="analysis_result.xlsx"'},
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ---------- 网页模板 ----------

INDEX_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>跨境电商选品分析</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         background: #f4f6fb; color: #1f2937; }
  .wrap { max-width: 880px; margin: 40px auto; padding: 0 16px; }
  h1 { font-size: 24px; margin: 0 0 6px; }
  .sub { color: #6b7280; margin-bottom: 24px; font-size: 14px; }
  .card { background: #fff; border-radius: 12px; padding: 24px; box-shadow: 0 2px 10px rgba(0,0,0,.06); }
  label { display: block; font-size: 13px; color: #374151; margin: 14px 0 6px; font-weight: 600; }
  input { width: 100%; padding: 10px 12px; border: 1px solid #d1d5db; border-radius: 8px;
          font-size: 15px; outline: none; }
  input:focus { border-color: #4f46e5; box-shadow: 0 0 0 3px rgba(79,70,229,.15); }
  button { margin-top: 22px; width: 100%; padding: 12px; background: #4f46e5; color: #fff;
           border: none; border-radius: 8px; font-size: 16px; cursor: pointer; font-weight: 600; }
  button:hover { background: #4338ca; }
  button:disabled { background: #9ca3af; cursor: not-allowed; }
  .results { margin-top: 28px; display: grid; gap: 16px; }
  .result-card { background: #fff; border-radius: 12px; padding: 20px;
                 box-shadow: 0 2px 10px rgba(0,0,0,.06); }
  .result-card h3 { margin: 0 0 12px; font-size: 15px; color: #4f46e5; }
  .en-title { font-size: 18px; font-weight: 700; color: #111827; word-break: break-word; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 8px 0; border-bottom: 1px solid #f0f0f0; font-size: 14px; }
  td:last-child { text-align: right; font-weight: 600; }
  .profit { color: #059669; }
  .analysis { white-space: pre-wrap; line-height: 1.7; font-size: 14px; color: #374151; }
  .error { color: #dc2626; background: #fef2f2; padding: 12px; border-radius: 8px; }
  .hidden { display: none; }
  .loading { color: #6b7280; font-size: 14px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>跨境电商选品分析</h1>
  <div class="sub">输入商品信息，一键获取英文标题、精准利润与 AI 选品建议</div>

  <div class="card">
    <form id="f">
      <label>商品中文标题</label>
      <input name="title" required placeholder="例如：纯棉短袖T恤 男士夏季宽松透气圆领" value="纯棉短袖T恤 男士夏季宽松透气圆领">

      <label>进货成本（人民币 元）</label>
      <input name="cost_cny" type="number" step="0.01" min="0" required value="35">

      <label>海外售价（美元）</label>
      <input name="price_usd" type="number" step="0.01" min="0" required value="12.99">

      <button type="submit" id="btn">开始分析</button>
    </form>
  </div>

  <div class="card" style="margin-top:24px">
    <h3 style="margin-top:0">批量处理</h3>
    <p style="color:#6b7280;font-size:13px;margin:0 0 12px">
      上传包含 <b>标题、进价、售价</b> 三列的 .xlsx 或 .csv 文件，处理完成后下载结果 Excel。
      <a href="/template" style="color:#4f46e5">下载模板</a>
    </p>
    <input type="file" id="batch-file" accept=".xlsx,.xls,.csv" style="margin-bottom:12px">
    <button type="button" id="batch-btn">开始批量处理</button>
    <div id="batch-status" class="loading hidden" style="margin-top:14px"></div>
    <div id="batch-error" class="error hidden" style="margin-top:14px"></div>
    <a id="batch-download" class="hidden" download="analysis_result.xlsx"
       style="display:block;margin-top:14px;padding:12px;background:#059669;color:#fff;
              text-align:center;border-radius:8px;text-decoration:none;font-weight:600">
       下载分析结果 Excel
    </a>
  </div>

  <div class="results" id="results">
    <div class="result-card hidden" id="card-title">
      <h3>跨境英文标题</h3>
      <div class="en-title" id="en-title"></div>
    </div>
    <div class="result-card hidden" id="card-profit">
      <h3>精准利润数据</h3>
      <table id="profit-table"></table>
    </div>
    <div class="result-card hidden" id="card-analysis">
      <h3>选品分析建议</h3>
      <div class="analysis" id="analysis"></div>
    </div>
    <div class="result-card loading hidden" id="loading">正在请求 DeepSeek 并调用本地函数计算…</div>
    <div class="result-card error hidden" id="error"></div>
  </div>
</div>

<script>
const form = document.getElementById('f');
const btn = document.getElementById('btn');
const loading = document.getElementById('loading');
const errorBox = document.getElementById('error');

function hideAll() {
  ['card-title','card-profit','card-analysis','error'].forEach(id =>
    document.getElementById(id).classList.add('hidden'));
  errorBox.textContent = '';
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  hideAll();
  btn.disabled = true;
  loading.classList.remove('hidden');

  const fd = new FormData(form);
  try {
    const res = await fetch('/analyze', { method: 'POST', body: fd });
    const data = await res.json();
    loading.classList.add('hidden');

    if (!data.ok) { errorBox.textContent = data.error || '请求失败'; errorBox.classList.remove('hidden'); return; }

    document.getElementById('en-title').textContent = data.english_title || '(未返回)';
    const p = data.profit;
    const rows = [
      ['折合人民币收入', `¥${p.revenue_cny.toFixed(2)}`],
      ['平台佣金（10%）', `¥${p.commission_cny.toFixed(2)}`],
      ['真实净利润', `<span class="profit">¥${p.net_profit_cny.toFixed(2)}</span>`],
      ['净利润率', `<span class="profit">${p.net_margin_pct.toFixed(2)}%</span>`],
    ];
    document.getElementById('profit-table').innerHTML =
      rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('');
    document.getElementById('analysis').textContent = data.analysis || '(无)';

    ['card-title','card-profit','card-analysis'].forEach(id => document.getElementById(id).classList.remove('hidden'));
  } catch (err) {
    loading.classList.add('hidden');
    errorBox.textContent = '请求出错：' + err.message;
    errorBox.classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
});

// 批量处理
const batchBtn = document.getElementById('batch-btn');
const batchFile = document.getElementById('batch-file');
const batchStatus = document.getElementById('batch-status');
const batchError = document.getElementById('batch-error');
const batchDownload = document.getElementById('batch-download');

batchBtn.addEventListener('click', async () => {
  if (!batchFile.files[0]) {
    batchError.textContent = '请先选择文件';
    batchError.classList.remove('hidden');
    return;
  }
  batchError.classList.add('hidden');
  batchDownload.classList.add('hidden');
  batchStatus.textContent = '正在批量处理，请稍候（每行都会调用 AI，耗时取决于行数）…';
  batchStatus.classList.remove('hidden');
  batchBtn.disabled = true;

  const fd = new FormData();
  fd.append('file', batchFile.files[0]);
  try {
    const res = await fetch('/batch', { method: 'POST', body: fd });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || '处理失败');
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    batchDownload.href = url;
    batchDownload.classList.remove('hidden');
    batchStatus.textContent = `处理完成，点击下方按钮下载结果 Excel。`;
  } catch (err) {
    batchStatus.classList.add('hidden');
    batchError.textContent = '批量处理出错：' + err.message;
    batchError.classList.remove('hidden');
  } finally {
    batchBtn.disabled = false;
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
