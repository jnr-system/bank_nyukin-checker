"""
spreadsheet.py - Google スプレッドシートの読み書き（gspread使用）
"""

import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_worksheet(sheet_name: str | None = None) -> gspread.Worksheet:
    """
    サービスアカウントJSON（環境変数）でGoogle Sheetsに接続する。

    Args:
        sheet_name: シート名（例: "202604"）。Noneの場合は当月のシートを使用。
    """
    if sheet_name is None:
        sheet_name = datetime.now().strftime("%Y%m")

    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sa_info = json.loads(sa_json)
    creds = Credentials.from_service_account_info(sa_info, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.environ["SPREADSHEET_ID"])
    ws = sh.worksheet(sheet_name)
    logger.info(f"スプレッドシート: シート「{sheet_name}」に接続")
    return ws


def col_letter_to_index(col: str) -> int:
    """エクセルの列文字（A, B, C...）を0始まりのインデックスに変換する"""
    col = col.upper()
    idx = 0
    for c in col:
        idx = idx * 26 + (ord(c) - ord('A')) + 1
    return idx - 1

def get_sheet_rows(
    worksheet: gspread.Worksheet,
    name_col: str = "D",
    date_col: str = "C",
    amount_col: str = "E"
) -> list[dict]:
    """
    スプレッドシートから全行を読み込む。

    Returns:
        list[dict]: 各行を {"row_index": int, "result": str, "date": str, "name": str, "amount": str} で返す
                    row_index はスプシの行番号（1始まり、ヘッダ行を含む）
    """
    all_values = worksheet.get_all_values()
    
    idx_name = col_letter_to_index(name_col)
    idx_date = col_letter_to_index(date_col)
    idx_amount = col_letter_to_index(amount_col)
    idx_result = 0 # A列を結果書き込み/読み込み用に固定
    
    max_idx = max(idx_name, idx_date, idx_amount, idx_result)
    
    rows = []
    for i, row in enumerate(all_values[1:], start=2):  # 2行目からデータ
        # 列が足りない場合は空文字で補完
        padded = row + [''] * (max_idx - len(row) + 1)
        rows.append({
            "row_index": i,
            "result": padded[idx_result].strip(),
            "date": padded[idx_date].strip(),
            "name": padded[idx_name].strip(),
            "amount": padded[idx_amount].strip(),
        })
    logger.info(f"スプレッドシート: {len(rows)}行読み込み完了 (対象列: 名義={name_col}, 日付={date_col}, 金額={amount_col})")
    return rows


def write_result(
    worksheet: gspread.Worksheet,
    row_index: int,
    result: str,
    dry_run: bool = False,
) -> None:
    """
    B列に照合結果を書き込む。

    Args:
        worksheet: 対象ワークシート
        row_index: 書き込む行番号（1始まり）
        result: 書き込む文字列（例: "照合済み（2026-04-15 10:32）" or "要確認"）
        dry_run: Trueの場合は書き込まずログのみ
    """
    if dry_run:
        logger.info(f"[DRY-RUN] 行{row_index} A列 ← {result!r}（スキップ）")
        return

    worksheet.update_cell(row_index, 1, result)

    cell_addr = f"A{row_index}"
    if result.startswith("要確認"):
        worksheet.format(cell_addr, {
            "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
            "textFormat": {"foregroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0}},
        })
    else:
        worksheet.format(cell_addr, {
            "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
            "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}},
        })

    logger.debug(f"スプレッドシート: 行{row_index} A列 ← {result!r}")


