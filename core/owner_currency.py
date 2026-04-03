"""
群主货币管理模块

管理用户的群主货币余额和交易。
"""

from astrbot.api import logger

from .exchange_rate import ExchangeRateCalculator


class InsufficientFundsError(Exception):
    """余额不足异常"""

    def __init__(self, message: str = "余额不足"):
        self.message = message
        super().__init__(self.message)


class InvalidAmountError(Exception):
    """无效金额异常"""

    def __init__(self, message: str = "无效金额"):
        self.message = message
        super().__init__(self.message)


class OwnerCurrencyManager:
    """群主货币管理器"""

    def __init__(self, data_manager, calculator: ExchangeRateCalculator):
        """初始化

        Args:
            data_manager: 数据管理器实例
            calculator: 汇率计算器实例
        """
        self.data_manager = data_manager
        self.calculator = calculator

    async def buy_currency(
        self, group_id: str, user_id: str, amount: float, rate: float
    ) -> tuple[bool, str, float]:
        """购买群主货币

        Args:
            group_id: 群ID
            user_id: 用户ID
            amount: 购买数量
            rate: 当前汇率

        Returns:
            (是否成功, 消息, 实际购买数量)
        """
        # 限制精度为一位小数
        amount = round(amount, 1)
        if amount <= 0:
            raise InvalidAmountError("购买数量必须大于0")

        # 计算所需货币成本
        cost = self.calculator.calculate_buy_cost(amount, rate)

        # 获取用户数据
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        # 检查余额（使用整数比较避免浮点精度问题）
        cost_int = int(cost * 10)
        coins_int = int(user_data["coins"] * 10)
        if cost_int > coins_int:
            return (
                False,
                f"现金不足，需要 {cost:.1f}，当前现金：{user_data['coins']:.1f}",
                0.0,
            )

        # 扣除用户货币
        user_data["coins"] -= cost
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        # 增加群主货币余额
        await self.data_manager.add_owner_currency_balance(group_id, user_id, amount)

        logger.info(
            f"[群主货币购买] 群 {group_id} 用户 {user_id}: 购买 {amount}, 花费 {cost}"
        )
        return True, f"成功购买 {amount:.1f} 群主货币，花费 {cost:.1f}", amount

    async def sell_currency(
        self, group_id: str, user_id: str, amount: float, rate: float
    ) -> tuple[bool, str, float]:
        """出售群主货币

        Args:
            group_id: 群ID
            user_id: 用户ID
            amount: 出售数量
            rate: 当前汇率

        Returns:
            (是否成功, 消息, 实际获得金额)
        """
        # 限制精度为一位小数
        amount = round(amount, 1)
        if amount <= 0:
            raise InvalidAmountError("出售数量必须大于0")

        # 获取当前群主货币余额
        balance = await self.get_balance(group_id, user_id)

        # 验证用户有足够群主货币余额（使用整数比较避免浮点精度问题）
        amount_int = int(amount * 10)
        balance_int = int(balance * 10)
        if amount_int > balance_int:
            return (
                False,
                f"群主货币不足，当前余额：{balance:.1f}",
                0.0,
            )

        # 计算出售获得金额
        revenue = self.calculator.calculate_sell_return(amount, rate)

        # 扣除群主货币余额
        await self.data_manager.add_owner_currency_balance(group_id, user_id, -amount)

        # 增加用户货币
        user_data = await self.data_manager.get_user_data(group_id, user_id)
        user_data["coins"] += revenue
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        logger.info(
            f"[群主货币出售] 群 {group_id} 用户 {user_id}: 出售 {amount}, 获得 {revenue}"
        )
        return True, f"成功出售 {amount:.1f} 群主货币，获得 {revenue:.1f}", revenue

    async def get_balance(self, group_id: str, user_id: str) -> float:
        """获取用户群主货币余额

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            群主货币余额
        """
        return await self.data_manager.get_owner_currency_balance(group_id, user_id)

    @staticmethod
    def format_currency_name(owner_nickname: str) -> str:
        """格式化货币名称

        Args:
            owner_nickname: 群主昵称

        Returns:
            格式化后的货币名称，如 "{owner_nickname}币"
        """
        return f"{owner_nickname}币"
