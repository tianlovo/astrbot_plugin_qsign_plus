"""
汇率更新后台服务模块

提供汇率定时更新的后台服务，支持启动、停止和自动汇率计算。
使用几何布朗运动和均值回归算法模拟汇率波动。
"""

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ..core.data_manager import DataManager
    from ..core.exchange_rate import ExchangeRateCalculator, ExchangeRateHistory


class ExchangeRateService:
    """汇率更新后台服务

    管理汇率的定时更新，包括：
    - 按配置间隔自动计算新汇率
    - 为每个启用的群组更新汇率
    - 定期清理旧汇率记录
    - 优雅启动和停止
    """

    def __init__(
        self,
        data_manager: "DataManager",
        exchange_calculator: "ExchangeRateCalculator",
        exchange_history: "ExchangeRateHistory",
        config: dict,
    ):
        """初始化汇率更新服务

        Args:
            data_manager: 数据管理器实例
            exchange_calculator: 汇率计算器实例
            exchange_history: 汇率历史管理器实例
            config: 插件配置字典
        """
        self._data_manager = data_manager
        self._exchange_calculator = exchange_calculator
        self._exchange_history = exchange_history
        self._config = config

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """启动汇率更新后台服务"""
        if self._task and not self._task.done():
            logger.warning("[汇率服务] 服务已在运行中")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_service())
        logger.info("[汇率服务] 后台服务已启动")

    async def stop(self) -> None:
        """停止汇率更新后台服务"""
        if not self._task or self._task.done():
            logger.info("[汇率服务] 服务已停止或未启动")
            return

        self._stop_event.set()
        self._task.cancel()

        try:
            await self._task
        except asyncio.CancelledError:
            pass

        logger.info("[汇率服务] 后台服务已停止")

    async def _run_service(self) -> None:
        """运行汇率更新服务的主循环"""
        # 等待数据库初始化完成
        while not self._data_manager.is_db_initialized():
            if self._stop_event.is_set():
                return
            await asyncio.sleep(1)

        logger.info("[汇率服务] 汇率更新循环已启动")

        while not self._stop_event.is_set():
            try:
                stock_config = self._config.get("stock_market", {})
                interval = stock_config.get("update_interval_minutes", 60)

                # 等待更新间隔或停止信号
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=interval * 60,
                    )
                    # 如果收到停止信号，退出循环
                    if self._stop_event.is_set():
                        break
                except asyncio.TimeoutError:
                    # 正常超时，继续执行更新
                    pass

                # 执行汇率更新
                await self._update_exchange_rates()

                # 定期清理旧记录
                await self._cleanup_old_records()

            except asyncio.CancelledError:
                logger.info("[汇率服务] 服务已取消")
                break
            except Exception as e:
                logger.error(f"[汇率服务] 服务运行出错: {e}")
                # 出错后等待一段时间再重试
                await asyncio.sleep(60)

    async def _update_exchange_rates(self) -> None:
        """更新所有启用群组的汇率"""
        stock_config = self._config.get("stock_market", {})
        basic_config = self._config.get("basic", {})
        enabled_groups = basic_config.get("enabled_groups", [])

        if not enabled_groups:
            return

        for group_id in enabled_groups:
            try:
                await self._update_group_rate(group_id, stock_config)
            except Exception as e:
                logger.error(f"[汇率服务] 更新群 {group_id} 汇率失败: {e}")

    async def _update_group_rate(self, group_id: str, stock_config: dict) -> None:
        """更新单个群组的汇率

        Args:
            group_id: 群ID
            stock_config: 股市配置
        """
        current_rate = await self._exchange_history.get_current_rate(group_id)

        if current_rate is None:
            current_rate = stock_config.get("base_exchange_rate", 1.0)
            # 初始化汇率记录
            await self._exchange_history.record_rate(group_id, current_rate)

        next_rate = self._exchange_calculator.calculate_next_rate(current_rate)
        await self._exchange_history.record_rate(group_id, next_rate)

        logger.info(
            f"[汇率服务] 群 {group_id} 汇率已更新: {current_rate:.4f} -> {next_rate:.4f}"
        )

    async def _cleanup_old_records(self) -> None:
        """清理旧的汇率记录"""
        try:
            await self._exchange_history.cleanup_old_records(days=30)
        except Exception as e:
            logger.warning(f"[汇率服务] 清理旧汇率记录失败: {e}")

    def is_running(self) -> bool:
        """检查服务是否正在运行

        Returns:
            是否正在运行
        """
        return self._task is not None and not self._task.done()
