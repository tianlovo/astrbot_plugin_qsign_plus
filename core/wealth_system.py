"""
财富系统模块

提供财富等级、身价计算等功能。
"""

from astrbot.api import logger


# 财富等级配置
WEALTH_LEVELS = [
    (0, "平民", 0.25),
    (500, "小资", 0.5),
    (2000, "富豪", 0.75),
    (5000, "巨擘", 1.0),
]

WEALTH_BASE_VALUES = {
    "平民": 100.0,
    "小资": 500.0,
    "富豪": 2000.0,
    "巨擘": 5000.0,
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

    def get_wealth_info(self, user_data: dict) -> tuple:
        """获取财富等级信息

        Args:
            user_data: 用户数据

        Returns:
            (等级名称, 等级加成率) 元组
        """
        total = user_data.get("coins", 0.0) + user_data.get("bank", 0.0)
        for min_coin, name, rate in reversed(WEALTH_LEVELS):
            if total >= min_coin:
                return name, rate
        return "平民", 0.25

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
        total = user_data.get("coins", 0.0) + user_data.get("bank", 0.0)
        base_value = WEALTH_BASE_VALUES["平民"]
        for min_coin, name, _ in reversed(WEALTH_LEVELS):
            if total >= min_coin:
                base_value = WEALTH_BASE_VALUES[name]
                break
        contract_level = self.data_manager.get_purchase_count(user_id)
        price_bonus = self.config.get("contract_level_price_bonus", 0.15)
        return base_value * (1 + contract_level * price_bonus)

    async def get_total_contractor_rate(
        self, group_id: str, contractor_ids: list
    ) -> float:
        """计算雇员总加成率

        Args:
            group_id: 群ID
            contractor_ids: 雇员ID列表

        Returns:
            总加成率
        """
        total_rate = 0.0
        rate_bonus = self.config.get("contract_level_rate_bonus", 0.075)
        for contractor_id in contractor_ids:
            contractor_data = await self.data_manager.get_user_data(
                group_id, contractor_id
            )
            _, base_rate = self.get_wealth_info(contractor_data)
            contract_level = self.data_manager.get_purchase_count(contractor_id)
            total_rate += base_rate + (contract_level * rate_bonus)
        return total_rate

    async def calculate_sign_income(
        self,
        user_data: dict,
        group_id: str,
        is_penalized: bool = False,
    ) -> tuple:
        """计算签到收益

        Args:
            user_data: 用户数据
            group_id: 群ID
            is_penalized: 是否受雇（收益减少）

        Returns:
            (最终收益, 原始收益, 基础收益, 雇员加成, 连续签到加成, 银行利息)
        """
        _, user_base_rate = self.get_wealth_info(user_data)
        contractor_dynamic_rates = await self.get_total_contractor_rate(
            group_id, user_data["contractors"]
        )

        consecutive_bonus = 10 * (user_data["consecutive"] - 1)
        base_with_bonus = BASE_INCOME * (1 + user_base_rate)
        contract_bonus = base_with_bonus * contractor_dynamic_rates

        earned = base_with_bonus + contract_bonus + consecutive_bonus
        original_earned = earned

        if is_penalized:
            income_rate = self.config.get("employed_income_rate", 0.7)
            earned *= income_rate

        interest = user_data["bank"] * 0.01

        return (
            earned + interest,
            original_earned,
            base_with_bonus,
            contract_bonus,
            consecutive_bonus,
            interest,
        )

    async def calculate_tomorrow_income(
        self, user_data: dict, group_id: str
    ) -> dict:
        """计算明日预计收入

        Args:
            user_data: 用户数据
            group_id: 群ID

        Returns:
            收入明细字典
        """
        _, user_base_rate = self.get_wealth_info(user_data)
        base_with_bonus = BASE_INCOME * (1 + user_base_rate)
        contractor_dynamic_rates = await self.get_total_contractor_rate(
            group_id, user_data["contractors"]
        )
        contract_bonus = base_with_bonus * contractor_dynamic_rates
        consecutive_bonus = 10 * user_data["consecutive"]
        tomorrow_interest = user_data["bank"] * 0.01

        return {
            "total": base_with_bonus + contract_bonus + consecutive_bonus + tomorrow_interest,
            "base": base_with_bonus,
            "contract_bonus": contract_bonus,
            "consecutive_bonus": consecutive_bonus,
            "interest": tomorrow_interest,
        }
