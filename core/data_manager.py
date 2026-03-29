"""
数据管理模块

提供用户数据的读写、缓存管理等功能。
使用SQLite数据库存储用户财富数据和雇员关系，使用YAML存储购买次数配置。
"""

import os
from pathlib import Path

import aiofiles
import yaml
from astrbot.api import logger

from .database import QsignDatabase


class DataManager:
    """数据管理器

    管理用户签到数据、购买记录等数据的读写和缓存。
    用户财富数据和雇员关系存储在SQLite数据库中，购买次数存储在YAML中。
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

        # Initialize database
        self.db = QsignDatabase()

        self._init_env()

    def _init_env(self):
        """初始化数据目录"""
        os.makedirs(self.data_dir, exist_ok=True)
        if not os.path.exists(self.purchase_data_file):
            with open(self.purchase_data_file, "w", encoding="utf-8") as f:
                yaml.dump({}, f)

    async def init(self):
        """异步初始化数据库连接并迁移数据"""
        await self.db.init()

        # Load purchase data from YAML
        self.purchase_data = await self._load_yaml_async(self.purchase_data_file)

        # Check if there's old YAML data to migrate
        if os.path.exists(self.data_file):
            yaml_data = await self._load_yaml_async(self.data_file)
            if yaml_data:
                user_count, contractor_count = await self.db.migrate_from_yaml(yaml_data)
                if user_count > 0:
                    logger.info(f"已从YAML迁移 {user_count} 个用户数据到数据库")
                # Rename old file to prevent re-migration
                backup_file = self.data_file + ".backup"
                os.rename(self.data_file, backup_file)
                logger.info(f"原YAML数据已备份到: {backup_file}")

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

    async def save_purchase_data(self):
        """保存购买记录数据"""
        await self._save_yaml_async(self.purchase_data, self.purchase_data_file)

    async def get_user_data(self, group_id: str, user_id: str) -> dict:
        """获取用户数据

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            用户数据字典
        """
        db_data = await self.db.get_user_data(group_id, user_id)

        # Get contractors and owner info from database
        contractors = await self.db.get_contractors(group_id, user_id)
        owner_id = await self.db.get_owner(group_id, user_id)

        return {
            "coins": db_data.get("coins", 0.0),
            "bank": db_data.get("bank", 0.0),
            "contractors": contractors,
            "contracted_by": owner_id,
            "last_sign": db_data.get("last_sign", ""),
            "consecutive": db_data.get("consecutive", 0),
        }

    async def save_user_data(self, group_id: str, user_id: str, user_data: dict):
        """保存用户数据到数据库

        Args:
            group_id: 群ID
            user_id: 用户ID
            user_data: 用户数据字典
        """
        await self.db.update_user_data(
            group_id,
            user_id,
            {
                "coins": user_data.get("coins", 0.0),
                "bank": user_data.get("bank", 0.0),
                "last_sign": user_data.get("last_sign", ""),
                "consecutive": user_data.get("consecutive", 0),
            },
        )

    async def add_contractor(self, group_id: str, owner_id: str, contractor_id: str):
        """添加雇员关系

        Args:
            group_id: 群ID
            owner_id: 雇主ID
            contractor_id: 雇员ID
        """
        await self.db.add_contractor(group_id, owner_id, contractor_id)

    async def remove_contractor(self, group_id: str, owner_id: str, contractor_id: str):
        """移除雇员关系

        Args:
            group_id: 群ID
            owner_id: 雇主ID
            contractor_id: 雇员ID
        """
        await self.db.remove_contractor(group_id, owner_id, contractor_id)

    async def clear_contractors(self, group_id: str, owner_id: str):
        """清空雇主的所有雇员

        Args:
            group_id: 群ID
            owner_id: 雇主ID
        """
        await self.db.clear_contractors(group_id, owner_id)

    async def get_leaderboard(self, group_id: str, limit: int = 10) -> list[tuple[str, float]]:
        """获取财富排行榜

        Args:
            group_id: 群ID
            limit: 返回数量限制

        Returns:
            (用户ID, 总资产) 元组列表
        """
        return await self.db.get_leaderboard(group_id, limit)

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

    async def close(self):
        """关闭数据库连接"""
        await self.db.close()
