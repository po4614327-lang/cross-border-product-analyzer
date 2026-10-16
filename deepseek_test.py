"""读取模拟商品列表，让 DeepSeek 翻译成跨境电商英文标题并估算利润率，
输出严格 JSON，由 Python 解析后在终端打印整齐表格。

用法：
1. 安装依赖：pip install openai
2. 设置 API Key：PowerShell 里执行 $env:DEEPSEEK_API_KEY="sk-你的key"
3. 运行：python deepseek_test.py
"""

import json
import os
import unicodedata

from openai import OpenAI

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# 模拟商品列表：中文标题、进价（成本）、建议售价（单位：元）
PRODUCTS = [
    {"id": 1, "title": "纯棉短袖T恤 男士夏季宽松透气圆领", "cost": 35.0, "price": 89.0},
    {"id": 2, "title": "不锈钢保温杯 316大容量便携车载水杯", "cost": 42.5, "price": 129.0},
    {"id": 3, "title": "无线蓝牙耳机 入耳式降噪长续航运动耳机", "cost": 78.0, "price": 199.0},
]


def build_prompt(products):
    lines = [
        "你是资深跨境电商运营。请为下列商品完成两件事：",
        "1. 翻译成适合跨境电商平台的英文标题（关键词自然、地道、利于搜索，控制在 60 字符以内）。",
        "2. 根据进价和建议售价估算利润率，公式为 (建议售价 - 进价) / 建议售价 * 100，保留 1 位小数。",
        "只输出 JSON，不要任何解释文字，格式严格如下：",
        '{"products":[{"id":1,"english_title":"...","profit_margin":45.2}]}',
        "",
        "商品数据：",
    ]
    for p in products:
        lines.append(
            json.dumps(p, ensure_ascii=False)
        )
    return "\n".join(lines)


def ask_deepseek(products):
    client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")
    response = client.chat.completions.create(
        model="deepseek-chat",
        response_format={"type": "json_object"},
        messages=[
            {"role": "user", "content": build_prompt(products)},
        ],
    )
    return response.choices[0].message.content


# ---------- 终端表格渲染（处理中文等宽字符对齐） ----------

def display_width(text):
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate(text, max_width):
    if display_width(text) <= max_width:
        return text
    out, width = "", 0
    for ch in text:
        ch_w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if width + ch_w > max_width - 1:
            break
        out += ch
        width += ch_w
    return out + "…"


def pad(text, width, align="left"):
    text = truncate(text, width)
    space = " " * (width - display_width(text))
    return space + text if align == "right" else text + space


def print_table(headers, rows, aligns, max_widths):
    widths = [display_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], display_width(cell))
    widths = [min(w, max_widths[i]) for i, w in enumerate(widths)]

    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    def render(cells):
        return "| " + " | ".join(
            pad(c, widths[i], aligns[i]) for i, c in enumerate(cells)
        ) + " |"

    print(line)
    print(render(headers))
    print(line)
    for row in rows:
        print(render(row))
    print(line)


def main():
    if not API_KEY:
        print("未检测到 API Key，请设置环境变量 DEEPSEEK_API_KEY。")
        return

    print("正在请求 DeepSeek...\n")
    raw = ask_deepseek(PRODUCTS)
    data = json.loads(raw)

    by_id = {p["id"]: p for p in PRODUCTS}
    rows = []
    for item in data["products"]:
        p = by_id.get(item["id"])
        if p is None:
            continue
        rows.append([
            str(item["id"]),
            p["title"],
            item["english_title"],
            f"{p['cost']:.2f}",
            f"{p['price']:.2f}",
            f"{item['profit_margin']:.1f}%",
        ])

    headers = ["ID", "中文标题", "跨境英文标题", "进价", "建议售价", "利润率"]
    aligns = ["right", "left", "left", "right", "right", "right"]
    max_widths = [4, 30, 52, 9, 10, 8]
    print_table(headers, rows, aligns, max_widths)

    print("\n原始 JSON：")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
