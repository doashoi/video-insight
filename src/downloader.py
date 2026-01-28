import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Tuple
from tqdm import tqdm
from config import config


class FeishuClient:
    """飞书多维表格 API 客户端，用于下载记录。"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = self._get_tenant_access_token()
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _get_tenant_access_token(self) -> str:
        """获取 Tenant Access Token。"""
        url = f"{config.FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        res = requests.post(url, json=payload, timeout=10)
        res.raise_for_status()
        return res.json().get("tenant_access_token")

    def get_all_records(self, app_token: str, table_id: str) -> list:
        """获取表中的所有记录（支持分页）。"""
        all_records = []
        page_token = ""
        has_more = True

        print("🔍 正在从飞书多维表格获取数据...")
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
                print(f"❌ 获取记录失败: {e}")
                break

        print(f"✅ 成功获取 {len(all_records)} 条记录")
        return all_records


class VideoDownloader:
    """增量视频下载器。"""

    FIELDS = {"NAME": "素材名称", "URL": "视频链接"}

    def __init__(self, output_dir: Path, max_workers: int = 5):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.session = requests.Session()

    def sanitize_filename(self, filename: str) -> str:
        """清理文件名中的非法字符。"""
        if not filename:
            return "unnamed_video"
        name = re.sub(r'[\\/*?:"<>|]', "_", str(filename))
        return name.strip()

    def download_single(self, name: str, url: str) -> Tuple[bool, str, str]:
        """下载单个视频。"""
        try:
            # 1. 验证 URL
            if not url or not str(url).startswith("http"):
                return False, name, "无效的 URL"

            # 2. 准备文件名
            clean_name = self.sanitize_filename(name)
            if not clean_name.lower().endswith(".mp4"):
                clean_name += ".mp4"

            file_path = self.output_dir / clean_name

            # 3. 增量检查 (文件存在且大小大于0则跳过)
            if file_path.exists() and file_path.stat().st_size > 0:
                return True, name, "跳过 (已存在)"

            # 4. 流式下载
            resp = self.session.get(url, timeout=60, stream=True)
            resp.raise_for_status()

            with open(file_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunk
                    f.write(chunk)

            return True, name, "成功"
        except Exception as e:
            return False, name, str(e)

    def start(self, records: list, progress_callback=None):
        """开始并发下载任务。"""
        tasks = []

        for r in records:
            fields = r.get("fields", {})
            name = fields.get(self.FIELDS["NAME"])
            url_field = fields.get(self.FIELDS["URL"])

            url = ""
            if isinstance(url_field, str):
                url = url_field
            elif isinstance(url_field, list) and len(url_field) > 0:
                url = (
                    url_field[0].get("url", "")
                    or url_field[0].get("link", "")
                    or url_field[0].get("text", "")
                )
            elif isinstance(url_field, dict):
                url = url_field.get("url", "") or url_field.get("link", "")

            if name and url:
                tasks.append((name, url))

        if not tasks:
            print("\n⚠️ 未找到有效的视频链接。")
            if progress_callback:
                progress_callback("⚠️ 未找到有效的视频链接。")
            return

        print(f"🚀 开始下载 (线程数: {self.max_workers})...")
        if progress_callback:
            progress_callback(f"🚀 任务已开始，正在下载视频，共计 {len(tasks)} 条...")

        success_count = 0
        skip_count = 0
        fail_count = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_video = {
                executor.submit(self.download_single, n, u): n for n, u in tasks
            }

            with tqdm(total=len(tasks), desc="Progress") as pbar:
                for future in as_completed(future_to_video):
                    success, name, msg = future.result()
                    if success:
                        if msg == "跳过 (已存在)":
                            skip_count += 1
                        else:
                            success_count += 1
                    else:
                        fail_count += 1
                        tqdm.write(f"❌ 失败: {name} | 原因: {msg}")
                        if progress_callback:
                            progress_callback(f"❌ 下载失败: {name} | 原因: {msg}")
                    pbar.update(1)

        print("\n" + "=" * 30)
        print("🏁 下载完成!")
        print(f"✨ 新增: {success_count}")
        print(f"♻️ 跳过: {skip_count}")
        print(f"📁 输出目录: {self.output_dir.absolute()}")
        print("=" * 30)

        if progress_callback:
            progress_callback(
                f"✅ 视频下载完成，成功 {success_count + skip_count} 条 (新增 {success_count}, 跳过 {skip_count})，失败 {fail_count} 条。"
            )


def run_downloader(
    source_app_token: str = None, source_table_id: str = None, progress_callback=None
):
    try:
        app_token = source_app_token or config.SOURCE_APP_TOKEN
        table_id = source_table_id or config.SOURCE_TABLE_ID

        if not app_token or not table_id:
            print("[Downloader] 缺少源 App Token 或 Table ID。")
            if progress_callback:
                progress_callback("❌ 配置错误: 缺少源 App Token 或 Table ID。")
            return

        client = FeishuClient(config.FEISHU_APP_ID, config.FEISHU_APP_SECRET)
        records = client.get_all_records(app_token, table_id)

        print({config.DOWNLOAD_DIR})
        downloader = VideoDownloader(config.DOWNLOAD_DIR, config.MAX_WORKERS)
        downloader.start(records, progress_callback)

    except Exception as e:
        print(f"💥 严重错误: {e}")
        if progress_callback:
            progress_callback(f"💥 下载器发生严重错误: {e}")


if __name__ == "__main__":
    run_downloader()
