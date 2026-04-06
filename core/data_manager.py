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
            (
                user_count,
                contractor_count,
                purchase_count,
            ) = await self.db.migrate_from_yaml(yaml_data, purchase_data)
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

    async def get_leaderboard(
        self, group_id: str, limit: int = 10
    ) -> list[tuple[str, float]]:
        """获取财富排行榜

        Args:
            group_id: 群ID
            limit: 返回数量限制

        Returns:
            (用户ID, 身价) 元组列表
        """
        return await self.db.get_leaderboard(group_id, limit)

    async def get_group_users(self, group_id: str) -> list[str]:
        """获取群所有用户ID列表

        Args:
            group_id: 群ID

        Returns:
            用户ID列表
        """
        return await self.db.get_group_users(group_id)

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

    async def record_at_reward(
        self,
        group_id: str,
        user_id: str,
        reward_date: str,
        reward_amount: float,
    ) -> bool:
        """记录at奖励

        Args:
            group_id: 群ID
            user_id: 用户ID
            reward_date: 奖励日期 (YYYY-MM-DD)
            reward_amount: 奖励金额

        Returns:
            是否成功
        """
        return await self.db.record_at_reward(
            group_id, user_id, reward_date, reward_amount
        )

    async def get_user_at_reward_count(
        self,
        group_id: str,
        user_id: str,
        reward_date: str,
    ) -> int:
        """获取用户指定日期的at奖励次数

        Args:
            group_id: 群ID
            user_id: 用户ID
            reward_date: 奖励日期 (YYYY-MM-DD)

        Returns:
            奖励次数
        """
        return await self.db.get_user_at_reward_count(group_id, user_id, reward_date)

    async def get_user_at_reward_total(
        self,
        group_id: str,
        user_id: str,
        reward_date: str,
    ) -> float:
        """获取用户指定日期的at奖励总金额

        Args:
            group_id: 群ID
            user_id: 用户ID
            reward_date: 奖励日期 (YYYY-MM-DD)

        Returns:
            奖励总金额
        """
        return await self.db.get_user_at_reward_total(group_id, user_id, reward_date)

    def is_db_initialized(self) -> bool:
        """检查数据库是否已初始化

        Returns:
            是否已初始化
        """
        return self.db._initialized

    async def get_redeem_code(self, code: str) -> dict | None:
        """获取兑换码信息

        Args:
            code: 兑换码

        Returns:
            兑换码信息字典，如果不存在则返回 None
        """
        return await self.db.get_redeem_code(code)

    async def use_redeem_code(
        self, group_id: str, user_id: str, code: str
    ) -> tuple[bool, str, float]:
        """使用兑换码（原子操作）

        Args:
            group_id: 群ID
            user_id: 用户ID
            code: 兑换码

        Returns:
            (是否成功, 错误信息, 奖励金额)
        """
        from datetime import datetime

        # 获取兑换码信息
        redeem_code = await self.db.get_redeem_code(code)
        if not redeem_code:
            return False, "兑换码不存在", 0.0

        # 检查是否过期（手动设置）
        if redeem_code["is_expired"]:
            return False, "兑换码已过期", 0.0

        # 检查时间过期
        if redeem_code["expire_time"]:
            try:
                expire_dt = datetime.strptime(
                    redeem_code["expire_time"], "%Y-%m-%d %H:%M"
                )
                if datetime.now() > expire_dt:
                    return False, "兑换码已过期", 0.0
            except ValueError:
                logger.warning(f"兑换码过期时间格式错误: {redeem_code['expire_time']}")

        # 检查使用次数
        max_uses = redeem_code["max_uses"]
        if max_uses > 0 and redeem_code["used_count"] >= max_uses:
            return False, "兑换码已被领完", 0.0

        # 检查群限制
        enabled_groups_str = redeem_code.get("enabled_groups", "")
        if enabled_groups_str:
            enabled_groups = [
                g.strip() for g in enabled_groups_str.split(",") if g.strip()
            ]
            if enabled_groups and str(group_id) not in enabled_groups:
                return False, "该兑换码在当前群不可用", 0.0

        # 检查用户是否已兑换
        has_redeemed = await self.db.has_user_redeemed(group_id, user_id, code)
        if has_redeemed:
            return False, "您已经兑换过该兑换码了", 0.0

        # 发放奖励
        reward_amount = redeem_code["reward_amount"]
        user_data = await self.get_user_data(group_id, user_id)
        user_data["coins"] += reward_amount
        await self.save_user_data(group_id, user_id, user_data)

        # 更新兑换码使用次数
        await self.db.increment_redeem_code_used_count(code)

        # 记录用户兑换
        await self.db.record_user_redeem(group_id, user_id, code, reward_amount)

        return True, "兑换成功", reward_amount

    async def get_all_redeem_codes(self) -> list[dict]:
        """获取所有兑换码列表

        Returns:
            兑换码信息列表
        """
        return await self.db.get_redeem_code_list()

    async def get_redeem_records_by_code(self, code: str) -> list[dict]:
        """获取指定兑换码的兑换记录

        Args:
            code: 兑换码

        Returns:
            兑换记录列表
        """
        return await self.db.get_redeem_records_by_code(code)

    async def sync_redeem_codes_from_config(self, config_codes: list[dict]):
        """从配置同步兑换码到数据库

        Args:
            config_codes: 配置中的兑换码列表
        """
        from datetime import datetime

        valid_count = 0
        for code_config in config_codes:
            code = code_config.get("code", "").strip()
            if not code:
                continue

            description = code_config.get("description", "")
            reward_amount = code_config.get("reward_amount", 0.0)
            is_expired = code_config.get("is_expired", False)
            expire_time = code_config.get("expire_time", "")
            max_uses = code_config.get("max_uses", 0)
            enabled_groups = code_config.get("enabled_groups", [])

            # 校验过期时间格式
            if expire_time:
                try:
                    datetime.strptime(expire_time, "%Y-%m-%d %H:%M")
                except ValueError:
                    logger.warning(
                        f"兑换码 '{code}' 的过期时间格式错误: '{expire_time}'，"
                        f"应为 'YYYY-MM-DD HH:MM' 格式，已清空该字段"
                    )
                    expire_time = ""

            # 将群列表转换为逗号分隔的字符串
            enabled_groups_str = (
                ",".join(str(g) for g in enabled_groups) if enabled_groups else ""
            )

            # 保存到数据库（保留已使用次数）
            await self.db.save_redeem_code(
                code=code,
                description=description,
                reward_amount=reward_amount,
                is_expired=is_expired,
                expire_time=expire_time,
                max_uses=max_uses,
                enabled_groups=enabled_groups_str,
            )
            valid_count += 1

        logger.info(f"已从配置同步 {valid_count} 个兑换码到数据库")

    async def record_purchase(
        self,
        group_id: str,
        owner_id: str,
        contractor_id: str,
        purchase_price: float,
    ) -> bool:
        """记录购买历史

        Args:
            group_id: 群ID
            owner_id: 雇主ID
            contractor_id: 雇员ID
            purchase_price: 购买价格

        Returns:
            是否成功
        """
        return await self.db.record_purchase(
            group_id, owner_id, contractor_id, purchase_price
        )

    async def get_latest_purchase_price(
        self,
        group_id: str,
        contractor_id: str,
    ) -> float:
        """获取雇员最新的购买价格

        Args:
            group_id: 群ID
            contractor_id: 雇员ID

        Returns:
            最新的购买价格，如果没有记录则返回0
        """
        return await self.db.get_latest_purchase_price(group_id, contractor_id)

    async def get_owner_currency_balance(self, group_id: str, user_id: str) -> float:
        """获取用户群主货币余额

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            群主货币余额
        """
        return await self.db.get_owner_currency_balance(group_id, user_id)

    async def add_owner_currency_balance(
        self, group_id: str, user_id: str, amount: float
    ) -> bool:
        """增加用户群主货币余额

        Args:
            group_id: 群ID
            user_id: 用户ID
            amount: 增加数量（可为负数）

        Returns:
            是否成功
        """
        current_balance = await self.db.get_owner_currency_balance(group_id, user_id)
        new_balance = current_balance + amount
        return await self.db.update_owner_currency_balance(
            group_id, user_id, new_balance
        )

    async def get_wealth_gap_penalty(self, group_id: str, user_id: str) -> dict[str, Any]:
        """获取财富榜差距惩罚状态

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            惩罚状态字典
        """
        return await self.db.get_wealth_gap_penalty(group_id, user_id)

    async def set_wealth_gap_penalty(
        self,
        group_id: str,
        user_id: str,
        has_debuff: bool,
        current_penalty_rate: float = 0.0,
        debuff_start_time: int = 0,
    ) -> bool:
        """设置财富榜差距惩罚状态

        Args:
            group_id: 群ID
            user_id: 用户ID
            has_debuff: 是否有debuff
            current_penalty_rate: 当前扣除比例
            debuff_start_time: debuff开始时间

        Returns:
            是否成功
        """
        return await self.db.set_wealth_gap_penalty(
            group_id, user_id, has_debuff, current_penalty_rate, debuff_start_time
        )

    async def update_penalty_last_time(
        self, group_id: str, user_id: str, last_penalty_time: int
    ) -> bool:
        """更新上次惩罚时间

        Args:
            group_id: 群ID
            user_id: 用户ID
            last_penalty_time: 上次惩罚时间戳

        Returns:
            是否成功
        """
        return await self.db.update_penalty_last_time(group_id, user_id, last_penalty_time)

    async def close(self):
        """关闭数据库连接"""
        await self.db.close()
