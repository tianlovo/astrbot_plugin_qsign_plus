"""
身价计算模块

提供统一的身价计算功能，所有涉及身价的计算都通过此模块进行，
确保实时计算和代码复用性。
"""

from astrbot.api import logger

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


class WealthCalculator:
    """身价计算器

    集中所有身价计算逻辑，提供统一的实时计算接口。
    """

    def __init__(self, data_manager, config: dict):
        """初始化身价计算器

        Args:
            data_manager: 数据管理器实例
            config: 配置字典
        """
        self.data_manager = data_manager
        self.config = config

    def get_wealth_level(self, user_data: dict) -> tuple:
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

    def get_max_contractor_limit(self, user_data: dict) -> int:
        """获取用户最大可雇佣数量

        Args:
            user_data: 用户数据

        Returns:
            最大可雇佣数量，-1表示无限制
        """
        wealth_name, _ = self.get_wealth_level(user_data)
        return WEALTH_CONTRACTOR_LIMITS.get(wealth_name, 3)

    async def calculate_wealth_value(
        self, group_id: str, user_data: dict, user_id: str
    ) -> float:
        """计算身价（包含雇员潜在价值）

        身价 = 现金 + 银行存款 + Σ(每个雇员的潜在价值)

        Args:
            group_id: 群ID
            user_data: 用户数据
            user_id: 用户ID

        Returns:
            身价数值
        """
        # 基础身价：现金 + 银行存款
        total = user_data.get("coins", 0.0) + user_data.get("bank", 0.0)

        # 加上所有雇员的潜在价值
        contractors = user_data.get("contractors", [])
        trade_config = self.config.get("trade", {})
        sell_return_rate = trade_config.get("sell_return_rate", 0.8)
        redeem_return_rate = trade_config.get("redeem_return_rate", 0.5)

        for contractor_id in contractors:
            contractor_data = await self.data_manager.get_user_data(
                group_id, contractor_id
            )
            # 计算雇员当前身价（购买价格）
            contractor_value = await self.calculate_dynamic_wealth_value(
                group_id, contractor_data, contractor_id
            )

            # 雇员潜在价值 = max(出售获得的钱, 赎身时雇主获得的钱)
            sell_value = contractor_value * sell_return_rate
            
            # 赎身费用 = 购买记录中的价格
            redeem_cost = await self.data_manager.get_latest_purchase_price(
                group_id, contractor_id
            )
            if redeem_cost <= 0:
                # 如果没有购买记录，使用当前身价（兼容旧数据）
                redeem_cost = contractor_value
            redeem_value = redeem_cost * redeem_return_rate

            # 取两者中的较大值作为潜在价值
            contractor_potential_value = max(sell_value, redeem_value)
            total += contractor_potential_value

        return total

    async def calculate_dynamic_wealth_value(
        self, group_id: str, user_data: dict, user_id: str
    ) -> float:
        """计算动态身价（用于购买价格计算）

        动态身价 = 基础身价 × (1 + 契约等级 × 每级契约身价加成)

        Args:
            group_id: 群ID
            user_data: 用户数据
            user_id: 用户ID

        Returns:
            动态身价数值
        """
        # 使用身价计算（包含雇员潜在价值）
        total = await self.calculate_wealth_value(group_id, user_data, user_id)
        
        # 根据身价确定基础身价
        base_value = WEALTH_BASE_VALUES["平民"]
        for min_coin, name, _ in reversed(WEALTH_LEVELS):
            if total >= min_coin:
                base_value = WEALTH_BASE_VALUES[name]
                break
        
        # 获取契约等级（被购买次数）
        contract_level = await self.data_manager.get_purchase_count(user_id)
        contract_config = self.config.get("contract", {})
        price_bonus = contract_config.get("contract_level_price_bonus", 0.15)
        
        return base_value * (1 + contract_level * price_bonus)

    async def calculate_purchase_price(
        self,
        group_id: str,
        target_data: dict,
        target_id: str,
        target_role: str = "member",
    ) -> float:
        """计算购买价格

        计算逻辑：
        1. 计算动态身价
        2. 应用最低价格限制
        3. 应用管理员/群主加成

        Args:
            group_id: 群ID
            target_data: 目标用户数据
            target_id: 目标用户ID
            target_role: 目标用户角色 (owner/admin/member)

        Returns:
            购买价格
        """
        # 计算动态身价
        base_cost = await self.calculate_dynamic_wealth_value(
            group_id, target_data, target_id
        )

        # 确保不低于最低购买价格
        trade_config = self.config.get("trade", {})
        min_purchase_price = trade_config.get("min_purchase_price", 100)
        base_cost = max(base_cost, min_purchase_price)

        # 管理员和群主享受价格加成
        if target_role in ["owner", "admin"]:
            admin_config = self.config.get("admin", {})
            admin_bonus = admin_config.get("admin_price_bonus", 0.5)
            base_cost *= 1 + admin_bonus

        return base_cost

    async def calculate_contractor_potential_value(
        self, group_id: str, contractor_id: str
    ) -> float:
        """计算雇员潜在价值

        雇员潜在价值 = max(出售获得的钱, 赎身时雇主获得的钱)

        Args:
            group_id: 群ID
            contractor_id: 雇员ID

        Returns:
            雇员潜在价值
        """
        contractor_data = await self.data_manager.get_user_data(
            group_id, contractor_id
        )
        
        # 计算雇员当前身价
        contractor_value = await self.calculate_dynamic_wealth_value(
            group_id, contractor_data, contractor_id
        )

        trade_config = self.config.get("trade", {})
        sell_return_rate = trade_config.get("sell_return_rate", 0.8)
        redeem_return_rate = trade_config.get("redeem_return_rate", 0.5)

        # 出售获得的钱
        sell_value = contractor_value * sell_return_rate
        
        # 赎身时雇主获得的钱
        redeem_cost = await self.data_manager.get_latest_purchase_price(
            group_id, contractor_id
        )
        if redeem_cost <= 0:
            redeem_cost = contractor_value
        redeem_value = redeem_cost * redeem_return_rate

        return max(sell_value, redeem_value)

    async def get_total_contractor_rate(
        self, group_id: str, contractor_ids: list, admin_ids: list = None
    ) -> float:
        """计算雇员总加成率

        Args:
            group_id: 群ID
            contractor_ids: 雇员ID列表
            admin_ids: 群管理员ID列表

        Returns:
            总加成率
        """
        total_rate = 0.0
        contract_config = self.config.get("contract", {})
        rate_bonus = contract_config.get("contract_level_rate_bonus", 0.075)
        admin_bonus = contract_config.get("admin_contractor_bonus", 0.1)
        wealth_value_rate = contract_config.get("wealth_value_bonus_rate", 0.001)

        admin_ids = admin_ids or []

        for contractor_id in contractor_ids:
            contractor_data = await self.data_manager.get_user_data(
                group_id, contractor_id
            )
            _, base_rate = self.get_wealth_level(contractor_data)
            contract_level = await self.data_manager.get_purchase_count(contractor_id)

            # 基础加成 = 财富等级加成 + 雇佣次数加成
            contractor_rate = base_rate + (contract_level * rate_bonus)

            # 管理员额外加成
            if contractor_id in admin_ids:
                contractor_rate += admin_bonus

            # 身价加成 = 雇员身价 / 1000 * 身价系数
            contractor_wealth = await self.calculate_wealth_value(
                group_id, contractor_data, contractor_id
            )
            wealth_bonus = contractor_wealth / 1000 * wealth_value_rate
            contractor_rate += wealth_bonus

            total_rate += contractor_rate

        return total_rate

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
        _, user_base_rate = self.get_wealth_level(user_data)
        contractor_dynamic_rates = await self.get_total_contractor_rate(
            group_id, user_data.get("contractors", []), admin_ids
        )

        consecutive_bonus = 10 * (user_data.get("consecutive", 1) - 1)
        base_with_bonus = BASE_INCOME * (1 + user_base_rate)
        contract_bonus = base_with_bonus * contractor_dynamic_rates

        earned = base_with_bonus + contract_bonus + consecutive_bonus
        original_earned = earned

        if is_penalized:
            contract_config = self.config.get("contract", {})
            income_rate = contract_config.get("employed_income_rate", 0.7)
            earned *= income_rate

        interest = user_data.get("bank", 0.0) * 0.01

        return (
            earned + interest,
            original_earned,
            base_with_bonus,
            contract_bonus,
            consecutive_bonus,
            interest,
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
        _, user_base_rate = self.get_wealth_level(user_data)
        base_with_bonus = BASE_INCOME * (1 + user_base_rate)
        contractor_dynamic_rates = await self.get_total_contractor_rate(
            group_id, user_data.get("contractors", []), admin_ids
        )
        contract_bonus = base_with_bonus * contractor_dynamic_rates
        consecutive_bonus = 10 * user_data.get("consecutive", 1)
        tomorrow_interest = user_data.get("bank", 0.0) * 0.01

        return {
            "total": base_with_bonus
            + contract_bonus
            + consecutive_bonus
            + tomorrow_interest,
            "base": base_with_bonus,
            "contract_bonus": contract_bonus,
            "consecutive_bonus": consecutive_bonus,
            "interest": tomorrow_interest,
        }
