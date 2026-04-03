"""
汇率管理模块

提供群主货币汇率的计算、历史记录管理等功能。
使用几何布朗运动 (Geometric Brownian Motion) 和均值回归 (Mean Reversion) 算法模拟汇率波动。
"""

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import QsignDatabase


@dataclass
class ExchangeRateRecord:
    """汇率记录数据类"""

    group_id: str
    rate: float
    recorded_at: int


class ExchangeRateCalculator:
    """汇率计算器

    使用几何布朗运动 (Geometric Brownian Motion) 和均值回归 (Mean Reversion)
    算法模拟汇率的随机波动。

    均值回归模型公式:
        dS = θ(μ - S)dt + σS dW

    其中:
        - dS: 汇率变化量
        - θ (theta): 均值回归速度 (mean_reversion_speed)
        - μ (mu): 均值回归水平 (mean_reversion_level)
        - S: 当前汇率
        - dt: 时间步长
        - σ (sigma): 波动率 (volatility)
        - dW: 维纳过程增量 (随机项)
    """

    def __init__(
        self,
        volatility: float = 0.1,
        mean_reversion_speed: float = 0.05,
        mean_reversion_level: float = 1.0,
    ):
        """初始化汇率计算器

        Args:
            volatility: 波动率 (σ)，控制随机波动的幅度，默认 0.1 (10%)
            mean_reversion_speed: 均值回归速度 (θ)，控制回归均值的快慢，默认 0.05
            mean_reversion_level: 均值回归水平 (μ)，长期均衡汇率，默认 1.0
        """
        self.volatility = volatility
        self.mean_reversion_speed = mean_reversion_speed
        self.mean_reversion_level = mean_reversion_level

    def calculate_next_rate(self, current_rate: float, dt: float = 1.0) -> float:
        """计算下一期汇率

        使用均值回归随机微分方程 (Ornstein-Uhlenbeck 过程变体):
            dS = θ(μ - S)dt + σS dW

        Args:
            current_rate: 当前汇率
            dt: 时间步长，默认为 1.0 (一天)

        Returns:
            下一期汇率
        """
        if current_rate <= 0:
            current_rate = self.mean_reversion_level

        # 维纳过程增量: dW = N(0, 1) * sqrt(dt)
        dW = random.gauss(0, 1) * math.sqrt(dt)

        # 均值回归项: θ(μ - S)dt
        mean_reversion_term = (
            self.mean_reversion_speed * (self.mean_reversion_level - current_rate) * dt
        )

        # 随机波动项: σS dW
        diffusion_term = self.volatility * current_rate * dW

        # 汇率变化量
        dS = mean_reversion_term + diffusion_term

        # 计算新汇率，确保为正
        next_rate = max(0.01, current_rate + dS)

        return next_rate

    def calculate_buy_cost(self, amount: float, rate: float) -> float:
        """计算购买群主货币所需货币

        Args:
            amount: 要购买的群主货币数量
            rate: 当前汇率

        Returns:
            所需支付的货币数量
        """
        return amount * rate

    def calculate_sell_revenue(self, amount: float, rate: float) -> float:
        """计算出售群主货币获得货币

        Args:
            amount: 要出售的群主货币数量
            rate: 当前汇率

        Returns:
            获得的货币数量
        """
        return amount * rate


class ExchangeRateHistory:
    """汇率历史管理器

    管理汇率历史记录的存储、查询和清理。
    支持按群组隔离存储汇率数据。
    所有数据库操作通过 QsignDatabase 进行。
    """

    def __init__(self, db: "QsignDatabase"):
        """初始化汇率历史管理器

        Args:
            db: QsignDatabase 数据库实例
        """
        self._db = db

    async def record_rate(self, group_id: str, rate: float) -> bool:
        """记录当前汇率

        Args:
            group_id: 群ID
            rate: 当前汇率

        Returns:
            是否记录成功
        """
        return await self._db.record_exchange_rate(group_id, rate)

    async def get_recent_rates(
        self, group_id: str, days: int = 7
    ) -> list[ExchangeRateRecord]:
        """获取近N天汇率历史

        Args:
            group_id: 群ID
            days: 查询天数，默认 7 天

        Returns:
            汇率记录列表，按时间升序排列
        """
        records = await self._db.get_exchange_rate_history(group_id, days)
        return [
            ExchangeRateRecord(
                group_id=group_id,
                rate=record["rate"],
                recorded_at=record["recorded_at"],
            )
            for record in records
        ]

    async def get_current_rate(self, group_id: str) -> float | None:
        """获取当前汇率

        Args:
            group_id: 群ID

        Returns:
            当前汇率，如果没有记录则返回 None
        """
        return await self._db.get_current_exchange_rate(group_id)

    async def cleanup_old_records(self, days: int = 30) -> int:
        """清理旧记录

        Args:
            days: 保留天数，默认 30 天

        Returns:
            清理的记录数量
        """
        return await self._db.cleanup_old_exchange_rates(days)
