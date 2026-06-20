import os
import sys
import re
import base64
import json
import urllib.parse
from datetime import date, timedelta

import requests
import anthropic

SHUFOO_API_URL = (
    "https://asp.shufoo.net/api/shopDetailXML.php"
    "?un=summitstore&shopId=255387&responseFormat=json&usedfor=shufoo.asp.chirashilist"
)
SHUFOO_HEADERS = {"Referer": "https://www.summitstore.co.jp/"}

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


def send_slack(webhook_url: str, text: str) -> None:
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"[Slack通知失敗] {e}")


def fetch_pdf_bytes(pdf_url: str) -> bytes:
    """pdfUrlがmeta-refreshリダイレクト先にある場合も対応してPDFバイト列を返す"""
    resp = requests.get(pdf_url, headers=SHUFOO_HEADERS, timeout=30)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type:
        match = re.search(
            r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][\d.]+;\s*URL=([^"\']+)["\']',
            resp.text,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"meta-refreshが見つかりませんでした: {pdf_url}")
        real_url = urllib.parse.urljoin(pdf_url, match.group(1).strip())
        resp = requests.get(real_url, timeout=30)
        resp.raise_for_status()
    return resp.content


def fetch_chirashis() -> list[dict]:
    """SHUFOO APIからチラシリストを取得して最大2件返す"""
    resp = requests.get(SHUFOO_API_URL, headers=SHUFOO_HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    chirashis = data["shop"]["chirashis"]["chirashi"]
    if isinstance(chirashis, dict):
        chirashis = [chirashis]
    return chirashis[:2]


def extract_sale_items(client: anthropic.Anthropic, chirashis: list[dict]) -> list[dict]:
    """チラシPDFをClaudeに送って特売品JSONを抽出する"""
    content = []
    end_dates = []

    for chirashi in chirashis:
        pdf_bytes = fetch_pdf_bytes(chirashi["pdfUrl"])
        b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
        content.append(
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            }
        )
        end_dates.append(chirashi.get("publishEndTime", "不明"))

    end_date_str = "、".join(
        [f"チラシ{i+1}の有効期限: {d}" for i, d in enumerate(end_dates)]
    )
    content.append(
        {
            "type": "text",
            "text": (
                f"以下のチラシPDFから夕食献立に使えそうな特売品を最大30品抽出してください。\n"
                f"（肉・魚・野菜・豆腐など食材を優先、調味料・お菓子・日用品は除く）\n"
                f"{end_date_str}\n\n"
                "出力形式（JSONのみ、他のテキスト不要）:\n"
                '{"items": [{"name": "商品名", "price": 数値, "sale_end": "YYYY-MM-DD", "note": "補足"}]}'
            ),
        }
    )

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system="スーパーのチラシから特売品情報をJSONで抽出するアシスタントです。出力はJSONのみ。",
        messages=[{"role": "user", "content": content}],
    )

    raw = ""
    for block in response.content:
        if block.type == "text":
            raw = block.text
            break

    # JSONブロックの抽出（```json ... ``` に包まれている場合も対応）
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        raise ValueError(f"特売品JSONが抽出できませんでした: {raw[:200]}")

    items = json.loads(json_match.group())
    return items.get("items", [])


def generate_meal_plan(client: anthropic.Anthropic, items: list[dict]) -> str:
    """特売品リストから7日分の夕食献立をSlack向けMarkdownで生成する"""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    week_later = today + timedelta(days=7)

    def fmt_date(d: date) -> str:
        return f"{d.month}/{d.day}({WEEKDAY_JP[d.weekday()]})"

    items_json = json.dumps(items, ensure_ascii=False, indent=2)

    prompt = f"""以下の特売品リストを使って、1人暮らし男性の7日分夕食献立を作成してください。

【特売品リスト】
{items_json}

【献立作成ルール】
- 対象期間: {fmt_date(tomorrow)} ～ {fmt_date(week_later)}（{fmt_date(today)}は除く）
- 2人分作って翌日は余りものを食べる（「新メニュー → 翌日残り物」を交互に繰り返す（1日目:新メニューA, 2日目:メニューAの残り, 3日目:新メニューB, 4日目:メニューBの残り…）4メニュー×2日 = 8食で7日をカバー）
- 4つのメニューは調理法を変える（例: 炒め物、煮物、焼き物、揚げ物）
- 特売品を積極的に使う
- 買い物リストも出す

【出力形式（Slack Markdown）】
*🍽️ 今週の夕食献立*

| 日付 | 献立 | メモ |
|------|------|------|
| {fmt_date(tomorrow)} | ... | ... |
...

*🛒 買い物リスト*
• 商品名（数量）

出力はSlack Markdownのみ、余分な説明は不要。"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "text":
            return block.text

    raise ValueError("献立テキストが生成されませんでした")


def main() -> None:
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")
    slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL")

    if not anthropic_api_key:
        print("[ERROR] ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)
    if not slack_webhook_url:
        print("[ERROR] SLACK_WEBHOOK_URL が設定されていません")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=anthropic_api_key)

    try:
        chirashis = fetch_chirashis()
    except Exception as e:
        msg = f":warning: チラシ取得に失敗しました: {e}"
        print(f"[ERROR] {msg}")
        send_slack(slack_webhook_url, msg)
        sys.exit(1)

    try:
        items = extract_sale_items(client, chirashis)
        meal_plan = generate_meal_plan(client, items)
        send_slack(slack_webhook_url, meal_plan)
        print("[OK] 献立をSlackに送信しました")

        shopping_match = re.search(r'(\*🛒.+)', meal_plan, re.DOTALL)
        if shopping_match:
            with open("shopping_list.txt", "w", encoding="utf-8") as f:
                f.write(shopping_match.group(1))
    except Exception as e:
        msg = f":warning: 献立生成に失敗しました: {e}"
        print(f"[ERROR] {msg}")
        send_slack(slack_webhook_url, msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
