"""
rakuraku.py - 楽楽販売APIとの通信（顧客データ取得・フラグ更新）
"""

import os
import csv
import io
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

RAKURAKU_DOMAIN = "hntobias.rakurakuhanbai.jp"
BASE_URL = f"https://{RAKURAKU_DOMAIN}/mspy4wa"

# 成約管理DB（データ取得・更新）
SEIYAKU_DB_SCHEMA_ID = "101185"
LIST_ID = "101490"
SEARCH_ID = "107915"
SEIYAKU_KINFU_ITEM_ID = "116378"  # 成約管理の照合済み項目ID
SEIYAKU_NYUKIN_DATE_ITEM_ID = "111590"  # 入金日項目ID

# 問合せ管理DB（更新のみ）
TOIAWASE_DB_SCHEMA_ID = "101181"
TOIAWASE_KINFU_ITEM_ID = "116376"  # 問合せ管理の照合済み項目ID

KINFU_VALUE = "照合済み"
REQUEST_INTERVAL = 1.0  # 秒（レート制限：1分20リクエスト）

# 必須項目が未入力で更新が弾かれた場合に補完する値
REQUIRED_FIELD_DEFAULT = "なし"


def _missing_required_item_ids(body: str) -> list[str]:
    """
    更新失敗レスポンスから「'＊XXX'を入力してください。」で弾かれた
    必須項目の項目IDを抽出する。
    """
    if not body:
        return []
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return []

    headers = (data.get("errors") or {}).get("description", {}).get("header") or []
    item_ids = []
    for h in headers:
        # value が空のまま「入力してください」と弾かれた必須項目を対象にする
        if h.get("name") and not h.get("value") and "入力してください" in (h.get("msg") or ""):
            item_ids.append(str(h["name"]))
    return item_ids


def _headers() -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-HD-apitoken": os.environ["RAKURAKU_TOKEN"],
    }


def fetch_all_records(limit: int = 5000) -> list[dict]:
    """
    CSVエクスポートAPIから顧客データを全件取得する（最大5000件・1リクエスト）。

    Returns:
        list[dict]: 各レコードをdictにした一覧
    """
    payload = {
        "dbSchemaId": SEIYAKU_DB_SCHEMA_ID,
        "listId": LIST_ID,
        "searchId": SEARCH_ID,
        "limit": limit,
        "offset": 1,
    }
    resp = requests.post(
        f"{BASE_URL}/api/csvexport/version/v1",
        json=payload,
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()

    csv_text = resp.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(csv_text))
    records = list(reader)

    logger.info(f"楽楽販売: {len(records)}件取得完了")
    return records


def _update_record(db_schema_id: str, record_id: str, values: dict, label: str, _retried: bool = False) -> bool:
    """指定DBの指定レコードの複数項目を更新する共通処理。

    必須項目が未入力で弾かれた場合は、その項目に「なし」を補って1度だけ再試行する。
    """
    payload = {
        "dbSchemaId": db_schema_id,
        "keyId": record_id,
        "values": values,
    }
    try:
        resp = requests.post(
            f"{BASE_URL}/apirecord/update/version/v1",
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        logger.info(f"楽楽販売({label}): ID={record_id} の値を更新しました ({values})")
        time.sleep(REQUEST_INTERVAL)
        return True
    except requests.RequestException as e:
        body = getattr(getattr(e, "response", None), "text", "") or ""

        # 必須項目が未入力で弾かれた場合は「なし」を補って1度だけ再試行する
        if not _retried:
            missing = [iid for iid in _missing_required_item_ids(body) if iid not in values]
            if missing:
                retry_values = {**values, **{iid: REQUIRED_FIELD_DEFAULT for iid in missing}}
                logger.warning(
                    f"楽楽販売({label}): ID={record_id} の必須項目 {missing} が未入力のため "
                    f"「{REQUIRED_FIELD_DEFAULT}」を補って再試行します"
                )
                time.sleep(REQUEST_INTERVAL)
                return _update_record(db_schema_id, record_id, retry_values, label, _retried=True)

        logger.error(
            f"楽楽販売({label}): ID={record_id} の更新に失敗しました: {e} "
            f"/ payload={values} / response={body}"
        )
        return False


def update_kinfu_flags(seiyaku_id: str, toiawase_id: str, nyukin_date: str = "", dry_run: bool = False) -> bool:
    """
    成約管理DBと問合せ管理DBの両方の照合済みフラグを更新する。入金日も更新する。

    Args:
        seiyaku_id: 成約管理DBの注文ID
        toiawase_id: 問合せ管理DBの記録ID（問い合わせ管理リンク）
        nyukin_date: 入金日文字列
        dry_run: Trueの場合は更新せずログのみ出力

    Returns:
        bool: 両方成功した場合True
    """
    if dry_run:
        logger.info(f"[DRY-RUN] 成約管理ID={seiyaku_id} / 問合せ管理ID={toiawase_id} の照合済み・入金日({nyukin_date})を更新（スキップ）")
        return True

    seiyaku_values = {
        SEIYAKU_KINFU_ITEM_ID: KINFU_VALUE,
    }
    if nyukin_date:
        seiyaku_values[SEIYAKU_NYUKIN_DATE_ITEM_ID] = nyukin_date

    ok1 = _update_record(SEIYAKU_DB_SCHEMA_ID, seiyaku_id, seiyaku_values, "成約管理")
    
    ok2 = True
    if toiawase_id:
        toiawase_kinfu_value = f"{nyukin_date} 入金確認済み" if nyukin_date else "入金確認済み"
        toiawase_values = {
            TOIAWASE_KINFU_ITEM_ID: toiawase_kinfu_value,
        }
        ok2 = _update_record(TOIAWASE_DB_SCHEMA_ID, toiawase_id, toiawase_values, "問合せ管理")
    else:
        logger.warning(f"成約管理ID={seiyaku_id}: 問い合わせ管理リンクが未設定のため問合せ管理DBの更新をスキップ")

    return ok1 and ok2
