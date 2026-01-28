
import os
import sys
from dotenv import load_dotenv

# 添加 src 目录到 python path
sys.path.append(os.path.join(os.getcwd(), "src"))

from video_insight.feishu_syncer import FeishuSyncer

def test_search():
    load_dotenv()
    syncer = FeishuSyncer()
    
    # 1. 先测试标准 API 看看能不能正常获取 Token
    print("正在测试标准 API (list_file)...")
    from lark_oapi.api.drive.v1 import ListFileRequest
    req = ListFileRequest.builder().folder_token("").build()
    resp = syncer.client.drive.v1.file.list(req)
    if resp.success():
        print(f"✅ 标准 API 成功! 找到 {len(resp.data.files) if resp.data.files else 0} 个文件")
    else:
        print(f"❌ 标准 API 失败: {resp.msg} (Code: {resp.code})")
        return

    folder_name = "自动分析"
    print(f"\n正在搜索文件夹: {folder_name}...")
    token = syncer.search_folder(folder_name)
    
    if token:
        print(f"✅ 找到文件夹! Token: {token}")
    else:
        print("❌ 未找到文件夹。")

if __name__ == "__main__":
    test_search()
