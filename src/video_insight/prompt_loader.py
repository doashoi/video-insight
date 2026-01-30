import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("PromptLoader")

class PromptLoader:
    """提示词加载类，负责从 prompts 目录加载 .md 文件"""
    
    def __init__(self, prompts_dir: Optional[Path] = None):
        if prompts_dir is None:
            # 默认指向项目根目录下的 prompts 文件夹
            from .config import config
            self.prompts_dir = config.ROOT_DIR / "prompts"
        else:
            self.prompts_dir = prompts_dir
            
        if not self.prompts_dir.exists():
            logger.warning(f"提示词目录不存在: {self.prompts_dir}")

    def load(self, relative_path: str) -> str:
        """
        根据相对路径加载提示词内容
        :param relative_path: 相对于 prompts 目录的路径，如 'video_analyzer/visual_description.md'
        :return: 提示词内容
        """
        file_path = self.prompts_dir / relative_path
        if not file_path.exists():
            logger.error(f"提示词文件不存在: {file_path}")
            return ""
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                # 简单移除 markdown 标题 (如果存在)
                if content.startswith("#"):
                    lines = content.split("\n")
                    if lines[0].startswith("#"):
                        content = "\n".join(lines[1:]).strip()
                return content
        except Exception as e:
            logger.error(f"加载提示词文件失败 {file_path}: {e}")
            return ""

# 单例模式方便直接使用
prompt_loader = PromptLoader()
