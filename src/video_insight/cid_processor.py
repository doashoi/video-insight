import os
import re
import logging
import json
import time
import pandas as pd
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

import lark_oapi
from lark_oapi.api.im.v1 import GetMessageResourceRequest
from lark_oapi.api.sheets.v3 import (
    CreateSpreadsheetRequest, Spreadsheet,
    SpreadsheetValuesBatchUpdateRequest, SpreadsheetValuesBatchUpdateRequestBody,
    ValueRange
)

from .config import config
from .feishu_syncer import FeishuSyncer

logger = logging.getLogger("CIDProcessor")

class CIDProcessor:
    def __init__(self, feishu_client: lark_oapi.Client):
        self.client = feishu_client
        self.syncer = FeishuSyncer() # 用于复用文件夹管理逻辑

    def download_file(self, message_id: str, file_key: str, save_path: str) -> bool:
        """从飞书下载消息中的文件。"""
        logger.info(f"Downloading file {file_key} from message {message_id}...")
        try:
            request = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(file_key) \
                .type("file") \
                .build()
            
            response = self.client.im.v1.message_resource.get(request)
            
            if not response.success():
                logger.error(f"Failed to download file: {response.msg} (code: {response.code})")
                return False
            
            with open(save_path, "wb") as f:
                f.write(response.file.read())
            
            logger.info(f"File downloaded to {save_path}")
            return True
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return False

    def parse_dimensions(self, dimension_str: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        解析尺寸字符串。
        示例: 0126_After My Bestie Slept With My Ex-Husba_高光片段2_竖
        返回: (PH Name, Category, Orientation)
        """
        if not dimension_str or not isinstance(dimension_str, str):
            return None, None, None
            
        # 尝试正则匹配: [日期前缀_]剧名_片段类型_尺寸
        # 允许前缀可选，允许剧名包含下划线
        parts = dimension_str.split('_')
        if len(parts) < 3:
            # 如果不符合 "_" 分隔，尝试返回原样以便排查，或者忽略
            return None, None, None
            
        orientation = parts[-1].strip()
        category = parts[-2].strip()
        
        # 处理剧名：忽视第一个部分（通常是日期 0126）
        # 无论第一个部分是不是数字，只要有 4 个及以上部分，我们就跳过第一部分
        # 如果只有 3 个部分，说明没有日期前缀，例如 "剧名_片段_尺寸"
        if len(parts) >= 4:
            start_idx = 1
        else:
            start_idx = 0
            
        ph_name = '_'.join(parts[start_idx:-2]).strip()
        
        # 不再进行 Category 映射归一化，以原表为准
        # category = category.replace("拼接素材", "拼接片段")
        
        return ph_name, category, orientation

    def process_file(self, file_path: str) -> Dict[str, Dict[str, Dict[str, str]]]:
        """
        处理 Excel/CSV 文件并提取信息。
        返回结构: { ph_name: { category: { orientation: cid } } }
        """
        try:
            if file_path.endswith('.csv'):
                # 尝试多种编码读取 CSV
                try:
                    df = pd.read_csv(file_path)
                except UnicodeDecodeError:
                    df = pd.read_csv(file_path, encoding='gbk')
            else:
                # 使用 openpyxl 引擎处理 Excel
                df = pd.read_excel(file_path, engine='openpyxl')
                
            # 1. 清理列名：去空格、转大写以便匹配
            df.columns = [str(c).strip() for c in df.columns]
            
            # 2. 寻找目标列（兼容大小写）
            cid_col = next((c for c in df.columns if c.upper() == "CID"), None)
            dim_col = next((c for c in df.columns if c == "尺寸"), None)
            
            if not cid_col or not dim_col:
                logger.error(f"Missing required columns. Found: {df.columns.tolist()}")
                return {}
                
            # 3. 去除空行
            df = df.dropna(subset=[cid_col, dim_col])
            
            results = {}
            for _, row in df.iterrows():
                cid = str(row[cid_col]).strip()
                dimension_str = str(row[dim_col]).strip()
                
                # 忽略一些明显的无效数据
                if cid.lower() in ['nan', 'none', ''] or dimension_str.lower() in ['nan', 'none', '']:
                    continue
                
                ph_name, category, orientation = self.parse_dimensions(dimension_str)
                
                if not ph_name or not category or not orientation:
                    continue
                    
                if ph_name not in results:
                    results[ph_name] = {}
                if category not in results[ph_name]:
                    results[ph_name][category] = {}
                
                # 同一个 (PH Name, Category, Orientation) 可能有多个 CID，换行存储
                if orientation in results[ph_name][category]:
                    # 如果 CID 已经存在，避免重复添加
                    existing_cids = results[ph_name][category][orientation].split('\n')
                    if cid not in existing_cids:
                        results[ph_name][category][orientation] += f"\n{cid}"
                else:
                    results[ph_name][category][orientation] = cid
                    
            return results
        except Exception as e:
            logger.error(f"Error processing file: {e}")
            return {}

    def create_report(self, data: Dict[str, Dict[str, Dict[str, str]]], user_id: str) -> Optional[str]:
        """
        创建飞书电子表格报告。
        """
        if not data:
            return None
            
        try:
            # 1. 准备文件夹
            folder_token = self.syncer.get_or_create_folder("自动提取", user_id)
            if not folder_token:
                logger.error("Failed to get or create folder '自动提取'")
                return None
                
            # 2. 确定动态列（所有的 Categories）
            all_categories = set()
            for ph_data in data.values():
                all_categories.update(ph_data.keys())
            
            # 排序 Category
            sorted_categories = sorted(list(all_categories))
            
            # 3. 准备表格数据
            headers = ["PH Name"] + sorted_categories
            rows = [headers]
            
            for ph_name, ph_data in sorted(data.items()):
                row = [ph_name]
                for cat in sorted_categories:
                    cat_data = ph_data.get(cat, {})
                    # 格式化单元格内容: 横：CID1\n竖：CID2
                    cell_lines = []
                    for orientation in ["横", "竖", "方"]:
                        if orientation in cat_data:
                            cell_lines.append(f"{orientation}：{cat_data[orientation]}")
                    
                    # 处理其他未预见的 Orientation
                    for orientation, cid in cat_data.items():
                        if orientation not in ["横", "竖", "方"]:
                            cell_lines.append(f"{orientation}：{cid}")
                            
                    row.append("\n".join(cell_lines))
                rows.append(row)
            
            # 4. 创建电子表格
            spreadsheet_name = f"CID提取结果_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
            req_body = Spreadsheet.builder() \
                .title(spreadsheet_name) \
                .folder_token(folder_token) \
                .build()
            
            create_req = CreateSpreadsheetRequest.builder() \
                .request_body(req_body) \
                .build()
                
            resp = self.client.sheets.v3.spreadsheet.create(create_req)
            if not resp.success():
                logger.error(f"Failed to create spreadsheet: {resp.msg}")
                return None
                
            spreadsheet_token = resp.data.spreadsheet.spreadsheet_token
            spreadsheet_url = resp.data.spreadsheet.url
            
            # 5. 写入数据
            row_count = len(rows)
            col_count = len(headers)
            
            # 计算列字母 (处理超过 26 列的情况)
            def get_col_letter(n):
                string = ""
                while n > 0:
                    n, remainder = divmod(n - 1, 26)
                    string = chr(65 + remainder) + string
                return string

            col_letter = get_col_letter(col_count)
            range_str = f"A1:{col_letter}{row_count}"
            
            value_range = ValueRange.builder() \
                .range(range_str) \
                .values(rows) \
                .build()
                
            batch_req_body = SpreadsheetValuesBatchUpdateRequestBody.builder() \
                .value_ranges([value_range]) \
                .build()
                
            batch_req = SpreadsheetValuesBatchUpdateRequest.builder() \
                .spreadsheet_token(spreadsheet_token) \
                .request_body(batch_req_body) \
                .build()
                
            write_resp = self.client.sheets.v3.spreadsheet_values.batch_update(batch_req)
            if not write_resp.success():
                logger.error(f"Failed to write data to spreadsheet: {write_resp.msg}")
            
            # 6. 权限处理
            if user_id:
                # 增加权限并转移所有权
                self.syncer.add_member_permission(spreadsheet_token, user_id, "sheet", role="full_access")
                time.sleep(1)
                self.syncer.transfer_owner(spreadsheet_token, user_id, "sheet")
            
            return spreadsheet_url
            
        except Exception as e:
            logger.error(f"Error creating spreadsheet report: {e}", exc_info=True)
            return None
