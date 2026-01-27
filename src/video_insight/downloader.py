import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tqdm import tqdm
from .config import config

class FeishuClient:
    """Feishu Bitable API Client for downloading records."""
    
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = self._get_tenant_access_token()
        self.headers = {
            "Authorization": f"Bearer {self.token}", 
            "Content-Type": "application/json"
        }

    def _get_tenant_access_token(self) -> str:
        """Get Tenant Access Token."""
        url = f"{config.FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
        return res.json().get("tenant_access_token")

    def get_all_records(self, app_token: str, table_id: str) -> list:
        """Fetch all records from a table with pagination."""
        all_records = []
        page_token = ""
        has_more = True
        
        print("🔍 Fetching data from Feishu Bitable...")
        while has_more:
            url = f"{config.FEISHU_DOMAIN}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            params = {"page_size": 100, "page_token": page_token}
            try:
                res = requests.get(url, headers=self.headers, params=params, timeout=20)
                res.raise_for_status()
                data = res.json().get("data", {})
                
                items = data.get("items", [])
                all_records.extend(items)
                
                has_more = data.get("has_more", False)
                page_token = data.get("page_token", "")
            except Exception as e:
                print(f"❌ Error fetching records: {e}")
                break
        
        print(f"✅ Successfully fetched {len(all_records)} records")
        return all_records

class VideoDownloader:
    """Incremental Video Downloader."""
    
    FIELDS = {
        "NAME": "素材名称",
        "URL": "视频链接"
    }

    def __init__(self, output_dir: Path, max_workers: int = 5):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.session = requests.Session()

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to remove illegal characters."""
        if not filename: return "unnamed_video"
        name = re.sub(r'[\\/*?:"<>|]', '_', str(filename))
        return name.strip()

    def download_single(self, name: str, url: str) -> tuple[bool, str, str]:
        """Download a single video."""
        try:
            # 1. Validate URL
            if not url or not str(url).startswith("http"):
                return False, name, "Invalid URL"

            # 2. Prepare filename
            clean_name = self.sanitize_filename(name)
            if not clean_name.lower().endswith(".mp4"):
                clean_name += ".mp4"
            
            file_path = self.output_dir / clean_name

            # 3. Incremental check
            if file_path.exists() and file_path.stat().st_size > 0:
                return True, name, "Skipped (Exists)"

            # 4. Stream download
            resp = self.session.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            
            with open(file_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024*1024): # 1MB chunk
                    f.write(chunk)
            
            return True, name, "Success"
        except Exception as e:
            return False, name, str(e)

    def start(self, records: list, progress_callback=None):
        """Start concurrent download tasks."""
        tasks = []
        
        for r in records:
            fields = r.get("fields", {})
            name = fields.get(self.FIELDS["NAME"])
            url_field = fields.get(self.FIELDS["URL"])
            
            url = ""
            if isinstance(url_field, str):
                url = url_field
            elif isinstance(url_field, list) and len(url_field) > 0:
                url = url_field[0].get("url", "") or url_field[0].get("link", "") or url_field[0].get("text", "")
            elif isinstance(url_field, dict):
                url = url_field.get("url", "") or url_field.get("link", "")

            if name and url:
                tasks.append((name, url))

        if not tasks:
            print("\n⚠️ No valid video links found.")
            if progress_callback:
                progress_callback("⚠️ 未找到有效的视频链接。")
            return

        print(f"🚀 Starting download (Threads: {self.max_workers})...")
        if progress_callback:
            progress_callback(f"🚀 任务已开始，正在下载视频，共计 {len(tasks)} 条...")

        success_count = 0
        skip_count = 0
        fail_count = 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_video = {executor.submit(self.download_single, n, u): n for n, u in tasks}
            
            with tqdm(total=len(tasks), desc="Progress") as pbar:
                for future in as_completed(future_to_video):
                    success, name, msg = future.result()
                    if success:
                        if msg == "Skipped (Exists)":
                            skip_count += 1
                        else:
                            success_count += 1
                    else:
                        fail_count += 1
                        tqdm.write(f"❌ Failed: {name} | Reason: {msg}")
                        if progress_callback:
                            progress_callback(f"❌ 下载失败: {name} | 原因: {msg}")
                    pbar.update(1)

        print("\n" + "="*30)
        print(f"🏁 Download Complete!")
        print(f"✨ New: {success_count}")
        print(f"♻️ Skipped: {skip_count}")
        print(f"📁 Output: {self.output_dir.absolute()}")
        print("="*30)
        
        if progress_callback:
            progress_callback(f"✅ 视频下载完成，成功 {success_count + skip_count} 条 (新增 {success_count}, 跳过 {skip_count})，失败 {fail_count} 条。")

def run_downloader(source_app_token: str = None, source_table_id: str = None, progress_callback=None):
    try:
        app_token = source_app_token or config.SOURCE_APP_TOKEN
        table_id = source_table_id or config.SOURCE_TABLE_ID

        if not app_token or not table_id:
            print("[Downloader] Missing Source App Token or Table ID.")
            if progress_callback:
                progress_callback("❌ 配置错误: 缺少源 App Token 或 Table ID。")
            return

        client = FeishuClient(config.FEISHU_APP_ID, config.FEISHU_APP_SECRET)
        records = client.get_all_records(app_token, table_id)
        
        downloader = VideoDownloader(config.OUTPUT_DIR, config.MAX_WORKERS)
        downloader.start(records, progress_callback)
        
    except Exception as e:
        print(f"💥 Fatal Error: {e}")
        if progress_callback:
            progress_callback(f"💥 下载器发生严重错误: {e}")

if __name__ == "__main__":
    run_downloader()
