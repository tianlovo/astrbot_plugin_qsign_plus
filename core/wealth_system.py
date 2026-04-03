"""
财富系统模块

提供财富等级、身价计算等功能。
"""

from astrbot.api import logger

from .wealth_calculator import WealthCalculator


# 财富等级配置 (10个阶段)
WEALTH_LEVELS = [
    (0, "平民", 0.25),
    (500, "小资", 0.5),
    (2000, "富豪", 0.75),
    (5000, "巨擘", 1.0),
    (15000, "权贵", 1.25),
    (50000, "领主", 1.5),
    (150000, "霸主", 1.75),
    (500000, "王者", 2.0),
    (1500000, "传奇", 2.5),
    (5000000, "神话", 3.0),
]

# 每个财富阶段的可雇佣数量限制
WEALTH_CONTRACTOR_LIMITS = {
    "平民": 3,
    "小资": 4,
    "富豪": 5,
    "巨擘": 6,
    "权贵": 7,
    "领主": 8,
    "霸主": 9,
    "王者": 10,
    "传奇": 15,
    "神话": -1,  # -1 表示无限制
}

WEALTH_BASE_VALUES = {
    "平民": 100.0,
    "小资": 500.0,
    "富豪": 2000.0,
    "巨擘": 5000.0,
    "权贵": 15000.0,
    "领主": 50000.0,
    "霸主": 150000.0,
    "王者": 500000.0,
    "传奇": 1500000.0,
    "神话": 5000000.0,
}

BASE_INCOME = 100.0


class WealthSystem:
    """财富系统

    管理财富等级、身价计算等功能。
    """

    def __init__(self, data_manager, config: dict):
        """初始化财富系统

        Args:
            data_manager: 数据管理器实例
            config: 配置字典
        """
        self.data_manager = data_manager
        self.config = config
        self.calculator = WealthCalculator(data_manager, config)

    def get_wealth_info(self, user_data: dict) -> tuple:
        """获取财富等级信息

        Args:
            user_data: 用户数据

        Returns:
            (等级名称, 等级加成率) 元组
        """
        return self.calculator.get_wealth_level(user_data)

    async def calculate_wealth_value(
        self, group_id: str, user_data: dict, user_id: str
    ) -> float:
        """计算身价（包含雇员潜在价值）

        身价 = 现金 + 银行存款 + Σ(每个雇员的潜在价值)

        雇员的潜在价值 = 出售该雇员时能获得的钱 或 雇员赎身时雇主能获得的钱

        Args:
            group_id: 群ID
            user_data: 用户数据
            user_id: 用户ID

        Returns:
            身价数值
        """
        return await self.calculator.calculate_wealth_value(group_id, user_data, user_id)

    async def calculate_dynamic_wealth_value(
        self, group_id: str, user_data: dict, user_id: str
    ) -> float:
        """计算动态身价

        Args:
            group_id: 群ID
            user_data: 用户数据
            user_id: 用户ID

        Returns:
            身价数值
        """
        return await self.calculator.calculate_dynamic_wealth_value(group_id, user_data, user_id)

    def get_max_contractor_limit(self, user_data: dict) -> int:
        """获取用户最大可雇佣数量

        Args:
            user_data: 用户数据

        Returns:
            最大可雇佣数量，-1表示无限制
        """
        return self.calculator.get_max_contractor_limit(user_data)

    async def get_total_contractor_rate(
        self, group_id: str, contractor_ids: list, admin_ids: list = None
    ) -> float:
        """计算雇员总加成率

        Args:
            group_id: 群ID
            contractor_ids: 雇员ID列表
            admin_ids: 群管理员ID列表，用于计算管理员额外加成

        Returns:
            总加成率
        """
        return await self.calculator.get_total_contractor_rate(group_id, contractor_ids, admin_ids)

    async def calculate_sign_income(
        self,
        user_data: dict,
        group_id: str,
        is_penalized: bool = False,
        admin_ids: list = None,
    ) -> tuple:
        """计算签到收益

        Args:
            user_data: 用户数据
            group_id: 群ID
            is_penalized: 是否受雇（收益减少）
            admin_ids: 群管理员ID列表

        Returns:
            (最终收益, 原始收益, 基础收益, 雇员加成, 连续签到加成, 银行利息)
        """
        return await self.calculator.calculate_sign_income(
            user_data, group_id, is_penalized, admin_ids
        )

    async def calculate_tomorrow_income(
        self, user_data: dict, group_id: str, admin_ids: list = None
    ) -> dict:
        """计算明日预计收入

        Args:
            user_data: 用户数据
            group_id: 群ID
            admin_ids: 群管理员ID列表

        Returns:
            收入明细字典
        """
        return await self.calculator.calculate_tomorrow_income(user_data, group_id, admin_ids)
