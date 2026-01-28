from video_insight.feishu_syncer import FeishuSyncer
import logging

# 配置日志以便看到更多信息
logging.basicConfig(level=logging.INFO)

app_token = "PElTbhUNBa4BqPsQoAJcEdnMnug"
table_id = "tblbANdNhWMHPs9F"

print(f"Connecting to Feishu App: {app_token}, Table: {table_id}")
syncer = FeishuSyncer()
fields = syncer.get_table_field_types(app_token, table_id)

print(f"\nCurrent Table Fields ({len(fields)} found):")
for name, type_id in fields.items():
    print(f" - '{name}' (Type: {type_id})")

print("-" * 30)
expected_fields = [
    '素材名称', '视频链接', '缩略图', 
    '人群', '功能', '场景', '痛点', '概述', '分析', 
    '展现', '点击', '消耗', '激活人数', '点击率', '转换率', '来源'
]

print("Checking against expected fields:")
missing_count = 0
for expected in expected_fields:
    if expected not in fields:
        print(f" ❌ [MISSING] '{expected}' - Code will fail when writing this field")
        missing_count += 1
    else:
        print(f" ✅ [OK] '{expected}'")

if missing_count == 0:
    print("\nAll expected fields are present. The error might be due to a specific record or hidden character.")
else:
    print(f"\nFound {missing_count} missing fields. This is likely the cause of 'FieldNameNotFound'.")
