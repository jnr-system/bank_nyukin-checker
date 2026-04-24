"""
debug_fetch.py - 楽楽販売から取得できるデータの確認用スクリプト
"""

import json
from dotenv import load_dotenv
import rakuraku

load_dotenv()

# 10件だけ取得して確認
records = rakuraku.fetch_all_records(limit=10)

print(f"\n取得件数: {len(records)}件")
print("=" * 60)

for i, rec in enumerate(records, 1):
    print(f"\n--- レコード {i} ---")
    for key, value in rec.items():
        if value.strip():  # 空の列は省略
            print(f"  {key}: {value}")
