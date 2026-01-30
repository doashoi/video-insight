import logging
import os
import sys
import time
import io
import re
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from typing import Dict, Any, Optional, List, Tuple

import lark_oapi
from lark_oapi import Client, FEISHU_DOMAIN
from lark_oapi.core import HttpMethod, AccessTokenType
from lark_oapi.core.model import BaseRequest
from lark_oapi.api.drive.v1 import (
    UploadAllMediaRequest, UploadAllMediaRequestBody, 
    CreatePermissionMemberRequest, BaseMember, ListFileRequest, CreateFolderFileRequest, CreateFolderFileRequestBody, File,
    TransferOwnerPermissionMemberRequest, Owner
)
from lark_oapi.api.im.v1.model import GetMessageResourceRequest
from lark_oapi.api.sheets.v3.model import (
    Spreadsheet, CreateSpreadsheetRequest
)
from lark_oapi.api.bitable.v1 import (
    CreateAppTableRecordRequest, 
    CreateAppTableFieldRequest, AppTableField, AppTableFieldProperty, AppTableFieldPropertyOption,
    CreateAppRequest, ReqApp, ListAppTableRequest, GetAppRequest,
    CopyAppRequest, CopyAppRequestBody, ListAppTableFieldRequest
)

from .config import config
from .data_store import UserFolderManager

logger = logging.getLogger("FeishuSyncer")

class FeishuSyncer:
    def __init__(self):
        self.app_id = config.FEISHU_APP_ID
        self.app_secret = config.FEISHU_APP_SECRET
        self.client = Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .domain(config.FEISHU_DOMAIN) \
            .build()
            
        self.app_token = config.DEST_APP_TOKEN
        self.table_id = config.DEST_TABLE_ID
        self.folder_manager = UserFolderManager()
        self.last_error = None
        
        # 字段别名映射 (用于适配用户自定义的表结构)
        self.FIELD_ALIASES = {
            "缩略图": ["缩略图", "视频", "封面", "图片", "thumb", "Thumbnail"],
            "转换率": ["转换率", "转化率", "Conversion Rate"],
            "场景": ["场景", "场景1", "使用场景", "Scene"],
            "点击率": ["点击率", "CTR", "Click Rate"],
            "素材名称": ["素材名称", "标题", "素材名", "Title", "Name"],
            "视频链接": ["视频链接", "链接", "URL", "Link", "Video Link"],
            "消耗": ["消耗", "Cost", "Spend"],
            "展现": ["展现", "曝光", "Impression", "Show"],
            "点击": ["点击", "Click"],
        }

    def get_app_name(self, app_token: str) -> Optional[str]:
        """获取多维表格应用的名称。"""
        try:
            req = GetAppRequest.builder().app_token(app_token).build()
            resp = self.client.bitable.v1.app.get(req)
            if resp.success() and resp.data and resp.data.app:
                return resp.data.app.name
            return None
        except Exception as e:
            logger.error(f"获取应用名称失败: {e}")
            return None

    def transfer_owner(self, token: str, member_id: str, type: str, member_type: str = "openid") -> bool:
        """转移文档/文件夹所有者。"""
        logger.info(f"正在转移 {type} ({token}) 所有权给 {member_id}...")
        try:
            req = TransferOwnerPermissionMemberRequest.builder() \
                .token(token) \
                .type(type) \
                .need_notification(True) \
                .remove_old_owner(True) \
                .stay_put(False) \
                .request_body(Owner.builder()
                    .member_type(member_type)
                    .member_id(member_id)
                    .build()) \
                .build()
            
            resp = self.client.drive.v1.permission_member.transfer_owner(req)
            if not resp.success():
                # 如果是因为已经是所有者，则不算失败
                if "is already owner" in str(resp.msg).lower():
                    logger.info(f"目标用户已经是所有者。")
                    return True
                logger.error(f"转移所有权失败: {resp.msg} (Code: {resp.code})")
                return False
            
            logger.info(f"所有权转移成功！(保留机器人权限)")
            return True
        except Exception as e:
            logger.error(f"转移所有权时发生异常: {e}")
            return False

    def search_folder(self, name: str) -> Optional[str]:
        """使用搜索 API 在全域查找指定名称的文件夹。"""
        try:
            # 构造搜索请求
            import json
            from lark_oapi.core.model import RequestOption
            from lark_oapi.core.const import CONTENT_TYPE, APPLICATION_JSON, AUTHORIZATION
            
            request = BaseRequest.builder() \
                .http_method(HttpMethod.POST) \
                .uri("/open-apis/drive/v1/files/search") \
                .token_types({AccessTokenType.TENANT}) \
                .body({"search_phrase": name}) \
                .build()
            
            # 手动获取 Token 并添加到 Header，因为 Client.request 处理 BaseRequest 时可能存在 Bug
            from lark_oapi.core.token import TokenManager
            tm = TokenManager(self.client._config)
            token = tm.get_tenant_access_token()
            
            option = RequestOption()
            option.headers[CONTENT_TYPE] = f"{APPLICATION_JSON}; charset=utf-8"
            option.headers[AUTHORIZATION] = f"Bearer {token}"
            
            response = self.client.request(request, option)
            
            if response.code == 0:
                data = json.loads(response.content.decode('utf-8'))
                if data.get("code") == 0:
                    items = data.get("data", {}).get("items", [])
                    for item in items:
                        # 搜索结果中的 title 对应文件名，docs_type 对应类型
                        if item.get("title") == name and item.get("docs_type") == "folder":
                            return item.get("docs_token")
            else:
                status_code = response.code
                logger.error(f"请求失败: {response.msg} (Code: {response.code}, HTTP: {status_code})")
            return None
        except Exception as e:
            logger.error(f"搜索文件夹异常: {e}")
            return None

    def get_root_folder_by_name(self, name: str) -> Optional[str]:
        """查找指定名称的文件夹。先搜索全域，再查找根目录。"""
        # 1. 先尝试全域搜索 (能找到被转移所有权但仍有权限的文件夹)
        token = self.search_folder(name)
        if token:
            return token

        try:
            # 2. 如果全域搜索没找到，再搜索根目录
            req = ListFileRequest.builder() \
                .folder_token("") \
                .build()
            
            resp = self.client.drive.v1.file.list(req)
            if resp.success() and resp.data and resp.data.files:
                for file in resp.data.files:
                    if file.type == "folder" and file.name == name:
                        if not file.deleted:
                            return file.token
            return None
        except Exception as e:
            logger.error(f"搜索文件夹异常: {e}")
            return None

    def download_im_file(self, message_id: str, file_key: str, save_path: str) -> bool:
        """从飞书 IM 下载文件。"""
        try:
            req = GetMessageResourceRequest.builder() \
                .message_id(message_id) \
                .file_key(file_key) \
                .type("file") \
                .build()
            
            resp = self.client.im.v1.message_resource.get(req)
            if not resp.success():
                logger.error(f"下载文件失败: {resp.msg}")
                return False
            
            # 确保目录存在
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            
            with open(save_path, "wb") as f:
                f.write(resp.file.read())
            return True
        except Exception as e:
            logger.error(f"下载文件异常: {e}")
            return False

    def process_cid_file(self, file_path: str) -> Dict[str, Dict[str, Dict[str, str]]]:
        """解析 CID 文件并按剧名、片段类型、尺寸聚合。"""
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".csv":
                # 尝试多种编码
                df = None
                for enc in ['utf-8', 'gbk', 'utf-8-sig']:
                    try:
                        df = pd.read_csv(file_path, encoding=enc)
                        break
                    except:
                        continue
                if df is None: return {}
            else:
                df = pd.read_excel(file_path)

            # 标准化列名
            df.columns = [str(c).strip().upper() for c in df.columns]
            
            # 查找 CID 和 尺寸 列
            cid_col = None
            dim_col = None
            for col in df.columns:
                if "CID" in col: cid_col = col
                if "尺寸" in col: dim_col = col
            
            if not cid_col or not dim_col:
                logger.error(f"未找到 CID 或 尺寸 列。现有列: {list(df.columns)}")
                return {}

            # 数据结构: {剧名: {片段类型: {尺寸: CID}}}
            data_map = {}
            
            for _, row in df.iterrows():
                cid = str(row[cid_col]).strip()
                dim_str = str(row[dim_col]).strip()
                if not cid or not dim_str or cid.lower() == "nan": continue
                
                # 解析尺寸字符串: 0126_After My Bestie Slept With My Ex-Husba_高光片段2_竖
                parts = dim_str.split('_')
                if len(parts) < 3: continue
                
                orientation = parts[-1].strip() # 竖/横/方
                category = parts[-2].strip()    # 高光片段2/拼接素材1
                
                # 剧名提取
                start_idx = 0
                if len(parts) >= 4 and re.match(r'^\d{4}$', parts[0]):
                    start_idx = 1
                
                ph_name = '_'.join(parts[start_idx:-2]).strip()
                
                if ph_name not in data_map: data_map[ph_name] = {}
                if category not in data_map[ph_name]: data_map[ph_name][category] = {}
                
                # 同一个剧名、片段、尺寸可能有多个 CID，用换行连接
                existing = data_map[ph_name][category].get(orientation, "")
                if cid not in existing:
                    data_map[ph_name][category][orientation] = (existing + "\n" + cid).strip()

            return data_map
        except Exception as e:
            logger.error(f"解析 CID 文件异常: {e}")
            return {}

    def create_cid_report(self, data: Dict[str, Dict[str, Dict[str, str]]], user_id: str) -> Optional[str]:
        """创建 CID 整理报表并返回链接。"""
        try:
            # 1. 获取或创建“自动提取”文件夹
            folder_name = "自动提取"
            folder_token = self.get_or_create_folder(folder_name, user_id)
            if not folder_token:
                logger.error("无法获取或创建“自动提取”文件夹")
                return None

            # 2. 创建飞书表格
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            file_name = f"CID整理表_{timestamp}"
            
            req = CreateSpreadsheetRequest.builder() \
                .request_body(Spreadsheet.builder()
                    .title(file_name)
                    .folder_token(folder_token)
                    .build()) \
                .build()
            
            resp = self.client.sheets.v3.spreadsheet.create(req)
            if not resp.success():
                logger.error(f"创建表格失败: {resp.msg}")
                return None
            
            spreadsheet_token = resp.data.spreadsheet.spreadsheet_token
            spreadsheet_url = resp.data.spreadsheet.url
            
            # 3. 准备数据矩阵
            # 收集所有片段类型
            all_categories = set()
            for ph_name, categories in data.items():
                all_categories.update(categories.keys())
            
            sorted_categories = sorted(list(all_categories))
            
            # 表头: PH Name | 片段1 (竖) | 片段1 (横) | 片段1 (方) | 片段2 (竖) ...
            headers = ["PH Name"]
            for cat in sorted_categories:
                headers.extend([f"{cat} (竖)", f"{cat} (横)", f"{cat} (方)"])
            
            value_matrix = [headers]
            
            # 添加数据行
            for ph_name, categories in data.items():
                row = [ph_name]
                for cat in sorted_categories:
                    cat_data = categories.get(cat, {})
                    for orient in ["竖", "横", "方"]:
                        cid_text = cat_data.get(orient, "")
                        row.append(cid_text)
                value_matrix.append(row)

            # 4. 使用 v2 Values API 写入数据 (兼容性最强)
            # 获取第一张工作表的 ID (通常是创建后的默认表)
            # v3 创建的表格默认第一张表 ID 可以在 resp.data.spreadsheet.sheets[0].sheet_id 获取
            sheet_id = resp.data.spreadsheet.sheets[0].sheet_id
            
            # 构造范围，例如 "sheet_id!A1:D10"
            # 注意：v2 API 支持使用 sheet_id
            range_str = f"{sheet_id}!A1" # 起始位置
            
            import json
            from lark_oapi.core.model import RequestOption
            from lark_oapi.core.const import CONTENT_TYPE, APPLICATION_JSON, AUTHORIZATION
            from lark_oapi.core.token import TokenManager

            write_req = BaseRequest.builder() \
                .http_method(HttpMethod.PUT) \
                .uri(f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values") \
                .token_types({AccessTokenType.TENANT}) \
                .body({
                    "valueRange": {
                        "range": range_str,
                        "values": value_matrix
                    }
                }) \
                .build()
            
            # 获取 Token 并发送请求
            tm = TokenManager(self.client._config)
            token = tm.get_tenant_access_token()
            
            option = RequestOption()
            option.headers[CONTENT_TYPE] = f"{APPLICATION_JSON}; charset=utf-8"
            option.headers[AUTHORIZATION] = f"Bearer {token}"
            
            write_resp = self.client.request(write_req, option)
            
            if write_resp.code != 0:
                logger.error(f"写入表格数据失败: {write_resp.msg} (Code: {write_resp.code})")
            
            # 5. 转移所有权给用户
            if user_id:
                self.transfer_owner(spreadsheet_token, user_id, "sheet")
            
            return spreadsheet_url
        except Exception as e:
            logger.error(f"创建 CID 报表异常: {e}", exc_info=True)
            return None

    def get_or_create_folder(self, folder_name: str, user_id: str = None) -> Optional[str]:
        """查找或创建文件夹。支持跨全域搜索、所有权转移以及自动清理冗余逻辑。"""
        logger.info(f"正在定位文件夹: {folder_name} ...")
        
        try:
            token = None
            
            # 1. 尝试从缓存获取
            if user_id:
                token = self.folder_manager.get_folder_token(user_id)
                if token:
                    # 验证有效性 (确保机器人仍有权限)
                    try:
                        check_req = ListFileRequest.builder().folder_token(token).build()
                        if self.client.drive.v1.file.list(check_req).success():
                            logger.info(f"命中缓存有效文件夹 Token: {token}")
                        else:
                            logger.info(f"缓存的 Token 已失效或无权限，尝试重新查找。")
                            token = None
                    except Exception:
                        token = None

            # 2. 如果缓存无效，进行全域搜索 (解决被转移所有权后无法在根目录找到的问题)
            if not token:
                token = self.get_root_folder_by_name(folder_name)
                if token:
                    logger.info(f"搜索到匹配文件夹 Token: {token}")
            
            # 3. 如果仍未找到，创建新文件夹
            if not token:
                logger.info(f"未发现已有文件夹，正在创建新文件夹: {folder_name} ...")
                req = CreateFolderFileRequest.builder() \
                    .request_body(CreateFolderFileRequestBody.builder()
                        .name(folder_name)
                        .folder_token("") # 先在机器人根目录创建
                        .build()) \
                    .build()
                
                resp = self.client.drive.v1.file.create_folder(req)
                if resp.success() and resp.data:
                    token = resp.data.token
                    logger.info(f"新文件夹创建成功: {token}")
                else:
                    logger.error(f"创建文件夹失败: {resp.msg}")
                    return None

            # 4. 处理所有权与权限 (确保文件夹最终在用户“我的文件夹”中)
            if user_id and token:
                # 无论是否新创建，都确认为用户同步缓存
                self.folder_manager.save_folder_token(user_id, token)
                
                # 检查所有权转移 (如果是机器人拥有的，则转移)
                # 注意：如果 search 到了用户拥有的文件夹，transfer_owner 会报错（不是所有者），忽略即可
                logger.info(f"正在确保文件夹所有权属于用户...")
                
                # A. 先给用户加管理权限 (转移前提)
                self.add_member_permission(token, user_id, "folder", role="full_access")
                time.sleep(1)
                
                # B. 转移所有权 (转移后，文件夹将从机器人根目录移动到用户“我的文件夹”)
                # remove_old_owner=True 确保机器人不再是所有者，释放“共享”标记
                transfer_success = self.transfer_owner(token, user_id, "folder")
                
                if transfer_success:
                    logger.info(f"文件夹已转移给用户。")
                else:
                    # 如果失败，可能是因为用户已经是所有者了，这是我们想要的结果
                    logger.info(f"文件夹已由用户拥有或无需转移。")
            
            return token
        
        except Exception as e:
            logger.error(f"查找/创建文件夹时发生异常: {e}")
            return None

    def create_bitable(self, name: str, folder_token: str, user_id: str = None) -> Optional[str]:
        """创建一个新的多维表格应用并返回其 App Token。"""
        logger.info(f"正在文件夹 {folder_token} 中创建多维表格应用: {name} ...")
        try:
            # 使用多维表格 API 创建应用
            req = CreateAppRequest.builder() \
                .request_body(ReqApp.builder()
                    .name(name)
                    .folder_token(folder_token)
                    .build()) \
                .build()
            
            resp = self.client.bitable.v1.app.create(req)
            if not resp.success() or not resp.data or not resp.data.app:
                logger.error(f"创建多维表格失败: {resp.msg} (code: {resp.code})")
                return None
            
            # 响应数据结构: resp.data.app.app_token
            app_token = resp.data.app.app_token
            logger.info(f"已创建多维表格 App Token: {app_token}")
            
            # 转移所有权
            if user_id:
                self.transfer_owner(app_token, user_id, "bitable")
                
            return app_token
        except Exception as e:
            logger.error(f"创建多维表格时发生异常: {e}")
            return None

    def copy_bitable(self, source_app_token: str, name: str, folder_token: str, user_id: str = None) -> Optional[str]:
        """复制多维表格应用（仅结构），并转移所有权。"""
        logger.info(f"正在复制应用 {source_app_token} 到文件夹 {folder_token} (名称: {name}) ...")
        try:
            req = CopyAppRequest.builder() \
                .app_token(source_app_token) \
                .request_body(CopyAppRequestBody.builder()
                    .name(name)
                    .folder_token(folder_token)
                    .without_content(True)
                    .build()) \
                .build()
            
            resp = self.client.bitable.v1.app.copy(req)
            if not resp.success() or not resp.data or not resp.data.app:
                logger.error(f"复制多维表格失败: {resp.msg} (code: {resp.code})")
                
                if resp.code == 1254701:
                    self.last_error = "权限不足 (1254701)。请检查：\n1. 机器人是否拥有源表格的「可阅读」权限；\n2. 机器人是否拥有目标文件夹（'自动分析'）的「可编辑」权限。\n如果文件夹已存在但机器人无权限，请在云文档中找到该文件夹并给机器人添加「可编辑」权限。"
                else:
                    self.last_error = f"复制多维表格失败: {resp.msg} (code: {resp.code})"
                    
                return None
            
            app_token = resp.data.app.app_token
            logger.info(f"已复制多维表格 App Token: {app_token}")
            
            # 转移所有权
            if user_id:
                self.transfer_owner(app_token, user_id, "bitable")
                
            return app_token
        except Exception as e:
            logger.error(f"复制多维表格时发生异常: {e}")
            return None

    def add_member_permission(self, token: str, member_id: str, type: str = "bitable", role: str = "full_access", member_type: str = "openid") -> bool:
        """为用户或应用添加协作者权限。默认添加为管理员 (full_access)。"""
        logger.info(f"正在为 {member_type} {member_id} 添加 {type} 的 {role} 权限...")
        try:
            req = CreatePermissionMemberRequest.builder() \
                .token(token) \
                .type(type) \
                .need_notification(False) \
                .request_body(BaseMember.builder()
                    .member_type(member_type) 
                    .member_id(member_id)
                    .perm(role)
                    .build()) \
                .build()
            
            resp = self.client.drive.v1.permission_member.create(req)
            if not resp.success():
                # 如果已经是协作者，API 会报错，这种情况忽略
                if "already exists" in str(resp.msg).lower() or resp.code == 106212 or resp.code == 1063003:
                    logger.info(f"该成员已拥有权限或操作已忽略 (Code: {resp.code})。")
                    return True
                logger.error(f"添加权限失败: {resp.msg} (Code: {resp.code})")
                return False
                
            logger.info(f"权限添加成功。")
            return True
        except Exception as e:
            logger.error(f"添加权限时发生异常: {e}")
            return False

    def init_table_fields(self, app_token: str, table_id: str) -> bool:
        """初始化默认表的字段。如果字段已存在(或有对应别名)则跳过。"""
        logger.info(f"正在初始化 Table ID: {table_id} 的字段...")
        
        # 1. 获取现有字段，避免重复创建报错
        existing_fields = self.get_table_field_types(app_token, table_id)
        if existing_fields:
            logger.info(f"检测到已有 {len(existing_fields)} 个字段，将执行增量更新。")
        
        # 字段定义
        # 类型 ID: 1=文本, 2=数字, 3=单选, 15=超链接, 17=附件
        fields_to_create = [
            {"name": "素材名称", "type": 1},
            {"name": "视频链接", "type": 15},
            {"name": "缩略图", "type": 17},
            {"name": "人群", "type": 3, "options": ["年轻女性", "年轻人", "职场白领", "通用", "健身人群", "情侣", "老人", "儿童", "家长", "学生", "宝妈"]},
            {"name": "功能", "type": 3, "options": ["月暖暖", "饮食健康小助手", "健康小目标", "心理健康自测", "流感健康攻略", "药管家", "健康档案", "问答", "口强小助理", "中医养生", "综合卖点", "AI解读智能报告"]},
            {"name": "场景", "type": 3, "options": ["生活场景", "工作场景", "特殊场景"]},
            {"name": "痛点", "type": 1},
            {"name": "概述", "type": 1},
            {"name": "分析", "type": 1},
            {"name": "展现", "type": 2},
            {"name": "点击", "type": 2},
            {"name": "消耗", "type": 2},
            {"name": "激活人数", "type": 2},
            {"name": "点击率", "type": 2},
            {"name": "转换率", "type": 2},
            {"name": "来源", "type": 3, "options": ["来源A", "来源B"]} # 根据需要调整选项，或者留空动态添加？API 需要选项用于选择类型。
        ]

        for field in fields_to_create:
            # 检查字段名是否存在
            if field["name"] in existing_fields:
                continue
                
            # 别名匹配
            alias_found = False
            if field["name"] in self.FIELD_ALIASES:
                for alias in self.FIELD_ALIASES[field["name"]]:
                    if alias in existing_fields:
                        logger.info(f"字段 '{field['name']}' 已通过别名 '{alias}' 匹配，跳过创建。")
                        alias_found = True
                        break
            if alias_found:
                continue
                
            try:
                # 为选择类型构建字段属性
                prop = None
                if field["type"] in [3, 4] and "options" in field:
                    opts = [AppTableFieldPropertyOption.builder().name(o).build() for o in field["options"]]
                    prop = AppTableFieldProperty.builder().options(opts).build()

                req_body = AppTableField.builder().field_name(field["name"]).type(field["type"])
                if prop:
                    req_body.property(prop)

                req = CreateAppTableFieldRequest.builder() \
                    .app_token(app_token) \
                    .table_id(table_id) \
                    .request_body(req_body.build()) \
                    .build()
                
                resp = self.client.bitable.v1.app_table_field.create(req)
                if not resp.success():
                    # 检查字段是否已存在 (如果表不为空这很常见)
                    logger.warning(f"创建字段 '{field['name']}' 失败: {resp.msg}")
                else:
                    logger.info(f"已创建字段: {field['name']}")
                    
            except Exception as e:
                logger.error(f"创建字段 '{field['name']}' 时发生异常: {e}")
        
        return True

    def get_default_table_id(self, app_token: str) -> Optional[str]:
        """获取应用的第一个表 ID。"""
        try:
            req = ListAppTableRequest.builder().app_token(app_token).build()
            resp = self.client.bitable.v1.app_table.list(req)
            if resp.success() and resp.data and resp.data.items:
                return resp.data.items[0].table_id
            return None
        except Exception:
            return None

    def get_table_field_types(self, app_token: str, table_id: str) -> Dict[str, int]:
        """获取数据表的所有字段名及其类型。"""
        field_types = {}
        try:
            req = ListAppTableFieldRequest.builder() \
                .app_token(app_token) \
                .table_id(table_id) \
                .build()
            
            resp = self.client.bitable.v1.app_table_field.list(req)
            if resp.success() and resp.data and resp.data.items:
                for field in resp.data.items:
                    field_types[field.field_name] = field.type
            return field_types
        except Exception as e:
            logger.warning(f"获取字段类型失败: {e}")
            return {}

    def get_table_schema(self, app_token: str, table_id: str) -> List[Dict[str, Any]]:
        """获取完整的表结构定义，包括单选/多选的选项。"""
        schema = []
        try:
            req = ListAppTableFieldRequest.builder() \
                .app_token(app_token) \
                .table_id(table_id) \
                .build()
            
            resp = self.client.bitable.v1.app_table_field.list(req)
            if resp.success() and resp.data and resp.data.items:
                for field in resp.data.items:
                    item = {
                        "field_name": field.field_name,
                        "type": field.type,
                    }
                    # 如果是单选(3)或多选(4)，获取选项
                    if field.type in [3, 4] and field.property and field.property.options:
                        item["options"] = [opt.name for opt in field.property.options]
                    schema.append(item)
            return schema
        except Exception as e:
            logger.error(f"获取表结构失败: {e}")
            return []

    def _upload_image(self, file_path: str, app_token: str) -> Optional[str]:
        """上传图片到飞书云文档并返回 Token。"""
        path = Path(file_path)
        if not path.exists():
            return None
            
        try:
            file_bytes = path.read_bytes()
            file_len = len(file_bytes)
            
            request_body = UploadAllMediaRequestBody.builder() \
                .file_name(path.name) \
                .parent_type("bitable") \
                .parent_node(app_token) \
                .size(file_len) \
                .file(io.BytesIO(file_bytes)) \
                .build()
                
            response = self.client.drive.v1.media.upload_all(
                UploadAllMediaRequest.builder().request_body(request_body).build()
            )
            
            if response.code == 0:
                return response.data.file_token
            else:
                logger.warning(f"图片上传失败 ({path.name}): {response.msg}")
                return None
        except Exception as e:
            logger.error(f"图片上传错误 ({path.name}): {e}")
            return None

    def _safe_number(self, value: Any) -> Optional[float]:
        """安全地将值转换为数字，处理百分比和逗号。"""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        
        try:
            s = str(value).strip().replace(",", "")
            if s.endswith("%"):
                return float(s.rstrip("%")) / 100.0
            if not s:
                return None
            return float(s)
        except (ValueError, TypeError):
            return None

    def _resolve_field_name(self, key: str, field_types: Dict[str, int]) -> Optional[str]:
        """根据 key 查找实际的字段名 (支持别名)。"""
        # 1. 直接匹配
        if key in field_types:
            return key
        
        # 2. 别名匹配
        if key in self.FIELD_ALIASES:
            for alias in self.FIELD_ALIASES[key]:
                if alias in field_types:
                    return alias
        return None

    def _build_fields(self, item: Dict, app_token: str, field_types: Dict[str, int] = None) -> Dict[str, Any]:
        """将数据项映射到飞书字段。动态适配表结构。"""
        fields = {}
        if not field_types:
            return fields

        # 遍历 AI 返回的所有字段
        for key, val in item.items():
            if val is None:
                continue
                
            # 1. 寻找实际的飞书字段名 (直接匹配或别名匹配)
            actual_key = self._resolve_field_name(key, field_types)
            if not actual_key:
                continue # 找不到对应字段，忽略

            f_type = field_types.get(actual_key)

            # 2. 根据飞书字段类型进行转换
            # 类型 ID: 1=文本, 2=数字, 3=单选, 4=多选, 15=超链接, 17=附件, 20=多行文本
            try:
                if f_type in [1, 20]: # 文本或多行文本
                    fields[actual_key] = str(val)
                
                elif f_type == 2: # 数字
                    num_val = self._safe_number(val)
                    if num_val is not None:
                        fields[actual_key] = num_val
                
                elif f_type == 3: # 单选
                    fields[actual_key] = str(val)
                
                elif f_type == 4: # 多选
                    if isinstance(val, str):
                        # 尝试分割逗号
                        fields[actual_key] = [v.strip() for v in re.split(r'[,，]', val) if v.strip()]
                    elif isinstance(val, list):
                        fields[actual_key] = [str(v) for v in val]
                    else:
                        fields[actual_key] = [str(val)]
                
                elif f_type == 15: # 超链接
                    url = str(val).strip()
                    if url.startswith("http"):
                        fields[actual_key] = {"text": url, "link": url}
                
                elif f_type == 17: # 附件
                    # 如果是本地路径，则上传
                    if isinstance(val, str) and os.path.exists(val):
                        token = self._upload_image(val, app_token)
                        if token:
                            fields[actual_key] = [{"file_token": token}]
            
            except Exception as e:
                logger.error(f"字段转换失败: {actual_key} ({key}) = {val}, error: {e}")
        
        return fields

    def sync_data(self, data: List[Dict], app_token: str = None, table_id: str = None):
        """将字典列表同步到飞书。"""
        target_app_token = app_token if app_token else self.app_token
        target_table_id = table_id if table_id else self.table_id
        
        if not data:
            logger.info("没有数据需要同步。")
            return

        logger.info("开始同步到飞书...")
        logger.info(f"App Token: {target_app_token}")
        logger.info(f"Table ID: {target_table_id}")
        
        # 获取字段类型以处理多选字段
        field_types = self.get_table_field_types(target_app_token, target_table_id)
        if field_types:
            logger.info(f"已获取 {len(field_types)} 个字段的类型定义")
        
        success = 0
        fail = 0
        
        pbar = tqdm(data, desc="Syncing")
        for idx, item in enumerate(pbar):
            try:
                fields = self._build_fields(item, target_app_token, field_types)
                if not fields:
                    continue

                req = CreateAppTableRecordRequest.builder() \
                    .app_token(target_app_token) \
                    .table_id(target_table_id) \
                    .request_body({"fields": fields}) \
                    .build()
                
                resp = self.client.bitable.v1.app_table_record.create(req)
                
                if resp.code == 0:
                    success += 1
                else:
                    fail += 1
                    logger.error(f"第 {idx+1} 行失败: {resp.msg} (Code: {resp.code})")
                
                # 速率限制
                time.sleep(0.2)
                
            except Exception as e:
                fail += 1
                logger.error(f"第 {idx+1} 行错误: {e}")

        logger.info(f"同步完成! 成功: {success} | 失败: {fail}")
