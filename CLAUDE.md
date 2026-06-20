# okaimonoHelper — 開発ガイドライン

## プロジェクト概要

サミットストアのチラシ（SHUFOO API）から特売品を抽出し、1人暮らし男性の7日分夕食献立を生成してSlackに通知するPythonスクリプト。GitHub Actionsで毎週月曜 JST 6:00 に自動実行。

## 固定パラメータ

```
SHUFOO shopId: 255387
SHUFOO un: summitstore
SHUFOO API URL: https://asp.shufoo.net/api/shopDetailXML.php
Referer: https://www.summitstore.co.jp/
```

## 環境変数（必須）

| 変数名 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API認証 |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook |

GitHub Secretsにも同名で登録済み。

## モデル・トークン設定

- モデル: `claude-sonnet-4-6`（コストと精度のバランスが良い）
- Call 1（PDF抽出）: `max_tokens=8192`（PDFは出力が多いので余裕を持たせる）
- Call 2（献立生成）: `max_tokens=4096`

## 開発時のルール（過去の失敗から）

### 1. 外部APIは実装前に必ずcurlで叩く

レスポンス構造を確認してから実装する。思い込みで実装するとKeyErrorなどで詰まる。

```bash
curl -s "https://asp.shufoo.net/api/shopDetailXML.php?un=summitstore&shopId=255387&responseFormat=json&usedfor=shufoo.asp.chirashilist" \
  -H "Referer: https://www.summitstore.co.jp/" | python3 -m json.tool | head -50
```

### 2. PDFや大きなドキュメントはトークン数を事前計測する

```python
token_count = client.messages.count_tokens(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": content}],
)
print(f"Input tokens: {token_count.input_tokens}")
```

max_tokensは計測結果を見てから設定する。

### 3. モデルIDは実装前にスキルで確認する

`/claude-api` スキルで最新のモデルIDと料金を確認してから使う。古いモデルIDはエラーになる。

### 4. API実行前にクレジット残高を確認する

[console.anthropic.com](https://console.anthropic.com) で残高を確認してからテスト実行する。

### 5. GitHub Actionsはworkflow_dispatchで手動テストする

cron を待たず、GitHub Actions画面の「Run workflow」で手動実行してSecretsの疎通確認をする。

## エージェント構成

- `.claude/agents/coder.md` — 実装担当
- `.claude/agents/reviewer.md` — レビュー担当（セキュリティ・仕様準拠・エラーハンドリング・コード品質を確認）

実装後は必ずreviewerに通してからテスト実行する。
