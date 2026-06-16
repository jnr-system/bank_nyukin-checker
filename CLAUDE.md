# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

銀行振込の入金データ（Googleスプレッドシート）と楽楽販売の顧客データを自動照合するツール。照合成功時はスプレッドシートへ結果書き込み、楽楽販売のフラグ・入金日を更新し、必要に応じてSMSを送信する。

## コマンド

```bash
# 依存パッケージインストール
pip install -r requirements.txt

# 当月シートで本番実行
python main.py

# 指定月シートで実行
python main.py --sheet 202603

# ドライラン（スプシ・楽楽・SMSへの書き込みなし）
python main.py --dry-run

# スプシのみ更新（楽楽フラグ更新・SMS送信はスキップ）
python main.py --sheet-only

# SMS送信スキップ（スプシ・楽楽は更新）
python main.py --no-sms

# 楽楽確認済みの行にも手配番号をB列へ反映
python main.py --fill-tehai
```

テスト・リントの設定は存在しない。

## アーキテクチャ

```
main.py          # エントリポイント・照合ループ・集計ログ
rakuraku.py      # 楽楽販売API（CSVエクスポートで全件取得・レコード更新）
matching.py      # カナ正規化・振込識別番号照合・Gemini照合ロジック
spreadsheet.py   # Google Sheets 読み書き（gspread）
sms.py           # accrete SMS送信API
```

### 処理フロー

1. `main.py` が起動時に `rakuraku.fetch_all_records()` で顧客データを最大5,000件一括取得
2. 環境変数 `SPREADSHEET_ID_1`〜`_4` で設定されたスプレッドシートをループ処理
3. 各行を `matching.match_record()` に渡して照合（モードは環境変数 `SPREADSHEET_MATCH_MODE_n` で切替）
4. 照合成功時：`spreadsheet.write_result()` → `rakuraku.update_kinfu_flags()` → `sms.send_sms()`（設定時のみ）

### 照合モード（`matching.py`）

- **`kana`モード**（デフォルト）：スキップ判定 → 振込識別番号照合 → カナ完全一致 → Gemini AI照合
- **`kanji`モード**：漢字氏名の完全一致 → Gemini AI照合（`（日程調整）請求先お名前` 列と突合）
- **`tehai`モード**：手配番号の完全一致のみ

Gemini モデルは `matching.py` の `GEMINI_MODEL = "gemini-3-flash-preview"` で固定。

### 楽楽販売API（`rakuraku.py`）

- ベースURL: `https://hntobias.rakurakuhanbai.jp/mspy4wa`
- データ取得: `/api/csvexport/version/v1`（CSVをdictのリストに変換）
- レコード更新: `/apirecord/update/version/v1`（成約管理DB + 問合せ管理DBの2回更新）
- レート制限: `REQUEST_INTERVAL = 1.0`秒スリープ（1分20リクエスト制限）
- 重要なID定数（`SEIYAKU_DB_SCHEMA_ID`, `LIST_ID`, `SEARCH_ID`, 各 `ITEM_ID`）はハードコード

### スプレッドシートの構造

- シート名は `YYYYMM`（例: `202604`）
- A列が照合結果書き込み先（固定）
- 名義・日付・金額の列はシートごとに環境変数で指定（`SPREADSHEET_NAME_COL_n` 等）

## 環境変数（`.env`）

| 変数名 | 説明 |
|--------|------|
| `RAKURAKU_TOKEN` | 楽楽販売 APIトークン |
| `GEMINI_API_KEY` | Gemini APIキー |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GoogleサービスアカウントのJSONを1行で記載 |
| `ACCRETE_ACCOUNT_ID` | accrete SMS APIアカウントID |
| `ACCRETE_REQUEST_ID` | accrete SMS APIリクエストID |
| `ACCRETE_PASSWORD` | accrete SMS APIパスワード |
| `SPREADSHEET_ID_n` | n番目のスプレッドシートID（n=1〜4） |
| `SPREADSHEET_MATCH_MODE_n` | 照合モード（`kana` / `kanji` / `tehai`） |
| `SPREADSHEET_NAME_COL_n` | 口座名義列（例: `C`） |
| `SPREADSHEET_DATE_COL_n` | 入金日列（例: `D`） |
| `SPREADSHEET_AMOUNT_COL_n` | 入金金額列（例: `E`） |
| `SPREADSHEET_SMS_n` | SMS送信有無（`true` / `false`） |

後方互換性として `SPREADSHEET_ID`（サフィックスなし）も動作する（kanaモード・SMS送信あり）。

## 注意事項

- `spreadsheet.py:35` の `_get_worksheet()` は `os.environ["SPREADSHEET_ID"]` を直接参照するため、`main.py` が各スプレッドシートのループ前に `os.environ["SPREADSHEET_ID"]` を上書きして対応している
- 楽楽販売APIのレート制限（1分20リクエスト）により、大量照合時は処理に時間がかかる
- `要確認` で始まる結果は赤文字でフォーマットされる（`spreadsheet.write_result()` 内で制御）
- `logs/matching.log` にファイルログを保存（ディレクトリは自動生成）
