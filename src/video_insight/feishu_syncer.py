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

from .config import config

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
        """Create a new Bitable app and return its App Token."""
        print(f"[Feishu] Creating Bitable App: {name} in folder {folder_token}...")
        try:
            # Use Bitable API to create app
            req = CreateAppRequest.builder() \
                .request_body(ReqApp.builder()
                    .name(name)
                    .folder_token(folder_token)
                    .build()) \
                .build()
            
            resp = self.client.bitable.v1.app.create(req)
            if not resp.success():
                print(f"[Error] Failed to create Bitable: {resp.msg}")
                return None
            
            # Response data structure: resp.data.app.app_token
            app_token = resp.data.app.app_token
            print(f"[Feishu] Created Bitable App Token: {app_token}")
            return app_token
        except Exception as e:
            print(f"[Error] Exception creating Bitable: {e}")
            return None

    def add_member_permission(self, app_token: str, user_id: str) -> bool:
        """Add user as administrator (full_access) to the Bitable app."""
        print(f"[Feishu] Adding admin permission for user: {user_id}...")
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
                print(f"[Error] Failed to add member: {resp.msg}")
                return False
                
            print(f"[Feishu] Permission granted successfully.")
            return True
        except Exception as e:
            print(f"[Error] Exception adding permission: {e}")
            return False

    def init_table_fields(self, app_token: str, table_id: str) -> bool:
        """Initialize fields for the default table."""
        print(f"[Feishu] Initializing table fields for Table ID: {table_id}...")
        
        # Field Definitions
        # Type IDs: 1=Text, 2=Number, 3=Single Select, 15=Url, 17=Attachment
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
            {"name": "来源", "type": 3, "options": ["来源A", "来源B"]} # Adjust options as needed or leave empty to dynamic add? API requires options for select.
        ]

        for field in fields_to_create:
            try:
                # Build Field Property for Select types
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
                    # Check if field already exists (common if table not empty)
                    print(f"[Warning] Failed to create field '{field['name']}': {resp.msg}")
                else:
                    print(f"[Feishu] Created field: {field['name']}")
                    
            except Exception as e:
                print(f"[Error] Exception creating field '{field['name']}': {e}")
        
        return True

    def get_default_table_id(self, app_token: str) -> Optional[str]:
        """Get the first table ID from the app."""
        try:
            req = ListAppTableRequest.builder().app_token(app_token).build()
            resp = self.client.bitable.v1.app_table.list(req)
            if resp.success() and resp.data.items:
                return resp.data.items[0].table_id
            return None
        except Exception:
            return None

    def _upload_image(self, file_path: str, app_token: str) -> Optional[str]:
        """Upload image to Feishu Drive and return token."""
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
                print(f"[Warning] Image upload failed ({path.name}): {response.msg}")
                return None
        except Exception as e:
            print(f"[Error] Image upload error ({path.name}): {e}")
            return None

    def _build_fields(self, item: Dict, app_token: str) -> Dict[str, Any]:
        """Map data item to Feishu fields."""
        fields = {}
        
        # 1. Direct Mapping Fields (Text, Select, Number)
        direct_map = [
            '素材名称', '痛点', '概述', '分析', 
            '人群', '功能', '场景', '来源',
            '展现', '点击', '消耗', '激活人数', '点击率', '转换率'
        ]
        
        for key in direct_map:
            if key in item and item[key] is not None:
                # For Select fields, value must strictly match options or it might fail if strict mode? 
                # API usually allows adding new options if configured, but here we assume safe values.
                fields[key] = item[key]

        # 2. Hyperlink Field
        if '视频链接' in item and item['视频链接']:
            url = str(item['视频链接']).strip()
            fields['视频链接'] = {"text": url, "link": url}

        # 3. Attachment Field (Thumbnail)
        thumb_path = item.get('缩略图')
        if thumb_path and os.path.exists(thumb_path):
            token = self._upload_image(thumb_path, app_token)
            if token:
                fields['缩略图'] = [{"file_token": token}]

        return fields

    def sync_data(self, data: List[Dict], app_token: str = None, table_id: str = None):
        """Sync list of dictionaries to Feishu."""
        target_app_token = app_token if app_token else self.app_token
        target_table_id = table_id if table_id else self.table_id
        
        if not data:
            print("[Sync] No data to sync.")
            return

        print(f"\n🚀 Starting Sync to Feishu...")
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
                    tqdm.write(f"❌ Row {idx+1} failed: {resp.msg}")
                
                # Rate limiting
                time.sleep(0.2)
                
            except Exception as e:
                fail += 1
                tqdm.write(f"💥 Row {idx+1} error: {e}")

        print(f"\n✅ Sync Complete! Success: {success} | Fail: {fail}")
