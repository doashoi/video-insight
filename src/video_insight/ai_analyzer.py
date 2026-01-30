import os
import time
import base64
import json
import requests
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from .config import config
from .prompt_loader import prompt_loader

logger = logging.getLogger("AIAnalyzer")

class FeishuClient:
    """飞书 Wiki/多维表格 数据获取客户端。"""
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = None
        self.headers = None

    def _ensure_token(self):
        """确保存在有效的 tenant_access_token。"""
        if not self.token:
            url = f"{config.FEISHU_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal"
            payload = {"app_id": self.app_id, "app_secret": self.app_secret}
            try:
                res = requests.post(url, json=payload, timeout=10)
                res.raise_for_status()
                self.token = res.json().get("tenant_access_token")
                self.headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json; charset=utf-8"
                }
            except Exception as e:
                logger.error(f"获取 Token 失败: {e}")
                raise

    def get_app_token_from_wiki(self, wiki_token: str) -> Optional[str]:
        """解析 Wiki Token 为多维表格 App Token。"""
        self._ensure_token()
        url = f"{config.FEISHU_DOMAIN}/open-apis/wiki/v2/space_node/get"
        params = {"token": wiki_token}
        
        try:
            res = requests.get(url, headers=self.headers, params=params, timeout=10)
            res.raise_for_status()
            data = res.json().get("data", {})
            node = data.get("node", {})
            obj_type = node.get("obj_type")
            obj_token = node.get("obj_token")
            
            if obj_type != "bitable":
                logger.warning(f"Wiki 节点类型是 '{obj_type}', 预期为 'bitable'。")
            
            return obj_token
        except Exception as e:
                logger.error(f"解析 Wiki 节点失败: {e}")
                raise

    def get_all_records(self, app_token: str, table_id: str, view_id: str = None) -> List[Dict]:
        """获取多维表格所有记录。"""
        self._ensure_token()
        all_records = []
        page_token = ""
        has_more = True
        
        logger.info("正在从飞书多维表格获取数据...")
        while has_more:
            url = f"{config.FEISHU_DOMAIN}/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            params = {"page_size": 100, "page_token": page_token}
            if view_id:
                params["view_id"] = view_id
                
            try:
                res = requests.get(url, headers=self.headers, params=params, timeout=20)
                res.raise_for_status()
                data = res.json().get("data", {})
                
                items = data.get("items", [])
                all_records.extend(items)
                
                has_more = data.get("has_more", False)
                page_token = data.get("page_token", "")
            except Exception as e:
                logger.error(f"获取记录失败: {e}")
                break
        
        logger.info(f"成功获取 {len(all_records)} 条记录。")
        return all_records

class AdsAnalyzer:
    def __init__(self, output_dir: Path = None, assets_dir: Path = None):
        self.output_dir = output_dir or config.RESULT_DIR
        self.assets_dir = assets_dir or config.OUTPUT_DIR
        self.api_key = config.DASHSCOPE_API_KEY
        self.feishu_client = FeishuClient(config.FEISHU_APP_ID, config.FEISHU_APP_SECRET)
        
        if not self.api_key:
            logger.warning("环境变量中未找到 DASHSCOPE_API_KEY。")

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _encode_image(self, image_path: str) -> str:
        """将图像编码为 base64。"""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def _call_dashscope(self, system_prompt: str, user_content: List[Dict], model: str = "qwen-vl-plus-2025-08-15") -> Optional[str]:
        """通用 DashScope API 调用方法。"""
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        if "vl" not in model.lower():
             # 如果是非视觉模型，使用不同的 URL (虽然 Qwen-VL 也能处理纯文本，但为了扩展性保留此判断)
             url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": model,
            "input": {
                "messages": [
                    {"role": "system", "content": [{"text": system_prompt}]},
                    {"role": "user", "content": user_content}
                ]
            },
            "parameters": {
                "result_format": "message"
            }
        }

        for attempt in range(3):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=60)
                response.raise_for_status()
                result = response.json()
                
                if "output" in result and "choices" in result["output"]:
                    content = result["output"]["choices"][0]["message"]["content"][0]["text"]
                    return content
                else:
                    logger.error(f"意外响应: {result}")
            
            except Exception as e:
                logger.error(f"第 {attempt+1}/3 次尝试失败: {e}")
                if attempt < 2:
                    time.sleep(2)
        return None

    def _get_visual_description(self, image_path: str, text_content: str) -> Optional[str]:
        """第一阶段：视觉内容识别（结合画面与文案）。"""
        system_prompt = prompt_loader.load("video_analyzer/visual_description.md")
        user_content = [
            {"image": f"data:image/jpeg;base64,{self._encode_image(image_path)}"},
            {"text": f"【语音文案】：\n{text_content}\n\n请结合文案，客观描述该视频宫格图呈现的内容。"}
        ]
        return self._call_dashscope(system_prompt, user_content)

    def _synthesize_analysis(self, visual_desc: str, text_content: str, row_data: Dict, schema: List[Dict] = None, user_logic: str = "") -> Optional[Dict]:
        """第二阶段：数据整合分析。根据 Schema 动态生成分析逻辑。"""
        system_prompt = prompt_loader.load("video_analyzer/data_synthesis.md")
        
        # 如果提供了 Schema，动态增强提示词
        schema_instruction = ""
        if schema:
            schema_instruction = "\n\n# Output Field Constraints (Strictly follow this Schema)\n"
            schema_instruction += "你必须严格按照以下字段定义进行分析，不要输出任何不在列表中的字段。\n"
            for field in schema:
                name = field["field_name"]
                f_type = field["type"]
                # 排除一些系统字段或只读字段
                if name in ["缩略图", "视频链接", "素材名称"]:
                    continue
                
                desc = f"- **{name}** (类型代码: {f_type})"
                if "options" in field:
                    desc += f" | 必须从以下预设选项中选择: {field['options']}"
                schema_instruction += desc + "\n"
            
            schema_instruction += "\n请务必返回一个包含上述字段的 JSON 对象。如果某个字段无法从内容中得出，请保持为空字符串或默认值。"

        # 准备输入数据 (包含所有行数据)
        # 排除已知的媒体字段，将其他所有字段作为“参考数据”喂给 AI
        reference_data = {k: v for k, v in row_data.items() if k not in ["素材名称", "视频链接", "缩略图"]}
        data_str = json.dumps(reference_data, ensure_ascii=False, indent=2)
        
        user_input = f"""
【视觉描述】：
{visual_desc}

【语音文案】：
{text_content}

【参考数据（包含原始表格中的所有字段信息）】：
{data_str}

【用户确认的分析逻辑/指令】：
{user_logic or "请按默认逻辑进行深度分析。"}
"""
        full_system_prompt = system_prompt + schema_instruction
        user_content = [{"text": user_input}]
        
        # 使用视觉模型处理纯文本 (Qwen-VL 也能处理)
        response_text = self._call_dashscope(full_system_prompt, user_content, model="qwen-vl-plus-2025-08-15")
        
        if response_text:
            try:
                content = response_text.replace("```json", "").replace("```", "").strip()
                return json.loads(content)
            except Exception as e:
                logger.error(f"解析 JSON 失败: {e}\n原内容: {response_text}")
        return None

    def analyze_template(self, fields: List[Dict]) -> Optional[List[Dict]]:
        """解析用户提供的飞书模板意图。生成理解清单。"""
        system_prompt = prompt_loader.load("interaction/intent_clarification.md")
        
        # 简化字段信息传给 AI
        simplified_fields = []
        for f in fields:
            f_info = {
                "field_name": f.get("field_name"),
                "field_type": f.get("type"),
            }
            if "options" in f:
                f_info["options"] = f["options"]
            simplified_fields.append(f_info)

        fields_str = json.dumps(simplified_fields, ensure_ascii=False, indent=2)
        user_input = f"以下是我的飞书多维表格模板字段列表，请按规范生成理解确认清单：\n{fields_str}"
        
        user_content = [{"text": user_input}]
        # 使用更强的模型来做逻辑分析
        response_text = self._call_dashscope(system_prompt, user_content, model="qwen-max")
        
        if response_text:
            try:
                content = response_text.replace("```json", "").replace("```", "").strip()
                return json.loads(content)
            except Exception as e:
                logger.error(f"模板意图解析失败: {e}\n内容: {response_text}")
        return None

    def _find_assets(self, material_name: str) -> Tuple[Optional[str], Optional[str]]:
        """在资源目录中查找拼图和 ASR 文案。"""
        # 资源目录通常是处理后的结果目录: assets_dir / material_name / (material_name_sheet.jpg & material_name_asr.txt)
        material_dir = self.assets_dir / material_name
        
        sheet_path = material_dir / f"{material_name}_sheet.jpg"
        text_path = material_dir / f"{material_name}_asr.txt"
        
        if sheet_path.exists() and text_path.exists():
            return str(sheet_path), str(text_path)
            
        return None, None

    def _fetch_feishu_data(self, app_token: str = None, table_id: str = None) -> List[Dict]:
        """获取并标准化飞书数据。动态提取所有可用字段。"""
        target_app_token = app_token
        target_table_id = table_id

        if not target_app_token:
            logger.info("正在从 Wiki Token 解析 App Token...")
            target_app_token = self.feishu_client.get_app_token_from_wiki(config.WIKI_TOKEN)
        
        if not target_app_token:
            logger.error("获取 App Token 失败")
            return []
            
        logger.info(f"App Token: {target_app_token}")
        if not target_table_id:
             target_table_id = config.SOURCE_TABLE_ID
             
        records = self.feishu_client.get_all_records(target_app_token, target_table_id)
        
        normalized_data = []
        for r in records:
            fields = r.get("fields", {})
            item = {}
            
            # 动态处理所有字段
            for key, val in fields.items():
                # 处理常见的飞书复杂字段类型
                if isinstance(val, list) and len(val) > 0:
                    item_0 = val[0]
                    if isinstance(item_0, dict):
                        # 处理链接、文本、人员等
                        item[key] = item_0.get("url") or item_0.get("link") or item_0.get("text") or item_0.get("name") or str(val)
                    else:
                        item[key] = val
                elif isinstance(val, dict):
                    item[key] = val.get("url") or val.get("link") or val.get("text") or val.get("name") or str(val)
                else:
                    item[key] = val
            
            # 确保关键字段存在（即使为空）
            if "素材名称" not in item:
                # 尝试通过别名或搜索含有“视频”或“名称”的字段作为素材名
                for k in item.keys():
                    if "名称" in k or "视频" in k or "素材" in k:
                        item["素材名称"] = item[k]
                        break

            normalized_data.append(item)
            
        return normalized_data

    def process(self, source_app_token: str = None, source_table_id: str = None, progress_callback=None, schema: List[Dict] = None, user_logic: str = "") -> List[Dict]:
        """核心处理逻辑：读取源表，分析视频，返回结果列表。"""
        # ... 保持之前的逻辑 ...
        target_app_token = source_app_token
        if not target_app_token:
            logger.info("正在从 Wiki Token 解析 App Token...")
            target_app_token = self.feishu_client.get_app_token_from_wiki(config.SOURCE_WIKI_TOKEN)
            
        if not target_app_token:
            logger.error("获取 App Token 失败")
            return []
            
        logger.info(f"App Token: {target_app_token}")
        target_table_id = source_table_id or config.SOURCE_TABLE_ID

        # 获取数据 (此时已是动态提取)
        data = self._fetch_feishu_data(target_app_token, target_table_id)
        
        results = []
        total_rows = len(data)
        logger.info(f"发现 {total_rows} 行待处理数据。")
        
        success_count = 0
        skip_count = 0
        
        report_step = max(1, total_rows // 5) if total_rows > 10 else 1

        for index, row in enumerate(data):
            material_name = str(row.get('素材名称', ''))
            if material_name.lower().endswith('.mp4'):
                material_name = material_name[:-4]
            material_name = material_name.strip()

            if not material_name:
                skip_count += 1
                continue
            
            if progress_callback:
                progress_callback(f"🤖 [3/4] 正在分析 ({index+1}/{total_rows}): {material_name}")
                
            logger.info(f"正在处理: {material_name}")
            
            # 查找素材
            sheet_path, text_path = self._find_assets(material_name)
            if not sheet_path or not text_path:
                logger.warning(f"{material_name}: 未找到本地素材 (跳过分析)")
                skip_count += 1
                if progress_callback:
                    progress_callback(f"⚠️ {material_name}: 未找到本地素材 (跳过分析)")
                continue

            # 读取文案
            try:
                with open(text_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
            except Exception as e:
                logger.error(f"读取文案失败: {e}")
                skip_count += 1
                continue

            # 调用 AI (两阶段分析，传入 Schema 和用户确认后的逻辑)
            visual_desc = self._get_visual_description(sheet_path, text_content)
            analysis_json = None
            if visual_desc:
                analysis_json = self._synthesize_analysis(visual_desc, text_content, row, schema=schema, user_logic=user_logic)

            if analysis_json:
                # 合并结果 (优先使用分析结果覆盖原始数据)
                res_item = {**row, **analysis_json}
                
                # 显式添加缩略图路径
                if sheet_path and os.path.exists(sheet_path):
                    res_item["缩略图"] = sheet_path
                
                results.append(res_item)
                success_count += 1
                
                if progress_callback:
                    if total_rows <= 10:
                        pass 
                    elif (success_count % report_step == 0) or (index + 1 == total_rows):
                        progress_callback(f"📊 AI 分析进度: {index+1}/{total_rows} (已完成 {success_count} 条)")
            else:
                skip_count += 1
                if progress_callback:
                    progress_callback(f"❌ {material_name}: AI 分析失败")

        if progress_callback:
            progress_callback(f"✅ AI 分析全部完成，生成 {len(results)} 条结果。")
            
        return results

    # 保留旧方法以兼容 CLI (如果需要)，但在新管线中未使用
    def _save_excel(self, results: List[Dict]):
        pass

if __name__ == "__main__":
    logger.info("🚀 开始广告分析...")
    analyzer = AdsAnalyzer()
    results = analyzer.process()
    # print(json.dumps(results, ensure_ascii=False, indent=2))
