"""
rakuraku.py - 楽楽販売APIとの通信（顧客データ取得・フラグ更新）
"""

import os
import csv
import io
import time
import logging
import requests

logger = logging.getLogger(__name__)

RAKURAKU_DOMAIN = "hntobias.rakurakuhanbai.jp"
BASE_URL = f"https://{RAKURAKU_DOMAIN}/mspy4wa"

DB_SCHEMA_ID = "101181"
LIST_ID = "101520"
REQUEST_INTERVAL = 1.0  # 秒（レート制限：1分20リクエスト）


def _headers() -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-HD-apitoken": os.environ["RAKURAKU_TOKEN"],
    }


def fetch_all_records() -> list[dict]:
    """
    CSVエクスポートAPIから顧客データを全件取得する（最大5000件・1リクエスト）。

    Returns:
        list[dict]: 各レコードをdictにした一覧
    """
    payload = {
        "dbSchemaId": DB_SCHEMA_ID,
        "listId": LIST_ID,
        "limit": 5000,
        "offset": 1,
    }
    resp = requests.post(
        f"{BASE_URL}/api/csvexport/version/v1",
        json=payload,
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()

    # レスポンスはCSVテキスト（BOM除去）
    csv_text = resp.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(csv_text))
    records = list(reader)

    logger.info(f"楽楽販売: {len(records)}件取得完了")
    return records


def update_kinfu_flag(record_id: str | int, dry_run: bool = False) -> bool:
    """
    指定レコードの「銀振照合済み」を「入金確認済み」に更新する。

    Args:
        record_id: 楽楽販売の記録ID
        dry_run: Trueの場合は更新せずログのみ出力

    Returns:
        bool: 成功した場合True
    """
    kinfu_item_id = os.environ.get("RAKURAKU_KINFU_ITEM_ID", "")
    if not kinfu_item_id:
        logger.error("RAKURAKU_KINFU_ITEM_ID が設定されていません")
        return False

    if dry_run:
        logger.info(f"[DRY-RUN] 記録ID={record_id} の銀振照合済みを更新（スキップ）")
        return True

    payload = {
        "dbSchemaId": DB_SCHEMA_ID,
        "id": int(record_id),
        "values": {
            kinfu_item_id: "入金確認済み",
        },
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/apirecord/update/version/v1",
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        logger.info(f"楽楽販売: 記録ID={record_id} を「入金確認済み」に更新しました")
        time.sleep(REQUEST_INTERVAL)
        return True
    except requests.RequestException as e:
        logger.error(f"楽楽販売: 記録ID={record_id} の更新に失敗しました: {e}")
        return False
