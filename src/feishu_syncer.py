import os
import sys
import time
import io
import pandas as pd
from pathlib import Path
from tqdm import tqdm
from typing import Dict, Any, Optional, List, Tuple

import lark_oapi
from lark_oapi import Client, FEISHU_DOMAIN
from lark_oapi.api.drive.v1 import (
    UploadAllMediaRequest, UploadAllMediaRequestBody, 
    CreatePermissionMemberRequest, BaseMember
)
from lark_oapi.api.bitable.v1 import (
    CreateAppTableRecordRequest, 
    CreateAppTableFieldRequest, AppTableField, AppTableFieldProperty, AppTableFieldPropertyOption,
    CreateAppRequest, ReqApp, ListAppTableRequest
)

from config import config

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

    def create_bitable(self, name: str, folder_token: str) -> Optional[str]:
        """创建一个新的多维表格应用并返回其 App Token。"""
        print(f"[Feishu] 正在文件夹 {folder_token} 中创建多维表格应用: {name} ...")
        try:
            # 使用多维表格 API 创建应用
            req = CreateAppRequest.builder() \
                .request_body(ReqApp.builder()
                    .name(name)
                    .folder_token(folder_token)
                    .build()) \
                .build()
            
            resp = self.client.bitable.v1.app.create(req)
            if not resp.success():
                print(f"[Error] 创建多维表格失败: {resp.msg}")
                return None
            
            # 响应数据结构: resp.data.app.app_token
            app_token = resp.data.app.app_token
            print(f"[Feishu] 已创建多维表格 App Token: {app_token}")
            return app_token
        except Exception as e:
            print(f"[Error] 创建多维表格时发生异常: {e}")
            return None

    def add_member_permission(self, app_token: str, user_id: str) -> bool:
        """将用户添加为多维表格应用的管理员 (full_access)。"""
        print(f"[Feishu] 正在为用户 {user_id} 添加管理员权限...")
        try:
            req = CreatePermissionMemberRequest.builder() \
                .token(app_token) \
                .type("bitable") \
                .need_notification(True) \
                .request_body(BaseMember.builder()
                    .member_type("openid") 
                    .member_id(user_id)
                    .perm("full_access")
                    .build()) \
                .build()
            
            resp = self.client.drive.v1.permission_member.create(req)
            if not resp.success():
                print(f"[Error] 添加成员失败: {resp.msg}")
                return False
                
            print(f"[Feishu] 权限添加成功。")
            return True
        except Exception as e:
            print(f"[Error] 添加权限时发生异常: {e}")
            return False

    def init_table_fields(self, app_token: str, table_id: str) -> bool:
        """初始化默认表的字段。"""
        print(f"[Feishu] 正在初始化 Table ID: {table_id} 的字段...")
        
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
                    print(f"[Warning] 创建字段 '{field['name']}' 失败: {resp.msg}")
                else:
                    print(f"[Feishu] 已创建字段: {field['name']}")
                    
            except Exception as e:
                print(f"[Error] 创建字段 '{field['name']}' 时发生异常: {e}")
        
        return True

    def get_default_table_id(self, app_token: str) -> Optional[str]:
        """获取应用的第一个表 ID。"""
        try:
            req = ListAppTableRequest.builder().app_token(app_token).build()
            resp = self.client.bitable.v1.app_table.list(req)
            if resp.success() and resp.data.items:
                return resp.data.items[0].table_id
            return None
        except Exception:
            return None

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
                .parent_type("bitable_image") \
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
                print(f"[Warning] 图片上传失败 ({path.name}): {response.msg}")
                return None
        except Exception as e:
            print(f"[Error] 图片上传错误 ({path.name}): {e}")
            return None

    def _build_fields(self, item: Dict, app_token: str) -> Dict[str, Any]:
        """将数据项映射到飞书字段。"""
        fields = {}
        
        # 1. 直接映射字段 (文本, 选项, 数字)
        direct_map = [
            '素材名称', '痛点', '概述', '分析', 
            '人群', '功能', '场景', '来源',
            '展现', '点击', '消耗', '激活人数', '点击率', '转换率'
        ]
        
        for key in direct_map:
            if key in item and item[key] is not None:
                # 对于选择字段，值必须严格匹配选项，否则在严格模式下可能失败
                # API 通常允许配置为添加新选项，但这里我们假设值是安全的。
                fields[key] = item[key]

        # 2. 超链接字段
        if '视频链接' in item and item['视频链接']:
            url = str(item['视频链接']).strip()
            fields['视频链接'] = {"text": url, "link": url}

        # 3. 附件字段 (缩略图)
        thumb_path = item.get('缩略图')
        if thumb_path and os.path.exists(thumb_path):
            token = self._upload_image(thumb_path, app_token)
            if token:
                fields['缩略图'] = [{"file_token": token}]

        return fields

    def sync_data(self, data: List[Dict], app_token: str = None, table_id: str = None):
        """将字典列表同步到飞书。"""
        target_app_token = app_token if app_token else self.app_token
        target_table_id = table_id if table_id else self.table_id
        
        if not data:
            print("[Sync] 没有数据需要同步。")
            return

        print(f"\n🚀 开始同步到飞书...")
        print(f"   App Token: {target_app_token}")
        print(f"   Table ID: {target_table_id}")
        
        success = 0
        fail = 0
        
        pbar = tqdm(data, desc="Syncing")
        for idx, item in enumerate(pbar):
            try:
                fields = self._build_fields(item, target_app_token)
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
                    tqdm.write(f"❌ 第 {idx+1} 行失败: {resp.msg}")
                
                # 速率限制
                time.sleep(0.2)
                
            except Exception as e:
                fail += 1
                tqdm.write(f"💥 第 {idx+1} 行错误: {e}")

        print(f"\n✅ 同步完成! 成功: {success} | 失败: {fail}")
