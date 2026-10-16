"""Function Calling 示例：
让 DeepSeek 根据用户输入的商品信息，自主决定调用本地利润计算函数，
拿到精确结果后再整合成给商家的选品利润分析建议。

用法：
1. 安装依赖：pip install openai
2. 设置 API Key：PowerShell 里执行 $env:DEEPSEEK_API_KEY="sk-你的key"
3. 运行：python deepseek_function_call.py
"""

import json
import os

from openai import OpenAI

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-chat"

# 用户输入：商品信息（模型据此自主决定是否调用本地函数、带哪些参数）
USER_QUERY = (
    "我有个商品，进货成本 35 元人民币，打算在跨境电商平台卖 12.99 美元。"
    "帮我算一下扣除平台佣金、换汇之后的真实人民币净利润，再给我一份选品利润分析建议。"
)


# ---------- 本地真实计算函数 ----------

def calculate_real_profit(cost_cny, price_usd, exchange_rate=7.2, platform_commission=0.1):
    """精确计算扣除平台佣金与换汇后的人民币净利润。

    :param cost_cny: 进货成本（人民币）
    :param price_usd: 海外售价（美元）
    :param exchange_rate: 美元兑人民币汇率
    :param platform_commission: 平台佣金比例（0.1 表示 10%）
    :return: 含各项明细的字典
    """
    revenue_cny = price_usd * exchange_rate
    commission_cny = revenue_cny * platform_commission
    net_profit_cny = revenue_cny - commission_cny - cost_cny
    net_margin_pct = net_profit_cny / revenue_cny * 100
    return {
        "revenue_cny": round(revenue_cny, 2),
        "commission_cny": round(commission_cny, 2),
        "net_profit_cny": round(net_profit_cny, 2),
        "net_margin_pct": round(net_margin_pct, 2),
    }


AVAILABLE_FUNCTIONS = {"calculate_real_profit": calculate_real_profit}

# 告诉模型有哪些本地函数可用（tools 机制）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_real_profit",
            "description": "计算商品在跨境电商平台的真实人民币净利润，已扣除平台佣金并按汇率换汇。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cost_cny": {"type": "number", "description": "进货成本（人民币）"},
                    "price_usd": {"type": "number", "description": "海外售价（美元）"},
                    "exchange_rate": {"type": "number", "description": "美元兑人民币汇率，默认 7.2"},
                    "platform_commission": {"type": "number", "description": "平台佣金比例，默认 0.1（10%）"},
                },
                "required": ["cost_cny", "price_usd"],
            },
        },
    }
]


def run():
    if not API_KEY:
        print("未检测到 API Key，请设置环境变量 DEEPSEEK_API_KEY。")
        return

    client = OpenAI(api_key=API_KEY, base_url="https://api.deepseek.com")
    messages = [
        {"role": "system", "content": "你是跨境电商选品顾问，可以调用工具获取精确计算，再给出分析建议。"},
        {"role": "user", "content": USER_QUERY},
    ]

    # Function Calling 循环：模型可能多轮调用工具，直到给出最终文字回答
    for _ in range(5):
        response = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
        msg = response.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            print("\n===== 选品利润分析建议 =====\n")
            print(msg.content)
            return

        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            print(f"[模型决定调用本地函数] {name}({json.dumps(args, ensure_ascii=False)})")

            result = AVAILABLE_FUNCTIONS[name](**args)
            print(f"[本地函数精确返回] {json.dumps(result, ensure_ascii=False)}")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    print("达到最大轮次仍未得到最终回答。")


if __name__ == "__main__":
    run()
