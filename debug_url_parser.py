
import re
from typing import Tuple, Optional

def parse_feishu_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    解析飞书链接，支持多维表格链接和知识库链接。
    返回 (app_token, table_id)
    """
    print(f"DEBUG: Parsing URL: {url}")
    try:
        # 1. 检查是否是 Wiki 链接
        wiki_match = re.search(r"\/wiki\/([a-zA-Z0-9]+)", url)
        if wiki_match:
            wiki_token = wiki_match.group(1)
            print(f"DEBUG: Detected Wiki link, token: {wiki_token}")
            # Mock resolve_wiki_token
            return "MOCK_APP_TOKEN", None

        # 2. 检查是否是普通的 Base 链接
        if "/base/" in url:
            part1 = url.split("/base/")[1]
            app_token = part1.split("?")[0].split("/")[0]
            
            table_id = None
            if "table=" in url:
                table_id = url.split("table=")[1].split("&")[0]
            
            print(f"DEBUG: Detected Base link. App Token: {app_token}, Table ID: {table_id}")
            return app_token, table_id
            
        print("DEBUG: No match found.")
        return None, None
    except Exception as e:
        print(f"DEBUG: Error parsing Feishu URL: {e}")
        return None, None

# Test cases
urls = [
    "https://feishu.cn/base/bascn12345?table=tbl67890",
    "https://www.feishu.cn/base/bascn12345",
    "https://feishu.cn/wiki/wikcnABCDE",
    "https://company.feishu.cn/base/bascnXYZ?table=tbl123&view=vew456",
    "https://feishu.cn/base/bascnNoTable",
    "https://feishu.cn/docs/doccnSomeDoc", # Should fail
    "https://larksuite.com/base/bascnLark?table=tblLark"
]

for url in urls:
    print(f"--- Testing: {url} ---")
    parse_feishu_url(url)
