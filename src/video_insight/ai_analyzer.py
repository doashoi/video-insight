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

    def _get_system_prompt(self) -> str:
        return """你是一位拥有10年经验的资深短视频广告分析师。请基于我提供的「视频宫格图」、「视频文案」，进行深度分析。

请严格遵守以下分析维度和约束条件：

1. **受众人群 (Audience)**：
   - 必须基于视频内容精准定位，并映射到以下标准分类词中（优先选择最具体的一个）：
   - 分类词库：[年轻女性, 年轻人, 职场白领, 通用, 健身人群, 情侣, 老人, 儿童, 家长, 学生, 宝妈]
   - 规则：定位到具体人群，例如如果分析结果是20-30岁女性，必须输出“年轻女性”，而不是“年轻人”。

2. **核心功能 (Function)**：
   - 识别视频推广的核心卖点，并映射到以下标准分类词（若涉及多个，优先选择最核心的一个，仅在无法区分主次时选择“综合卖点”）：
   - 分类词库：[月暖暖, 饮食健康小助手, 健康小目标, 心理健康自测, 流感健康攻略, 药管家, 健康档案, 问答, 口腔小助理, 中医养生, 综合卖点, AI解读智能报告]

3. **核心痛点 (Pain Point)**：
   - 结合文案和画面，总结用户面临的具体问题。
   - 约束：必须缩短成简单的一句话（15字以内）。
   - 示例：“忘记药品来源”、“不知道药品禁忌”、“减少焦虑”、“痛经缓解”。

4. **应用场景 (Scenario)**：
   - 仅限从以下三个选项中选择一个：[生活场景, 工作场景, 特殊场景]
   - 规则：特殊场景权重最低，仅在无法归类为生活或工作时使用。

5. **概述 (Overview)**：
   - 简要描述视频的主要内容、剧情走向或展现形式。
   - **严禁**使用“视频通过...”、“该视频展示了...”等引导语。
   - 直接陈述画面内容或剧情。

6. **深度分析 (Analysis)**：
   - 结合提供的投放数据（展现、点击、消耗、激活、点击率CTR、转换率CVR）进行综合评判。
   - **严禁**使用“根据数据分析...”、“从数据来看...”等废话。直接给出结论。
   - **特别注意**：请显著提升「消耗」数据的分析权重。消耗代表了公司的实际投入和潜在收益规模。
     - 对于**高消耗**视频：需严格审视其转化率和点击率，分析为何能跑出高消耗（素材哪里吸引人？）以及是否存在“高耗低效”的浪费风险。
     - 对于**低消耗**视频：分析未能跑量的原因（是封面不吸引人导致点击率低，还是内容平庸）。
   - 综合判断视频的优劣，并给出优化方向。

**输出格式要求**：
请直接返回标准的 JSON 格式，不要包含Markdown标记或其他废话：
{
    "人群": "年轻女性",
    "功能": "月暖暖",
    "痛点": "痛经缓解",
    "场景": "生活场景",
    "概述": "年轻女性在办公室捂着肚子，表情痛苦，随后拿出月暖暖产品使用，表情舒缓。",
    "分析": "点击率较高（X%），封面痛点直击人心。消耗较高但转化一般，建议优化落地页引导。"
}"""

    def _call_qwen_vl(self, image_path: str, text_content: str, performance_data: Dict) -> Optional[Dict]:
        """调用 Qwen-VL-Plus API。"""
        url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        perf_str = "\n".join([f"{k}: {v}" for k, v in performance_data.items()])
        
        user_content = [
            {"image": f"data:image/jpeg;base64,{self._encode_image(image_path)}"},
            {"text": f"【视频文案】：\n{text_content}\n\n【投放数据】：\n{perf_str}\n\n请根据上述素材和数据进行分析。"}
        ]

        payload = {
            "model": "qwen-vl-plus-2025-08-15",
            "input": {
                "messages": [
                    {"role": "system", "content": [{"text": self._get_system_prompt()}]},
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
                    content = content.replace("```json", "").replace("```", "").strip()
                    return json.loads(content)
                else:
                    logger.error(f"意外响应: {result}")
            
            except Exception as e:
                logger.error(f"第 {attempt+1}/3 次尝试失败: {e}")
                if attempt < 2:
                    time.sleep(2)
        
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
        """获取并标准化飞书数据。"""
        target_app_token = app_token
        target_table_id = table_id

        if not target_app_token:
            logger.info("正在从 Wiki Token 解析 App Token...")
            target_app_token = self.feishu_client.get_app_token_from_wiki(config.WIKI_TOKEN)
        
        if not target_app_token:
            logger.error("获取 App Token 失败")
            return []
            
        logger.info(f"App Token: {target_app_token}")
        # 如果未提供 table_id，使用配置或获取第一个表
        if not target_table_id:
             target_table_id = config.SOURCE_TABLE_ID
             
        records = self.feishu_client.get_all_records(target_app_token, target_table_id)
        
        normalized_data = []
        for r in records:
            fields = r.get("fields", {})
            
            # 标准化链接
            url_field = fields.get("视频链接")
            url = ""
            if isinstance(url_field, str):
                url = url_field
            elif isinstance(url_field, list) and len(url_field) > 0:
                url = url_field[0].get("url", "") or url_field[0].get("link", "")
            elif isinstance(url_field, dict):
                url = url_field.get("url", "") or url_field.get("link", "")
                
            # 标准化来源
            source_field = fields.get("来源", "")
            source = ""
            if isinstance(source_field, str):
                source = source_field
            elif isinstance(source_field, list) and len(source_field) > 0:
                item_0 = source_field[0]
                if isinstance(item_0, dict):
                    source = item_0.get("text", "") or item_0.get("name", "")
                else:
                    source = str(item_0)
            elif isinstance(source_field, dict):
                source = source_field.get("text", "") or source_field.get("name", "")

            item = {
                "素材名称": fields.get("素材名称", ""),
                "视频链接": url,
                "展现": fields.get("展现", 0),
                "点击": fields.get("点击", 0),
                "消耗": fields.get("消耗", 0),
                "激活人数": fields.get("激活人数", 0),
                "来源": source
            }
            normalized_data.append(item)
            
        return normalized_data

    def process(self, source_app_token: str = None, source_table_id: str = None, progress_callback=None) -> List[Dict]:
        """核心处理逻辑：读取源表，分析视频，返回结果列表。"""
        # 1. 确定目标表
        target_app_token = source_app_token
        if not target_app_token:
            logger.info("正在从 Wiki Token 解析 App Token...")
            target_app_token = self.feishu_client.get_app_token_from_wiki(config.SOURCE_WIKI_TOKEN)
            
        if not target_app_token:
            logger.error("获取 App Token 失败")
            return []
            
        logger.info(f"App Token: {target_app_token}")
        target_table_id = source_table_id or config.SOURCE_TABLE_ID

        data = self._fetch_feishu_data(target_app_token, target_table_id)
        
        results = []
        total_rows = len(data)
        logger.info(f"发现 {total_rows} 行待处理数据。")
        
        success_count = 0
        skip_count = 0
        
        # 定义进度通知步长 (例如总数的 20%，或者至少每 10 条一次)
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
                # 即使是静默模式，跳过的信息也建议显示，方便排查
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

            # 提取投放数据
            perf_data = {
                "展现": row.get("展现", 0),
                "点击": row.get("点击", 0),
                "消耗": row.get("消耗", 0),
                "激活人数": row.get("激活人数", 0),
                "来源": row.get("来源", "")
            }

            # 调用 AI
            analysis_json = self._call_qwen_vl(sheet_path, text_content, perf_data)
            if analysis_json:
                # 合并结果
                res_item = {**row, **analysis_json}
                
                # 显式添加缩略图路径，以便 Syncer 可以上传
                if sheet_path and os.path.exists(sheet_path):
                    res_item["缩略图"] = sheet_path
                
                results.append(res_item)
                success_count += 1
                
                # 进度通知逻辑：
                # 1. 如果数据量小 (<10)，逐条通知
                # 2. 如果数据量大，按步长通知
                if progress_callback:
                    if total_rows <= 10:
                        # 逐条通知不需要显示具体 JSON，只显示成功状态
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
