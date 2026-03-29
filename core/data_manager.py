"""
数据管理模块

提供用户数据的读写、缓存管理等功能。
数据存储使用YAML格式，存储在 data/astrbot_plugin_Qsign/ 目录下。
"""

import os
from pathlib import Path

import aiofiles
import yaml
from astrbot.api import logger


class DataManager:
    """数据管理器

    管理用户签到数据、购买记录等数据的读写和缓存。
    """

    def __init__(self, plugin_dir: str):
        """初始化数据管理器

        Args:
            plugin_dir: 插件目录路径
        """
        self.plugin_dir = plugin_dir
        self.data_dir = os.path.join("data", "astrbot_plugin_Qsign")
        self.data_file = os.path.join(self.data_dir, "sign_data.yml")
        self.purchase_data_file = os.path.join(self.data_dir, "purchase_counts.yml")

        self.sign_data: dict = {}
        self.purchase_data: dict = {}

        self._init_env()

    def _init_env(self):
        """初始化数据目录"""
        os.makedirs(self.data_dir, exist_ok=True)
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w", encoding="utf-8") as f:
                yaml.dump({}, f)
        if not os.path.exists(self.purchase_data_file):
            with open(self.purchase_data_file, "w", encoding="utf-8") as f:
                yaml.dump({}, f)

    async def load_all_data(self):
        """加载所有数据到缓存"""
        self.sign_data = await self._load_yaml_async(self.data_file)
        self.purchase_data = await self._load_yaml_async(self.purchase_data_file)
        logger.info("签到插件数据已加载到缓存。")

    async def _load_yaml_async(self, file_path: str) -> dict:
        """异步加载YAML文件

        Args:
            file_path: 文件路径

        Returns:
            解析后的字典数据
        """
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                return yaml.safe_load(content) or {}
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.error(f"异步加载YAML文件失败 ({file_path}): {e}")
            return {}

    async def _save_yaml_async(self, data: dict, file_path: str):
        """异步保存YAML文件

        Args:
            data: 要保存的字典数据
            file_path: 文件路径
        """
        try:
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                content = yaml.dump(data, allow_unicode=True)
                await f.write(content)
        except Exception as e:
            logger.error(f"异步保存YAML文件失败 ({file_path}): {e}")

    async def save_sign_data(self):
        """保存签到数据"""
        await self._save_yaml_async(self.sign_data, self.data_file)

    async def save_purchase_data(self):
        """保存购买记录数据"""
        await self._save_yaml_async(self.purchase_data, self.purchase_data_file)

    def get_user_data(self, group_id: str, user_id: str) -> dict:
        """获取用户数据

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            用户数据字典
        """
        return self.sign_data.setdefault(str(group_id), {}).setdefault(
            str(user_id),
            {
                "coins": 0.0,
                "bank": 0.0,
                "contractors": [],
                "contracted_by": None,
                "last_sign": None,
                "consecutive": 0,
            },
        )

    def get_purchase_count(self, user_id: str) -> int:
        """获取用户被购买次数

        Args:
            user_id: 用户ID

        Returns:
            被购买次数
        """
        return self.purchase_data.get(str(user_id), 0)

    def increment_purchase_count(self, user_id: str):
        """增加用户被购买次数

        Args:
            user_id: 用户ID
        """
        self.purchase_data[str(user_id)] = self.purchase_data.get(str(user_id), 0) + 1
