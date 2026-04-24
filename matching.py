"""
matching.py - カナ名の前処理・正規化・Gemini APIによる照合ロジック
"""

import re
import os
import unicodedata
import logging

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-3-flash-preview"

# 楽楽販売CSVのフリカナ列名（優先順）
KANA_COLUMNS = (
    'フリカナ（フォーム申込者）',
    '（日程調整）フリカナ',
    '（日程調整）フリカナ（請求先）',
)


def _get_kana(rec: dict) -> str:
    """レコードから最初に値があるフリカナを返す"""
    for col in KANA_COLUMNS:
        kana = rec.get(col, '').strip()
        if kana:
            return kana
    return ''


# ── ステップ1：照合スキップ行の判定 ─────────────────────────────────────

def should_skip(raw: str) -> bool:
    """法人・手数料・銀行経由振込など照合不要な行を判定する"""
    s = raw.strip()

    # 1. 手数料・利息・システム行
    if re.search(r'手数料|消費税|利息|預金利息|総合振込', s):
        return True

    # 2. 銀行経由振込（他行名が含まれる長文）
    if re.search(r'銀行|信金|信用組合|ゆうちょ', s) and re.search(r'普通預金|当座預金|支店', s):
        return True

    # 3. 法人格の判定（カ）ユ）シヤ）などで始まる、または（カ で終わる）
    if re.search(r'^[\(（]?[カユシ]）', s):
        return True
    if s.endswith('（カ') or s.endswith('(カ'):
        return True

    # 4. カナが全く含まれない行
    if not re.search(r'[ァ-ヶｦ-ﾟ]', s):
        return True

    return False


# ── ステップ2：カナ名の正規化抽出 ────────────────────────────────────────

def normalize_bank_name(raw: str) -> str:
    """銀行の口座名義から照合用のカナ名を抽出・正規化する"""

    # 1. 濁点・半濁点の文字結合（NFCで結合）
    s = unicodedata.normalize('NFC', raw)

    # 2. 先頭のノイズ除去（全角・半角数字、括弧類、英数字プレフィックス）
    s = re.sub(r'^[０-９0-9\(\)（）「」\-―A-ZＡ-Ｚa-zａ-ｚ]+', '', s).strip()

    # 3. 末尾の備考を除去（カナ・スペース・長音符以外が現れた時点で切り捨て）
    match = re.match(r'^([゠-ヿ･-ﾟー\s　\xa0]+)', s)
    s = match.group(1).strip() if match else s

    # 4. 半角カナ → 全角カナ（NFKC正規化）
    s = unicodedata.normalize('NFKC', s)

    # 5. あらゆるスペースを半角スペース1つに統一
    s = re.sub(r'[\s　\xa0]+', ' ', s).strip()

    return s


def extract_name(raw: str) -> str | None:
    """スキップ対象はNone、それ以外は正規化済みカナ名を返す"""
    if should_skip(raw):
        return None
    name = normalize_bank_name(raw)
    # 正規化後にカナが残っていなければスキップ
    if not re.search(r'[ァ-ヶ]', name):
        return None
    return name


# ── 第1段階：正規化完全一致 ──────────────────────────────────────────────

def normalize_for_match(name: str) -> str:
    """照合用に正規化：スペース除去・全角統一"""
    n = unicodedata.normalize('NFKC', name)
    n = re.sub(r'[\s　\xa0]', '', n)
    return n


def find_exact_match(bank_normalized: str, rakuraku_records: list[dict]) -> dict | None:
    """正規化後の完全一致で楽楽レコードを返す（3列のいずれかが一致すればOK）"""
    for rec in rakuraku_records:
        for col in KANA_COLUMNS:
            kana = rec.get(col, '').strip()
            if kana and normalize_for_match(kana) == bank_normalized:
                return rec
    return None


# ── 第2段階：頭文字グルーピング → Gemini一括照合 ─────────────────────────

def get_candidates(bank_normalized: str, rakuraku_records: list[dict]) -> list[dict]:
    """銀行名の先頭1文字と一致する行グループを返す（3列のいずれかが一致すればOK）"""
    first_char = bank_normalized[0] if bank_normalized else ''
    candidates = []
    for rec in rakuraku_records:
        for col in KANA_COLUMNS:
            kana = rec.get(col, '').strip()
            if not kana or not re.search(r'[ァ-ヶ]', kana):
                continue
            kana_normalized = normalize_for_match(kana)
            if kana_normalized and kana_normalized[0] == first_char:
                candidates.append(rec)
                break  # このレコードは追加済みなので次のレコードへ
    return candidates


def build_prompt(bank_name: str, candidates: list[dict]) -> str:
    candidate_list = '\n'.join(
        f"{i+1}: {_get_kana(rec)}"
        for i, rec in enumerate(candidates)
    )
    return f"""銀行振込人名（正規化済み・全角カナ）: {bank_name}

以下の顧客リストの中から、上記と同一人物を選んでください。

{candidate_list}

判定ルール：
- スペースの有無・位置の違いは無視する
- 楽楽側が姓のみ（例：「サトウ」）の場合、銀行側に姓が含まれていても確信が持てなければ 0 を返す
- 明らかに同一人物と判断できる場合のみ番号を返す

一致する番号を1つだけ返してください。該当なしまたは確信が持てない場合は 0 を返してください。
数字のみ返答し、余分な文字は不要です。
"""


def call_gemini(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(GEMINI_MODEL)
    response = model.generate_content(prompt)
    return response.text.strip()


def gemini_match(bank_name: str, candidates: list[dict]) -> dict | None:
    if not candidates:
        return None
    prompt = build_prompt(bank_name, candidates)
    response = call_gemini(prompt)
    try:
        idx = int(response.strip())
        if idx == 0:
            return None
        return candidates[idx - 1]
    except (ValueError, IndexError):
        raise ValueError(f"Gemini不正レスポンス: {response!r}")


# ── メイン照合エントリポイント ────────────────────────────────────────────

class MatchResult:
    MATCHED = "matched"
    NO_MATCH = "no_match"
    SKIP = "skip"
    ERROR = "error"
    NO_KANA = "no_kana"


def match_record(raw_bank_name: str, rakuraku_records: list[dict]) -> tuple[str, dict | None]:
    """
    1件の銀行口座名義を照合する。

    Returns:
        (result_type, matched_record_or_None)
        result_type: MatchResult の定数
    """
    # 第0段階：スキップ判定
    bank_name = extract_name(raw_bank_name)
    if bank_name is None:
        return MatchResult.SKIP, None

    bank_normalized = normalize_for_match(bank_name)

    # 第1段階：完全一致
    exact = find_exact_match(bank_normalized, rakuraku_records)
    if exact:
        logger.info(f"[完全一致] {bank_name} → {_get_kana(exact)} (記録ID={exact.get('記録ID')})")
        return MatchResult.MATCHED, exact

    # 第2段階：Gemini照合
    candidates = get_candidates(bank_normalized, rakuraku_records)
    if not candidates:
        logger.info(f"[候補なし] {bank_name} → フリカナ未登録")
        return MatchResult.NO_KANA, None

    try:
        matched = gemini_match(bank_name, candidates)
        if matched:
            logger.info(f"[Gemini一致] {bank_name} → {_get_kana(matched)} (記録ID={matched.get('記録ID')})")
            return MatchResult.MATCHED, matched
        else:
            logger.info(f"[不一致] {bank_name} → NO_MATCH")
            return MatchResult.NO_MATCH, None
    except Exception as e:
        logger.error(f"[エラー] {bank_name} → {e}")
        return MatchResult.ERROR, None
