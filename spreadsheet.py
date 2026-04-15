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


def get_sheet_rows(worksheet: gspread.Worksheet) -> list[dict]:
    """
    スプレッドシートから全行を読み込む。

    Returns:
        list[dict]: 各行を {"row_index": int, "b": str, "c": str, "d": str} で返す
                    row_index はスプシの行番号（1始まり、ヘッダ行を含む）
    """
    # A〜D列を全取得（ヘッダ行除く）
    all_values = worksheet.get_all_values()
    rows = []
    for i, row in enumerate(all_values[1:], start=2):  # 2行目からデータ
        # 列が足りない場合は空文字で補完
        padded = row + [''] * (4 - len(row))
        rows.append({
            "row_index": i,
            "b": padded[1].strip(),  # B列：照合結果（既存値）
            "c": padded[2].strip(),  # C列：入金日
            "d": padded[3].strip(),  # D列：口座名義
        })
    logger.info(f"スプレッドシート: {len(rows)}行読み込み完了")
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
        logger.info(f"[DRY-RUN] 行{row_index} B列 ← {result!r}（スキップ）")
        return

    worksheet.update_cell(row_index, 2, result)
    logger.debug(f"スプレッドシート: 行{row_index} B列 ← {result!r}")


