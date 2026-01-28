
import os
import sys
import logging
from dotenv import load_dotenv
import requests

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Diagnostic")

def run_diagnostic():
    load_dotenv()
    
    app_id = os.getenv("FEISHU_APP_ID", "").strip().replace('"', '').replace("'", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip().replace('"', '').replace("'", "")
    
    print("\n" + "="*50)
    print("🔍 飞书凭证诊断工具")
    print("="*50)
    
    # 1. 检查环境变量是否存在
    if not app_id:
        print("❌ 错误: 未找到 FEISHU_APP_ID")
    else:
        print(f"✅ FEISHU_APP_ID: {app_id[:5]}... (长度: {len(app_id)})")
        if not app_id.startswith("cli_"):
            print("   ⚠️ 警告: App ID 通常应以 'cli_' 开头")

    if not app_secret:
        print("❌ 错误: 未找到 FEISHU_APP_SECRET")
    else:
        print(f"✅ FEISHU_APP_SECRET: {app_secret[:2]}...{app_secret[-2:]} (长度: {len(app_secret)})")

    if not app_id or not app_secret:
        print("\n请在 .env 文件中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET 后重试。")
        return

    # 2. 尝试调用飞书原生 API 获取 Token
    print("\n正在尝试请求飞书 Token (tenant_access_token)...")
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": app_id,
        "app_secret": app_secret
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        
        if resp.status_code == 200 and data.get("code") == 0:
            print("🎉 成功! 已成功获取 Tenant Access Token。")
            print(f"Token 前缀: {data.get('tenant_access_token')[:10]}...")
            print(f"有效期: {data.get('expire')} 秒")
        else:
            print(f"❌ 失败! 飞书返回错误:")
            print(f"   HTTP 状态码: {resp.status_code}")
            print(f"   错误代码: {data.get('code')}")
            print(f"   错误信息: {data.get('msg')}")
            
            if data.get("code") == 10003:
                print("\n💡 诊断建议 (错误 10003):")
                print("1. 请核对 App ID 和 App Secret 是否完整复制（无多余空格）。")
                print("2. 确认你使用的是 'App Secret' 而不是 'Verification Token'。")
                print("3. 确认应用已在飞书后台「启用」。")
    except Exception as e:
        print(f"💥 请求过程中发生异常: {e}")

    print("="*50 + "\n")

if __name__ == "__main__":
    run_diagnostic()
