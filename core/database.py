"""
数据库管理模块

提供用户财富数据和雇员关系的持久化存储，使用 SQLite 数据库。
所有数据按 group_id 隔离存储。

存储路径遵守 AstrBot 规范：
data/plugin_data/astrbot_plugin_Qsign/qsign.db
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite
from astrbot.api import logger

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:
    get_astrbot_data_path = None


@dataclass
class UserWealthData:
    """用户财富数据类"""

    user_id: str
    group_id: str
    coins: float = 0.0
    bank: float = 0.0
    last_sign: str = ""
    consecutive: int = 0
    created_at: int = 0
    updated_at: int = 0


@dataclass
class ContractorRelation:
    """雇员关系数据类"""

    owner_id: str
    contractor_id: str
    group_id: str
    created_at: int = 0


class QsignDatabase:
    """Qsign Plus 插件数据库类

    管理用户财富数据和雇员关系的存储、查询，支持群组隔离。
    """

    def __init__(self, plugin_name: str = "astrbot_plugin_Qsign"):
        """初始化数据库

        Args:
            plugin_name: 插件名称
        """
        self.plugin_name = plugin_name
        self.db_path = self._get_db_path()
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

    def _get_db_path(self) -> Path:
        """获取数据库文件路径，遵守 AstrBot 存储规范

        Returns:
            数据库文件路径: data/plugin_data/{plugin_name}/qsign.db
        """
        if get_astrbot_data_path:
            base_path = Path(get_astrbot_data_path())
        else:
            base_path = Path(__file__).resolve().parent.parent.parent / "data"

        db_dir = base_path / "plugin_data" / self.plugin_name
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "qsign.db"

    async def init(self) -> None:
        """异步初始化数据库

        创建必要的表结构和索引
        """
        async with self._init_lock:
            if self._initialized:
                return

            self._conn = await aiosqlite.connect(str(self.db_path))
            self._conn.row_factory = aiosqlite.Row

            # 创建用户财富数据表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS user_wealth (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    coins REAL DEFAULT 0.0,
                    bank REAL DEFAULT 0.0,
                    last_sign TEXT DEFAULT '',
                    consecutive INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(group_id, user_id)
                )
            """)

            # 创建雇员关系表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS user_contractors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contractor_id TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(group_id, owner_id, contractor_id)
                )
            """)

            # 创建购买次数表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS purchase_counts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL UNIQUE,
                    count INTEGER DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
            """)

            # 创建at奖励记录表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS at_reward_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    reward_date TEXT NOT NULL,
                    reward_count INTEGER DEFAULT 0,
                    reward_total REAL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(group_id, user_id, reward_date)
                )
            """)

            # 创建兑换码表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS redeem_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    description TEXT DEFAULT '',
                    reward_amount REAL DEFAULT 0.0,
                    is_expired INTEGER DEFAULT 0,
                    expire_time TEXT DEFAULT '',
                    max_uses INTEGER DEFAULT 0,
                    used_count INTEGER DEFAULT 0,
                    enabled_groups TEXT DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            """)

            # 创建用户兑换记录表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS user_redeem_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    reward_amount REAL DEFAULT 0.0,
                    redeemed_at INTEGER NOT NULL,
                    UNIQUE(group_id, user_id, code)
                )
            """)

            # 创建购买记录表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS purchase_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    contractor_id TEXT NOT NULL,
                    purchase_price REAL NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)

            # 创建群主货币余额表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS owner_currency_balances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    balance REAL DEFAULT 0.0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(group_id, user_id)
                )
            """)

            # 创建汇率历史表
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS exchange_rate_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    rate REAL NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)

            # 创建索引
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_wealth_group
                ON user_wealth(group_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_wealth_user
                ON user_wealth(user_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_contractors_owner
                ON user_contractors(group_id, owner_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_contractors_contractor
                ON user_contractors(group_id, contractor_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_redeem_records_user
                ON user_redeem_records(group_id, user_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_redeem_records_code
                ON user_redeem_records(code)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_purchase_records_contractor
                ON purchase_records(group_id, contractor_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_owner_currency_group_user
                ON owner_currency_balances(group_id, user_id)
            """)
            await self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_exchange_rate_group_time
                ON exchange_rate_history(group_id, recorded_at)
            """)

            await self._conn.commit()
            self._initialized = True
            logger.info(f"[{self.plugin_name}] 数据库初始化完成: {self.db_path}")

    async def get_user_data(self, group_id: str, user_id: str) -> dict[str, Any]:
        """获取用户财富数据

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            用户数据字典，如果不存在则返回默认值
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT * FROM user_wealth 
                WHERE group_id = ? AND user_id = ?
                """,
                (str(group_id), str(user_id)),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "coins": row["coins"],
                        "bank": row["bank"],
                        "last_sign": row["last_sign"],
                        "consecutive": row["consecutive"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取用户数据失败: {e}")

        # 返回默认值
        return {
            "coins": 0.0,
            "bank": 0.0,
            "last_sign": "",
            "consecutive": 0,
            "created_at": 0,
            "updated_at": 0,
        }

    async def update_user_data(
        self, group_id: str, user_id: str, data: dict[str, Any]
    ) -> bool:
        """更新用户财富数据

        Args:
            group_id: 群ID
            user_id: 用户ID
            data: 要更新的数据字典

        Returns:
            是否更新成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())

            # 检查用户是否存在
            async with self._conn.execute(
                "SELECT 1 FROM user_wealth WHERE group_id = ? AND user_id = ?",
                (str(group_id), str(user_id)),
            ) as cursor:
                exists = await cursor.fetchone()

            if exists:
                # 更新现有记录
                fields = []
                values = []
                for key, value in data.items():
                    if key in ["coins", "bank", "last_sign", "consecutive"]:
                        fields.append(f"{key} = ?")
                        values.append(value)
                values.extend([now, str(group_id), str(user_id)])

                if fields:
                    await self._conn.execute(
                        f"""
                        UPDATE user_wealth 
                        SET {", ".join(fields)}, updated_at = ?
                        WHERE group_id = ? AND user_id = ?
                        """,
                        values,
                    )
            else:
                # 插入新记录
                coins = data.get("coins", 0.0)
                bank = data.get("bank", 0.0)
                last_sign = data.get("last_sign", "")
                consecutive = data.get("consecutive", 0)

                await self._conn.execute(
                    """
                    INSERT INTO user_wealth 
                    (group_id, user_id, coins, bank, last_sign, consecutive, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(group_id),
                        str(user_id),
                        coins,
                        bank,
                        last_sign,
                        consecutive,
                        now,
                        now,
                    ),
                )

            await self._conn.commit()
            return True

        except Exception as e:
            logger.error(f"[{self.plugin_name}] 更新用户数据失败: {e}")
            return False

    async def get_group_users(self, group_id: str) -> list[dict[str, Any]]:
        """获取群内所有用户数据

        Args:
            group_id: 群ID

        Returns:
            用户数据列表
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT * FROM user_wealth 
                WHERE group_id = ?
                ORDER BY coins + bank DESC
                """,
                (str(group_id),),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "user_id": row["user_id"],
                        "coins": row["coins"],
                        "bank": row["bank"],
                        "last_sign": row["last_sign"],
                        "consecutive": row["consecutive"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取群用户数据失败: {e}")
            return []

    async def get_group_users(self, group_id: str) -> list[str]:
        """获取群所有用户ID列表

        Args:
            group_id: 群ID

        Returns:
            用户ID列表
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT user_id FROM user_wealth WHERE group_id = ?
                """,
                (str(group_id),),
            ) as cursor:
                rows = await cursor.fetchall()
                return [row["user_id"] for row in rows]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取群用户列表失败: {e}")
            return []

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
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT user_id, coins, bank 
                FROM user_wealth 
                WHERE group_id = ?
                ORDER BY (coins + bank) DESC
                LIMIT ?
                """,
                (str(group_id), limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [(row["user_id"], row["coins"] + row["bank"]) for row in rows]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取排行榜失败: {e}")
            return []

    async def add_contractor(
        self, group_id: str, owner_id: str, contractor_id: str
    ) -> bool:
        """添加雇员关系

        Args:
            group_id: 群ID
            owner_id: 雇主ID
            contractor_id: 雇员ID

        Returns:
            是否添加成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT OR REPLACE INTO user_contractors 
                (group_id, owner_id, contractor_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(group_id), str(owner_id), str(contractor_id), now),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 添加雇员关系失败: {e}")
            return False

    async def remove_contractor(
        self, group_id: str, owner_id: str, contractor_id: str
    ) -> bool:
        """移除雇员关系

        Args:
            group_id: 群ID
            owner_id: 雇主ID
            contractor_id: 雇员ID

        Returns:
            是否移除成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            await self._conn.execute(
                """
                DELETE FROM user_contractors 
                WHERE group_id = ? AND owner_id = ? AND contractor_id = ?
                """,
                (str(group_id), str(owner_id), str(contractor_id)),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 移除雇员关系失败: {e}")
            return False

    async def get_contractors(self, group_id: str, owner_id: str) -> list[str]:
        """获取雇主的雇员列表

        Args:
            group_id: 群ID
            owner_id: 雇主ID

        Returns:
            雇员ID列表
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT contractor_id FROM user_contractors 
                WHERE group_id = ? AND owner_id = ?
                """,
                (str(group_id), str(owner_id)),
            ) as cursor:
                rows = await cursor.fetchall()
                return [row["contractor_id"] for row in rows]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取雇员列表失败: {e}")
            return []

    async def get_owner(self, group_id: str, contractor_id: str) -> str | None:
        """获取雇员的主人

        Args:
            group_id: 群ID
            contractor_id: 雇员ID

        Returns:
            雇主ID，如果没有则返回 None
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT owner_id FROM user_contractors 
                WHERE group_id = ? AND contractor_id = ?
                """,
                (str(group_id), str(contractor_id)),
            ) as cursor:
                row = await cursor.fetchone()
                return row["owner_id"] if row else None
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取主人信息失败: {e}")
            return None

    async def clear_contractors(self, group_id: str, owner_id: str) -> bool:
        """清空雇主的所有雇员

        Args:
            group_id: 群ID
            owner_id: 雇主ID

        Returns:
            是否清空成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            await self._conn.execute(
                """
                DELETE FROM user_contractors 
                WHERE group_id = ? AND owner_id = ?
                """,
                (str(group_id), str(owner_id)),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 清空雇员列表失败: {e}")
            return False

    async def get_purchase_count(self, user_id: str) -> int:
        """获取用户被购买次数

        Args:
            user_id: 用户ID

        Returns:
            被购买次数
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                "SELECT count FROM purchase_counts WHERE user_id = ?",
                (str(user_id),),
            ) as cursor:
                row = await cursor.fetchone()
                return row["count"] if row else 0
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取购买次数失败: {e}")
            return 0

    async def increment_purchase_count(self, user_id: str) -> bool:
        """增加用户被购买次数

        Args:
            user_id: 用户ID

        Returns:
            是否成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO purchase_counts (user_id, count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                count = count + 1,
                updated_at = ?
                """,
                (str(user_id), now, now),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 增加购买次数失败: {e}")
            return False

    async def migrate_from_yaml(
        self,
        yaml_data: dict[str, dict[str, dict]],
        purchase_data: dict[str, int] | None = None,
    ) -> tuple[int, int, int]:
        """从YAML数据迁移到数据库

        Args:
            yaml_data: YAML格式的数据 {group_id: {user_id: user_data}}
            purchase_data: 购买次数数据 {user_id: count}

        Returns:
            (迁移的用户数, 迁移的雇员关系数, 迁移的购买次数)
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        user_count = 0
        contractor_count = 0
        purchase_count = 0

        try:
            for group_id, group_data in yaml_data.items():
                for user_id, user_data in group_data.items():
                    # 迁移用户数据
                    await self.update_user_data(
                        group_id,
                        user_id,
                        {
                            "coins": user_data.get("coins", 0.0),
                            "bank": user_data.get("bank", 0.0),
                            "last_sign": user_data.get("last_sign", ""),
                            "consecutive": user_data.get("consecutive", 0),
                        },
                    )
                    user_count += 1

                    # 迁移雇员关系
                    contractors = user_data.get("contractors", [])
                    for contractor_id in contractors:
                        await self.add_contractor(group_id, user_id, contractor_id)
                        contractor_count += 1

            # 迁移购买次数
            if purchase_data:
                for user_id, count in purchase_data.items():
                    if count > 0:
                        now = int(time.time())
                        await self._conn.execute(
                            """
                            INSERT INTO purchase_counts (user_id, count, updated_at)
                            VALUES (?, ?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET
                            count = ?,
                            updated_at = ?
                            """,
                            (str(user_id), count, now, count, now),
                        )
                        purchase_count += 1
                await self._conn.commit()

            logger.info(
                f"[{self.plugin_name}] 数据迁移完成: "
                f"{user_count} 个用户, {contractor_count} 个雇员关系, {purchase_count} 个购买次数"
            )
            return user_count, contractor_count, purchase_count

        except Exception as e:
            logger.error(f"[{self.plugin_name}] 数据迁移失败: {e}")
            return 0, 0, 0

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
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO at_reward_records (group_id, user_id, reward_date, reward_count, reward_total, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(group_id, user_id, reward_date) DO UPDATE SET
                reward_count = reward_count + 1,
                reward_total = reward_total + ?,
                updated_at = ?
                """,
                (
                    str(group_id),
                    str(user_id),
                    reward_date,
                    reward_amount,
                    now,
                    reward_amount,
                    now,
                ),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 记录at奖励失败: {e}")
            return False

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
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT reward_count FROM at_reward_records
                WHERE group_id = ? AND user_id = ? AND reward_date = ?
                """,
                (str(group_id), str(user_id), reward_date),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row["reward_count"]
                return 0
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取at奖励次数失败: {e}")
            return 0

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
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT reward_total FROM at_reward_records
                WHERE group_id = ? AND user_id = ? AND reward_date = ?
                """,
                (str(group_id), str(user_id), reward_date),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return row["reward_total"]
                return 0.0
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取at奖励总金额失败: {e}")
            return 0.0

    async def get_redeem_code(self, code: str) -> dict[str, Any] | None:
        """获取兑换码信息

        Args:
            code: 兑换码

        Returns:
            兑换码信息字典，如果不存在则返回 None
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                "SELECT * FROM redeem_codes WHERE code = ?",
                (str(code),),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "id": row["id"],
                        "code": row["code"],
                        "description": row["description"],
                        "reward_amount": row["reward_amount"],
                        "is_expired": bool(row["is_expired"]),
                        "expire_time": row["expire_time"],
                        "max_uses": row["max_uses"],
                        "used_count": row["used_count"],
                        "enabled_groups": row["enabled_groups"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                return None
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取兑换码信息失败: {e}")
            return None

    async def save_redeem_code(
        self,
        code: str,
        description: str = "",
        reward_amount: float = 0.0,
        is_expired: bool = False,
        expire_time: str = "",
        max_uses: int = 0,
        enabled_groups: str = "",
    ) -> bool:
        """保存或更新兑换码

        Args:
            code: 兑换码
            description: 描述
            reward_amount: 奖励数量
            is_expired: 是否过期
            expire_time: 过期时间
            max_uses: 最大使用次数
            enabled_groups: 允许使用的群列表（逗号分隔）

        Returns:
            是否成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO redeem_codes
                (code, description, reward_amount, is_expired, expire_time, max_uses, enabled_groups, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                description = ?,
                reward_amount = ?,
                is_expired = ?,
                expire_time = ?,
                max_uses = ?,
                enabled_groups = ?,
                updated_at = ?
                """,
                (
                    str(code),
                    description,
                    reward_amount,
                    1 if is_expired else 0,
                    expire_time,
                    max_uses,
                    enabled_groups,
                    now,
                    now,
                    description,
                    reward_amount,
                    1 if is_expired else 0,
                    expire_time,
                    max_uses,
                    enabled_groups,
                    now,
                ),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 保存兑换码失败: {e}")
            return False

    async def increment_redeem_code_used_count(self, code: str) -> bool:
        """增加兑换码使用次数

        Args:
            code: 兑换码

        Returns:
            是否成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                UPDATE redeem_codes
                SET used_count = used_count + 1, updated_at = ?
                WHERE code = ?
                """,
                (now, str(code)),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 增加兑换码使用次数失败: {e}")
            return False

    async def record_user_redeem(
        self,
        group_id: str,
        user_id: str,
        code: str,
        reward_amount: float,
    ) -> bool:
        """记录用户兑换

        Args:
            group_id: 群ID
            user_id: 用户ID
            code: 兑换码
            reward_amount: 奖励金额

        Returns:
            是否成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO user_redeem_records
                (group_id, user_id, code, reward_amount, redeemed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(group_id), str(user_id), str(code), reward_amount, now),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 记录用户兑换失败: {e}")
            return False

    async def has_user_redeemed(self, group_id: str, user_id: str, code: str) -> bool:
        """检查用户是否已兑换过该兑换码

        Args:
            group_id: 群ID
            user_id: 用户ID
            code: 兑换码

        Returns:
            是否已兑换
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT 1 FROM user_redeem_records
                WHERE group_id = ? AND user_id = ? AND code = ?
                """,
                (str(group_id), str(user_id), str(code)),
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 检查用户兑换记录失败: {e}")
            return False

    async def get_redeem_code_list(self) -> list[dict[str, Any]]:
        """获取所有兑换码列表

        Returns:
            兑换码信息列表
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                "SELECT * FROM redeem_codes ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "code": row["code"],
                        "description": row["description"],
                        "reward_amount": row["reward_amount"],
                        "is_expired": bool(row["is_expired"]),
                        "expire_time": row["expire_time"],
                        "max_uses": row["max_uses"],
                        "used_count": row["used_count"],
                        "enabled_groups": row["enabled_groups"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取兑换码列表失败: {e}")
            return []

    async def get_redeem_records_by_code(self, code: str) -> list[dict[str, Any]]:
        """获取指定兑换码的兑换记录

        Args:
            code: 兑换码

        Returns:
            兑换记录列表
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT * FROM user_redeem_records
                WHERE code = ?
                ORDER BY redeemed_at DESC
                """,
                (str(code),),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": row["id"],
                        "group_id": row["group_id"],
                        "user_id": row["user_id"],
                        "code": row["code"],
                        "reward_amount": row["reward_amount"],
                        "redeemed_at": row["redeemed_at"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取兑换记录失败: {e}")
            return []

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
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO purchase_records
                (group_id, owner_id, contractor_id, purchase_price, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(group_id), str(owner_id), str(contractor_id), purchase_price, now),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 记录购买历史失败: {e}")
            return False

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
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT purchase_price FROM purchase_records
                WHERE group_id = ? AND contractor_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(group_id), str(contractor_id)),
            ) as cursor:
                row = await cursor.fetchone()
                return row["purchase_price"] if row else 0.0
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取购买价格失败: {e}")
            return 0.0

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None
            self._initialized = False
            logger.info(f"[{self.plugin_name}] 数据库连接已关闭")

    async def get_owner_currency_balance(self, group_id: str, user_id: str) -> float:
        """获取群主货币余额

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            群主货币余额，如果不存在则返回 0.0
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT balance FROM owner_currency_balances
                WHERE group_id = ? AND user_id = ?
                """,
                (str(group_id), str(user_id)),
            ) as cursor:
                row = await cursor.fetchone()
                return row["balance"] if row else 0.0
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取群主货币余额失败: {e}")
            return 0.0

    async def update_owner_currency_balance(
        self, group_id: str, user_id: str, balance: float
    ) -> bool:
        """更新群主货币余额

        Args:
            group_id: 群ID
            user_id: 用户ID
            balance: 新的余额

        Returns:
            是否更新成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO owner_currency_balances (group_id, user_id, balance, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id, user_id) DO UPDATE SET
                balance = ?,
                updated_at = ?
                """,
                (str(group_id), str(user_id), balance, now, now, balance, now),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 更新群主货币余额失败: {e}")
            return False

    async def record_exchange_rate(self, group_id: str, rate: float) -> bool:
        """记录汇率

        Args:
            group_id: 群ID
            rate: 汇率值

        Returns:
            是否记录成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            now = int(time.time())
            await self._conn.execute(
                """
                INSERT INTO exchange_rate_history (group_id, rate, recorded_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(group_id), rate, now, now),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 记录汇率失败: {e}")
            return False

    async def get_exchange_rate_history(
        self, group_id: str, days: int = 7
    ) -> list[dict[str, Any]]:
        """获取汇率历史

        Args:
            group_id: 群ID
            days: 查询天数，默认7天

        Returns:
            汇率历史记录列表
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            cutoff_time = int(time.time()) - (days * 24 * 60 * 60)
            async with self._conn.execute(
                """
                SELECT rate, recorded_at FROM exchange_rate_history
                WHERE group_id = ? AND recorded_at >= ?
                ORDER BY recorded_at DESC
                """,
                (str(group_id), cutoff_time),
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {"rate": row["rate"], "recorded_at": row["recorded_at"]}
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取汇率历史失败: {e}")
            return []

    async def get_current_exchange_rate(self, group_id: str) -> float | None:
        """获取当前汇率

        Args:
            group_id: 群ID

        Returns:
            最新汇率值，如果没有记录则返回 None
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            async with self._conn.execute(
                """
                SELECT rate FROM exchange_rate_history
                WHERE group_id = ?
                ORDER BY recorded_at DESC
                LIMIT 1
                """,
                (str(group_id),),
            ) as cursor:
                row = await cursor.fetchone()
                return row["rate"] if row else None
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 获取当前汇率失败: {e}")
            return None

    async def cleanup_old_exchange_rates(self, days: int = 30) -> bool:
        """清理旧汇率记录

        Args:
            days: 保留天数，默认30天

        Returns:
            是否清理成功
        """
        if not self._conn:
            raise RuntimeError("数据库未初始化")

        try:
            cutoff_time = int(time.time()) - (days * 24 * 60 * 60)
            await self._conn.execute(
                """
                DELETE FROM exchange_rate_history
                WHERE recorded_at < ?
                """,
                (cutoff_time,),
            )
            await self._conn.commit()
            return True
        except Exception as e:
            logger.error(f"[{self.plugin_name}] 清理旧汇率记录失败: {e}")
            return False
