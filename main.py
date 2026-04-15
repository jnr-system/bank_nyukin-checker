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
import sys
from pathlib import Path

from dotenv import load_dotenv

import rakuraku
import spreadsheet
from matching import MatchResult, match_record

# ── ログ設定 ─────────────────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "matching.log"


def setup_logging(dry_run: bool) -> None:
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

    mode = "DRY-RUN" if dry_run else "本番"
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
        "--sheet",
        default=None,
        metavar="YYYYMM",
        help="対象シート名（例: 202603）。省略時は当月。",
    )
    args = parser.parse_args()
    dry_run: bool = args.dry_run
    sheet_name: str | None = args.sheet

    load_dotenv()
    setup_logging(dry_run)
    logger = logging.getLogger(__name__)

    # ── 1. スプレッドシート読み込み ──────────────────────────────────────
    logger.info("スプレッドシートに接続中...")
    ws = spreadsheet._get_worksheet(sheet_name)
    sheet_rows = spreadsheet.get_sheet_rows(ws)

    # 処理対象行を絞る（B列が「照合済み」で始まる行はスキップ）
    target_rows = [r for r in sheet_rows if not r["b"].startswith("照合済み")]
    logger.info(f"処理対象: {len(target_rows)}行（全{len(sheet_rows)}行中）")

    # ── 2. 楽楽販売 顧客データ全件取得 ──────────────────────────────────
    logger.info("楽楽販売から顧客データを取得中...")
    rakuraku_records = rakuraku.fetch_all_records()

    # 「銀振照合済み」が既に「入金確認済み」のレコードをインデックス化
    already_confirmed_ids = {
        rec.get("記録ID")
        for rec in rakuraku_records
        if rec.get("銀振照合済み") == "入金確認済み"
    }
    logger.info(f"楽楽販売: 既に「入金確認済み」のレコード {len(already_confirmed_ids)}件")

    # ── 3. 照合処理 ──────────────────────────────────────────────────────
    stats = {
        "matched": 0,
        "no_match": 0,
        "skip": 0,
        "no_kana": 0,
        "error": 0,
        "already_confirmed": 0,
    }

    for row in target_rows:
        row_idx = row["row_index"]
        d_value = row["d"]

        if not d_value:
            logger.debug(f"行{row_idx}: D列が空のためスキップ")
            stats["skip"] += 1
            continue

        result_type, matched_rec = match_record(d_value, rakuraku_records)

        if result_type == MatchResult.SKIP:
            logger.info(f"行{row_idx}: [{d_value}] → スキップ（法人・手数料等）")
            stats["skip"] += 1
            continue

        elif result_type == MatchResult.MATCHED:
            rec_id = matched_rec.get("記録ID", "")
            tebai_no = matched_rec.get("手配番号", "")

            # 楽楽側が既に入金確認済みの場合もスキップ
            if rec_id in already_confirmed_ids:
                logger.info(f"行{row_idx}: [{d_value}] → 楽楽側で既に入金確認済み（スキップ）")
                stats["already_confirmed"] += 1
                continue

            # スプシB列に照合済み（手配番号）を書き込み
            spreadsheet.write_result(ws, row_idx, f"照合済み（{tebai_no}）", dry_run=dry_run)

            # 楽楽販売フラグ更新
            rakuraku.update_kinfu_flag(rec_id, dry_run=dry_run)

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
    logger.info("=== 処理完了 ===")
    logger.info(f"  照合済み（MATCH）  : {stats['matched']}件")
    logger.info(f"  要確認（NO_MATCH） : {stats['no_match']}件")
    logger.info(f"  要確認（候補なし） : {stats['no_kana']}件")
    logger.info(f"  要確認（エラー）   : {stats['error']}件")
    logger.info(f"  スキップ           : {stats['skip']}件")
    logger.info(f"  楽楽側照合済み済   : {stats['already_confirmed']}件")
    logger.info(f"  合計処理対象       : {len(target_rows)}行")


if __name__ == "__main__":
    main()
