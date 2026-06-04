"""
sms.py - accreteのSMS送信API連携
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

ACCRETE_API_URL = "https://api.acrt.jp/ibss/api/sms_reg/{account_id}/json"

SMS_MESSAGE_TEMPLATE = (
    "【正直屋】\n"
    "平素よりお世話になっております。\n"
    "この度はご入金頂き、誠にありがとうございました。\n"
    "以下の通りご入金を確認いたしました。\n"
    "\n"
    "ご入金日：{date}\n"
    "ご入金金額：{amount}円\n"
    "\n"
    "引き続き何卒よろしくお願いいたします。"
)


def _clean_phone(telno: str) -> str:
    """電話番号からハイフン・スペースを除去して返す"""
    return telno.replace("-", "").replace(" ", "").replace("　", "").strip()


def send_sms(telno: str, tehai_no: str, date: str = "", amount: str = "", dry_run: bool = False) -> bool:
    """
    指定の電話番号にSMSを送信する。

    Args:
        telno: 送信先電話番号（ハイフンありでも可）
        tehai_no: 手配番号（メッセージ本文に埋め込む）
        date: 入金日
        amount: 入金金額
        dry_run: Trueの場合は送信せずログのみ

    Returns:
        bool: 成功した場合True
    """
    account_id = os.environ.get("ACCRETE_ACCOUNT_ID", "")
    request_id = os.environ.get("ACCRETE_REQUEST_ID", "")
    password = os.environ.get("ACCRETE_PASSWORD", "")

    if not all([account_id, request_id, password]):
        logger.error("ACCRETE_ACCOUNT_ID / ACCRETE_REQUEST_ID / ACCRETE_PASSWORD が設定されていません")
        return False

    cleaned = _clean_phone(telno)
    if not cleaned:
        logger.warning(f"SMS送信スキップ: 電話番号が空（手配番号={tehai_no}）")
        return False

    amount_formatted = f"{int(amount):,}" if amount.isdigit() else amount
    message = SMS_MESSAGE_TEMPLATE.format(date=date, amount=amount_formatted)

    if dry_run:
        logger.info(f"[DRY-RUN] SMS送信スキップ: telno={cleaned} tehai_no={tehai_no} 本文={message!r}")
        return True

    url = ACCRETE_API_URL.format(account_id=account_id)
    data = {
        "id": request_id,
        "pass": password,
        "telno": cleaned,
        "text.long": message,
    }

    try:
        resp = requests.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        try:
            body = resp.json()
        except Exception:
            body = {}
        result_code = body.get("result_code", "")
        if result_code == "0000":
            logger.info(f"SMS送信成功: telno={cleaned} tehai_no={tehai_no} delivery_id={body.get('delivery_id')}")
            return True
        else:
            logger.error(
                f"SMS送信失敗: telno={cleaned} tehai_no={tehai_no} "
                f"http_status={resp.status_code} result_code={result_code} message={body.get('message')} body={body}"
            )
            return False
    except requests.RequestException as e:
        logger.error(f"SMS送信エラー: telno={cleaned} tehai_no={tehai_no} error={e}")
        return False
