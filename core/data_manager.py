"""
数据管理模块

提供用户数据的读写、缓存管理等功能。
使用SQLite数据库存储所有数据（用户财富、雇员关系、购买次数）。
"""

import os
from pathlib import Path

from astrbot.api import logger

from .database import QsignDatabase


class DataManager:
    """数据管理器

    管理用户签到数据、购买记录等数据的读写和缓存。
    所有数据统一存储在SQLite数据库中。
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

        # Initialize database
        self.db = QsignDatabase()

    async def init(self):
        """异步初始化数据库连接并迁移数据"""
        await self.db.init()

        # Check if there's old YAML data to migrate
        purchase_data = {}
        yaml_data = {}

        # Load purchase data from YAML if exists
        if os.path.exists(self.purchase_data_file):
            try:
                import yaml
                with open(self.purchase_data_file, "r", encoding="utf-8") as f:
                    purchase_data = yaml.safe_load(f) or {}
                logger.info(f"已从YAML加载购买次数数据: {len(purchase_data)} 条")
            except Exception as e:
                logger.warning(f"加载YAML购买次数数据失败: {e}")

        # Load user data from YAML if exists
        if os.path.exists(self.data_file):
            try:
                import yaml
                with open(self.data_file, "r", encoding="utf-8") as f:
                    yaml_data = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning(f"加载YAML用户数据失败: {e}")

        # Migrate data if any
        if yaml_data or purchase_data:
            user_count, contractor_count, purchase_count = await self.db.migrate_from_yaml(
                yaml_data, purchase_data
            )
            if user_count > 0 or purchase_count > 0:
                logger.info(
                    f"已从YAML迁移 {user_count} 个用户, "
                    f"{contractor_count} 个雇员关系, {purchase_count} 个购买次数到数据库"
                )
            # Rename old files to prevent re-migration
            if os.path.exists(self.data_file):
                backup_file = self.data_file + ".backup"
                os.rename(self.data_file, backup_file)
                logger.info(f"原YAML用户数据已备份到: {backup_file}")
            if os.path.exists(self.purchase_data_file):
                backup_file = self.purchase_data_file + ".backup"
                os.rename(self.purchase_data_file, backup_file)
                logger.info(f"原YAML购买次数数据已备份到: {backup_file}")

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

    async def get_purchase_count(self, user_id: str) -> int:
        """获取用户被购买次数

        Args:
            user_id: 用户ID

        Returns:
            被购买次数
        """
        return await self.db.get_purchase_count(user_id)

    async def increment_purchase_count(self, user_id: str):
        """增加用户被购买次数

        Args:
            user_id: 用户ID
        """
        await self.db.increment_purchase_count(user_id)

    async def close(self):
        """关闭数据库连接"""
        await self.db.close()
