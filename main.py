"""
main.py - 銀行振込照合ツール エントリーポイント

Usage:
    python3 main.py                    # 当月シート（例: 202604）で本番実行
    python3 main.py --sheet 202603     # 指定月シートで実行
    python3 main.py --dry-run          # ドライラン（照合のみ・更新なし）
"""

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

import rakuraku
import spreadsheet
import sms
from matching import MatchResult, match_record

# 楽楽販売CSVの電話番号列名
TELNO_COLUMNS = ["（日程調整）請求先電話番号_1", "（日程調整）請求先電話番号_2"]

def extract_amount(amount_str: str) -> int:
    """金額文字列（カンマ入り、円など）から数値を抽出"""
    s = re.sub(r'[^\d]', '', str(amount_str))
    return int(s) if s else 0

def get_rakuraku_amount(rec: dict) -> int:
    """楽楽側の金額を取得。"""
    for col in ["（日程調整）請求金額（税込）", "（日程調整）請求金額", "請求金額"]:
        val = rec.get(col, "").strip()
        if val:
            return extract_amount(val)
    return 0

# ── ログ設定 ─────────────────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "matching.log"


def setup_logging(dry_run: bool, sheet_only: bool = False) -> None:
    LOG_DIR.mkdir(exist_ok=True)

    log_format = "%(asctime)s [%(levelname)s] %(message)s"
    handlers: list[logging.Handler] = [
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers,
    )

    if dry_run:
        mode = "DRY-RUN"
    elif sheet_only:
        mode = "スプシのみ"
    else:
        mode = "本番"
    logging.getLogger(__name__).info(f"=== 銀行振込照合ツール 起動（{mode}モード）===")


# ── メイン処理 ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="銀行振込照合ツール")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ドライラン（スプシ・楽楽への書き込みを行わない）",
    )
    parser.add_argument(
        "--sheet-only",
        action="store_true",
        help="スプシのみ更新（楽楽フラグ更新・SMS送信はスキップ）",
    )
    parser.add_argument(
        "--sheet",
        default=None,
        metavar="YYYYMM",
        help="対象シート名（例: 202603）。省略時は当月。",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run
    sheet_only: bool = args.sheet_only
    sheet_name: str | None = args.sheet

    load_dotenv()
    setup_logging(dry_run, sheet_only)
    logger = logging.getLogger(__name__)

    # ── 1. 楽楽販売 顧客データ全件取得 ──────────────────────────────────
    logger.info("楽楽販売から顧客データを取得中...")
    all_records = rakuraku.fetch_all_records()

    # 「進捗」列に「重複」が含まれるレコードを除外
    rakuraku_records = [r for r in all_records if "重複" not in r.get("進捗", "")]
    excluded = len(all_records) - len(rakuraku_records)
    if excluded:
        logger.info(f"楽楽販売: 重複レコード {excluded}件を除外（残り{len(rakuraku_records)}件）")

    # 「銀振照合済み」が既に「入金確認済み」のレコードをインデックス化
    already_confirmed_ids = {
        rec.get("注文ID")
        for rec in rakuraku_records
        if rec.get("銀行振り込み照合済み") == "照合済み"
    }
    logger.info(f"楽楽販売: 既に「入金確認済み」のレコード {len(already_confirmed_ids)}件")

    # 当日ループ内で既に照合した注文IDを保持するセット
    matched_order_ids = set()

    # ── 3. スプレッドシート読み込み＆照合処理 ──────────────────────────────────────
    SPREADSHEETS_CONFIG = []
    for i in range(1, 5):
        sp_id = os.environ.get(f"SPREADSHEET_ID_{i}")
        if sp_id:
            SPREADSHEETS_CONFIG.append({
                "id": sp_id,
                "mode": os.environ.get(f"SPREADSHEET_MATCH_MODE_{i}", "kana"),
                "name_col": os.environ.get(f"SPREADSHEET_NAME_COL_{i}", "D"),
                "date_col": os.environ.get(f"SPREADSHEET_DATE_COL_{i}", "C"),
                "amount_col": os.environ.get(f"SPREADSHEET_AMOUNT_COL_{i}", "E"),
            })
    
    # 後方互換性
    if not SPREADSHEETS_CONFIG and "SPREADSHEET_ID" in os.environ:
        SPREADSHEETS_CONFIG.append({
            "id": os.environ["SPREADSHEET_ID"],
            "mode": "kana",
            "name_col": "D",
            "date_col": "C",
            "amount_col": "E",
        })

    logger.info(f"設定されているスプレッドシートは {len(SPREADSHEETS_CONFIG)} 件です。")

    # ── 3. 照合処理 ──────────────────────────────────────────────────────
    stats = {
        "matched": 0,
        "amount_mismatch": 0,
        "no_match": 0,
        "skip": 0,
        "no_kana": 0,
        "error": 0,
        "already_confirmed": 0,
        "sms_sent": 0,
        "sms_failed": 0,
    }
    
    total_target_rows = 0

    for idx, config in enumerate(SPREADSHEETS_CONFIG, start=1):
        logger.info(f"--- スプレッドシート {idx}件目 の処理を開始します ---")
        os.environ["SPREADSHEET_ID"] = config["id"] # _get_worksheetが参照するため一時的に上書き
        ws = spreadsheet._get_worksheet(sheet_name)
        sheet_rows = spreadsheet.get_sheet_rows(
            ws, 
            name_col=config["name_col"], 
            date_col=config["date_col"], 
            amount_col=config["amount_col"]
        )

        # 処理対象行を絞る（A列が「照合済み」で始まる行はスキップ）
        target_rows = [r for r in sheet_rows if not r["result"].startswith("照合済み")]
        total_target_rows += len(target_rows)
        logger.info(f"処理対象: {len(target_rows)}行（全{len(sheet_rows)}行中）")

        for row in target_rows:
            row_idx = row["row_index"]
            name_value = row["name"]
            date_value = row["date"].replace("-", "/") # YYYY/MM/DD に統一
            amount_value = extract_amount(row["amount"])

            if not name_value:
                logger.debug(f"行{row_idx}: 名義が空のためスキップ")
                stats["skip"] += 1
                continue

            result_type, matched_rec = match_record(name_value, rakuraku_records, mode=config["mode"])

            if result_type == MatchResult.SKIP:
                logger.info(f"行{row_idx}: [{name_value}] → スキップ（法人・手数料等）")
                spreadsheet.write_result(ws, row_idx, "要確認", dry_run=dry_run)
                stats["skip"] += 1
                continue

            elif result_type == MatchResult.MATCHED:
                rec_id = matched_rec.get("注文ID", "")
                tebai_no = matched_rec.get("手配番号", "")

                # 金額チェック（スプシ・楽楽両方に値があり一致する場合のみ通過）
                rakuraku_amount = get_rakuraku_amount(matched_rec)
                if amount_value == 0:
                    logger.warning(f"行{row_idx}: [{name_value}] → スプシ金額が未入力")
                    spreadsheet.write_result(ws, row_idx, "要確認（金額未入力）", dry_run=dry_run)
                    stats["amount_mismatch"] += 1
                    continue
                if rakuraku_amount == 0:
                    logger.warning(f"行{row_idx}: [{name_value}] → 楽楽金額が未入力")
                    spreadsheet.write_result(ws, row_idx, "要確認（金額未入力）", dry_run=dry_run)
                    stats["amount_mismatch"] += 1
                    continue
                if amount_value != rakuraku_amount:
                    logger.warning(f"行{row_idx}: [{name_value}] → 金額不一致（スプシ: {amount_value}, 楽楽: {rakuraku_amount}）")
                    spreadsheet.write_result(ws, row_idx, "要確認（金額不一致）", dry_run=dry_run)
                    stats["amount_mismatch"] += 1
                    continue

                # 楽楽側が既に入金確認済み、または当日別スプシで確認済みの場合もスキップ
                if rec_id in already_confirmed_ids or rec_id in matched_order_ids:
                    logger.info(f"行{row_idx}: [{name_value}] → 楽楽側で既に入金確認済み（スキップ）")
                    spreadsheet.write_result(ws, row_idx, "照合済み（楽楽確認済み）", dry_run=dry_run)
                    stats["already_confirmed"] += 1
                    continue

                # スプシA列に照合済み（手配番号）を書き込み
                spreadsheet.write_result(ws, row_idx, f"照合済み（{tebai_no}）", dry_run=dry_run)
                matched_order_ids.add(rec_id)

                # 楽楽販売フラグ更新・SMS送信（sheet_onlyモードはスキップ）
                if sheet_only:
                    logger.info(f"行{row_idx}: [{name_value}] → [SHEET-ONLY] 楽楽更新・SMS送信スキップ")
                else:
                    # 楽楽販売フラグ更新（成約管理DB + 問合せ管理DB）
                    toiawase_id = matched_rec.get("問い合わせ管理リンク", "").strip()
                    rakuraku.update_kinfu_flags(rec_id, toiawase_id, nyukin_date=date_value, dry_run=dry_run)

                # SMS送信（電話番号_1 → なければ _2 を使用）
                telno = ""
                for col in TELNO_COLUMNS:
                    telno = matched_rec.get(col, "").strip()
                    if telno:
                        break

                if telno:
                    ok = sms.send_sms(telno, tebai_no, date=date_value, amount=str(amount_value), dry_run=dry_run)
                    if ok:
                        stats["sms_sent"] += 1
                    else:
                        stats["sms_failed"] += 1
                else:
                    logger.warning(f"行{row_idx}: [{name_value}] → 電話番号未登録のためSMSスキップ")

                stats["matched"] += 1

            elif result_type == MatchResult.NO_MATCH:
                spreadsheet.write_result(ws, row_idx, "要確認", dry_run=dry_run)
                stats["no_match"] += 1

            elif result_type == MatchResult.NO_KANA:
                spreadsheet.write_result(ws, row_idx, "要確認（フリカナ未登録）", dry_run=dry_run)
                stats["no_kana"] += 1

            elif result_type == MatchResult.ERROR:
                spreadsheet.write_result(ws, row_idx, "要確認（エラー）", dry_run=dry_run)
                stats["error"] += 1

    # ── 4. 集計ログ ──────────────────────────────────────────────────────
    logger.info("=== 全スプレッドシート処理完了 ===")
    logger.info(f"  照合済み（MATCH）  : {stats['matched']}件")
    logger.info(f"  要確認（NO_MATCH） : {stats['no_match']}件")
    logger.info(f"  要確認（金額不一致）: {stats['amount_mismatch']}件")
    logger.info(f"  要確認（候補なし） : {stats['no_kana']}件")
    logger.info(f"  要確認（エラー）   : {stats['error']}件")
    logger.info(f"  スキップ           : {stats['skip']}件")
    logger.info(f"  楽楽側照合済み済   : {stats['already_confirmed']}件")
    logger.info(f"  SMS送信成功        : {stats['sms_sent']}件")
    logger.info(f"  SMS送信失敗        : {stats['sms_failed']}件")
    logger.info(f"  合計処理対象       : {total_target_rows}行")


if __name__ == "__main__":
    main()
