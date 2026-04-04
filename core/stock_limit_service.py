"""
股市限制服务模块

提供股市命令使用次数限制功能，基于用户财富等级动态调整次数上限。
"""

from datetime import datetime

import pytz

from astrbot.api import logger

# 财富等级顺序列表，用于确定等级索引
WEALTH_LEVEL_ORDER = [
    "平民",
    "小资",
    "富豪",
    "巨擘",
    "权贵",
    "领主",
    "霸主",
    "王者",
    "传奇",
    "神话",
]


class StockLimitService:
    """股市限制服务

    管理股市命令的使用次数限制，根据财富等级动态调整上限。
    """

    def __init__(self, data_manager, config: dict):
        """初始化股市限制服务

        Args:
            data_manager: 数据管理器实例
            config: 配置字典
        """
        self.data_manager = data_manager
        self.config = config
        self.shanghai_tz = pytz.timezone("Asia/Shanghai")

    def _get_today_date(self) -> str:
        """获取今日日期字符串

        Returns:
            日期字符串 (YYYY-MM-DD)
        """
        return datetime.now(self.shanghai_tz).strftime("%Y-%m-%d")

    def _get_wealth_level_index(self, wealth_level: str) -> int:
        """获取财富等级索引

        Args:
            wealth_level: 财富等级名称

        Returns:
            等级索引（平民=1, 小资=2, ...），未知等级返回1
        """
        try:
            return WEALTH_LEVEL_ORDER.index(wealth_level) + 1
        except ValueError:
            logger.warning(f"[股市限制] 未知的财富等级: {wealth_level}")
            return 1

    def _get_rate_limit_config(self) -> dict:
        """获取限制配置

        Returns:
            限制配置字典
        """
        stock_config = self.config.get("stock_market", {})
        return stock_config.get("rate_limit", {})

    def get_limit_by_wealth_level(self, wealth_level: str) -> tuple[int, int, int]:
        """根据财富等级获取次数上限

        计算公式：次数上限 = 基础次数 + (财富等级索引 - 1) × 等级加成

        Args:
            wealth_level: 财富等级名称

        Returns:
            (汇率查询次数, 购买次数, 出售次数) 元组
        """
        rate_limit_config = self._get_rate_limit_config()

        # 获取基础次数配置（默认值）
        base_exchange = rate_limit_config.get("base_exchange_query", 3)
        base_buy = rate_limit_config.get("base_buy", 2)
        base_sell = rate_limit_config.get("base_sell", 2)

        # 获取等级加成配置（默认每级加1次）
        level_bonus = rate_limit_config.get("wealth_level_bonus", 1)

        # 获取财富等级索引
        level_index = self._get_wealth_level_index(wealth_level)

        # 计算次数上限
        bonus = (level_index - 1) * level_bonus
        exchange_limit = base_exchange + bonus
        buy_limit = base_buy + bonus
        sell_limit = base_sell + bonus

        return (exchange_limit, buy_limit, sell_limit)

    async def check_limit(
        self, group_id: str, user_id: str, limit_type: str, wealth_level: str
    ) -> tuple[bool, int, int]:
        """检查是否达到使用次数限制

        Args:
            group_id: 群ID
            user_id: 用户ID
            limit_type: 限制类型 (exchange_query/buy/sell)
            wealth_level: 用户财富等级

        Returns:
            (是否允许使用, 已使用次数, 次数上限) 元组
        """
        today = self._get_today_date()

        # 获取当前限制数据
        limit_data = await self.data_manager.db.get_stock_limit(
            group_id, user_id, today
        )

        # 获取次数上限
        exchange_limit, buy_limit, sell_limit = self.get_limit_by_wealth_level(
            wealth_level
        )

        # 根据类型获取已使用次数和上限
        if limit_type == "exchange_query":
            used_count = limit_data["exchange_query_count"]
            max_count = exchange_limit
        elif limit_type == "buy":
            used_count = limit_data["buy_count"]
            max_count = buy_limit
        elif limit_type == "sell":
            used_count = limit_data["sell_count"]
            max_count = sell_limit
        else:
            logger.error(f"[股市限制] 无效的限制类型: {limit_type}")
            return False, 0, 0

        # 检查是否达到上限
        can_use = used_count < max_count

        return can_use, used_count, max_count

    async def increment_limit(
        self, group_id: str, user_id: str, limit_type: str
    ) -> bool:
        """增加使用次数

        Args:
            group_id: 群ID
            user_id: 用户ID
            limit_type: 限制类型 (exchange_query/buy/sell)

        Returns:
            是否成功
        """
        today = self._get_today_date()
        return await self.data_manager.db.increment_stock_limit(
            group_id, user_id, limit_type, today
        )

    async def get_remaining_limits(
        self, group_id: str, user_id: str, wealth_level: str
    ) -> dict[str, int]:
        """获取剩余次数信息

        Args:
            group_id: 群ID
            user_id: 用户ID
            wealth_level: 用户财富等级

        Returns:
            剩余次数字典
        """
        today = self._get_today_date()
        limit_data = await self.data_manager.db.get_stock_limit(
            group_id, user_id, today
        )

        exchange_limit, buy_limit, sell_limit = self.get_limit_by_wealth_level(
            wealth_level
        )

        return {
            "exchange_query": max(
                0, exchange_limit - limit_data["exchange_query_count"]
            ),
            "buy": max(0, buy_limit - limit_data["buy_count"]),
            "sell": max(0, sell_limit - limit_data["sell_count"]),
            "exchange_limit": exchange_limit,
            "buy_limit": buy_limit,
            "sell_limit": sell_limit,
        }

    def get_limit_reset_time(self) -> str:
        """获取下次限制重置时间

        Returns:
            重置时间字符串 (HH:MM)
        """
        return "00:00"

    def get_limit_type_name(self, limit_type: str) -> str:
        """获取限制类型的中文名称

        Args:
            limit_type: 限制类型

        Returns:
            中文名称
        """
        type_names = {
            "exchange_query": "汇率查询",
            "buy": "购买",
            "sell": "出售",
        }
        return type_names.get(limit_type, limit_type)

    def format_limit_message(self, remaining: dict[str, int]) -> str:
        """格式化限制提示消息

        Args:
            remaining: 剩余次数字典

        Returns:
            格式化后的消息
        """
        return (
            f"今日剩余：汇率查询{remaining['exchange_query']}次，"
            f"购买{remaining['buy']}次，出售{remaining['sell']}次"
        )
