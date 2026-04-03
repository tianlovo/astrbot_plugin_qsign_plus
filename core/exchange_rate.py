"""
汇率管理模块

提供群主货币汇率的计算、历史记录管理等功能。
使用几何布朗运动 (Geometric Brownian Motion) 和均值回归 (Mean Reversion) 算法模拟汇率波动。
"""

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astrbot.api import logger

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
        trend_mode: str = "off",
        trend_direction: int = 0,
        trend_bull_probability: float = 30.0,
        trend_bear_probability: float = 30.0,
        trend_range_probability: float = 40.0,
        trend_min_days: int = 3,
        trend_max_days: int = 10,
        trend_min_strength: float = 0.01,
        trend_max_strength: float = 0.05,
    ):
        """初始化汇率计算器

        Args:
            volatility: 波动率 (σ)，控制随机波动的幅度，默认 0.1 (10%)
            mean_reversion_speed: 均值回归速度 (θ)，控制回归均值的快慢，默认 0.05
            mean_reversion_level: 均值回归水平 (μ)，长期均衡汇率，默认 1.0
            trend_mode: 趋势模式 ("random"/"fixed"/"off")，默认 "off"
            trend_direction: 固定趋势方向 (1=上涨, -1=下跌, 0=震荡)，默认 0
            trend_bull_probability: 牛市概率，默认 30
            trend_bear_probability: 熊市概率，默认 30
            trend_range_probability: 震荡概率，默认 40
            trend_min_days: 趋势最少持续天数，默认 3
            trend_max_days: 趋势最多持续天数，默认 10
            trend_min_strength: 最小趋势强度，默认 0.01
            trend_max_strength: 最大趋势强度，默认 0.05
        """
        self.volatility = volatility
        self.mean_reversion_speed = mean_reversion_speed
        self.mean_reversion_level = mean_reversion_level
        self.trend_mode = trend_mode
        self.trend_direction = trend_direction
        self.trend_bull_probability = trend_bull_probability
        self.trend_bear_probability = trend_bear_probability
        self.trend_range_probability = trend_range_probability
        self.trend_min_days = trend_min_days
        self.trend_max_days = trend_max_days
        self.trend_min_strength = trend_min_strength
        self.trend_max_strength = trend_max_strength

        self._trend_state: int = 0
        self._trend_strength: float = 0.0
        self._state_duration: int = 0

        self._update_trend_state()

    def _update_trend_state(self) -> None:
        """更新趋势状态

        根据 trend_mode 决定如何设置趋势:
        - "random": 根据概率随机选择趋势状态、持续天数和趋势强度
        - "fixed": 使用配置的 trend_direction，持续天数设为无限大
        - "off": 趋势状态为 0，无趋势影响
        """
        if self.trend_mode == "off":
            self._trend_state = 0
            self._trend_strength = 0.0
            self._state_duration = 0
            return

        if self.trend_mode == "fixed":
            self._trend_state = max(-1, min(1, self.trend_direction))
            self._trend_strength = self.trend_max_strength
            self._state_duration = float("inf")
            return

        if self.trend_mode == "random":
            total_prob = (
                self.trend_bull_probability
                + self.trend_bear_probability
                + self.trend_range_probability
            )
            if total_prob <= 0:
                self._trend_state = 0
                self._trend_strength = 0.0
                self._state_duration = 0
                return

            rand_val = random.uniform(0, total_prob)

            if rand_val < self.trend_bull_probability:
                self._trend_state = 1
            elif rand_val < self.trend_bull_probability + self.trend_bear_probability:
                self._trend_state = -1
            else:
                self._trend_state = 0

            self._state_duration = random.randint(
                self.trend_min_days, self.trend_max_days
            )
            self._trend_strength = random.uniform(
                self.trend_min_strength, self.trend_max_strength
            )

    def calculate_next_rate(self, current_rate: float, dt: float = 1.0) -> float:
        """计算下一期汇率

        使用均值回归随机微分方程 (Ornstein-Uhlenbeck 过程变体):
            dS = θ(μ - S)dt + σS dW + trend_term

        其中趋势项: trend_term = trend_state * trend_strength * current_rate * dt

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

        # 趋势项: trend_state * trend_strength * current_rate * dt
        trend_term = self._trend_state * self._trend_strength * current_rate * dt

        # 汇率变化量
        dS = mean_reversion_term + diffusion_term + trend_term

        # 计算新汇率，确保为正
        next_rate = max(0.01, current_rate + dS)

        # 输出详细的计算日志
        self._log_calculation_details(
            current_rate,
            next_rate,
            dS,
            mean_reversion_term,
            diffusion_term,
            trend_term,
            dW,
        )

        # 更新趋势状态持续时间
        if self.trend_mode != "off" and self._state_duration > 0:
            self._state_duration -= 1
            if self._state_duration <= 0:
                self._update_trend_state()

        return next_rate

    def _log_calculation_details(
        self,
        current_rate: float,
        next_rate: float,
        dS: float,
        mean_reversion_term: float,
        diffusion_term: float,
        trend_term: float,
        dW: float,
    ) -> None:
        """输出汇率计算详细日志

        Args:
            current_rate: 当前汇率
            next_rate: 下一期汇率
            dS: 汇率总变化量
            mean_reversion_term: 均值回归项
            diffusion_term: 随机波动项
            trend_term: 趋势项
            dW: 维纳过程增量
        """
        # 趋势状态名称
        trend_names = {1: "牛市", -1: "熊市", 0: "震荡"}
        trend_name = trend_names.get(self._trend_state, "未知")

        # 计算各项占比
        total_change = abs(mean_reversion_term) + abs(diffusion_term) + abs(trend_term)
        if total_change > 0:
            mr_ratio = abs(mean_reversion_term) / total_change * 100
            diff_ratio = abs(diffusion_term) / total_change * 100
            trend_ratio = abs(trend_term) / total_change * 100
        else:
            mr_ratio = diff_ratio = trend_ratio = 0

        logger.info(
            f"[汇率计算] 当前: {current_rate:.4f} -> 新汇率: {next_rate:.4f} (变化: {dS:+.4f}) | "
            f"趋势: {trend_name}(强度:{self._trend_strength:.2%},剩余:{self._state_duration}天) | "
            f"均值回归: {mean_reversion_term:+.4f}({mr_ratio:.1f}%) | "
            f"随机波动: {diffusion_term:+.4f}({diff_ratio:.1f}%,dW={dW:.3f}) | "
            f"趋势项: {trend_term:+.4f}({trend_ratio:.1f}%)"
        )

        # 如果启用了趋势模式，输出概率配置
        if self.trend_mode == "random":
            total_prob = (
                self.trend_bull_probability
                + self.trend_bear_probability
                + self.trend_range_probability
            )
            if total_prob > 0:
                logger.info(
                    f"[汇率计算] 趋势概率配置: 牛市{self.trend_bull_probability / total_prob * 100:.1f}% | "
                    f"熊市{self.trend_bear_probability / total_prob * 100:.1f}% | "
                    f"震荡{self.trend_range_probability / total_prob * 100:.1f}%"
                )

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
            汇率记录列表，按时间升序排列（旧的在前面，新的在后面）
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
