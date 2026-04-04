"""
汇率历史清理后台服务模块

提供汇率历史记录的定时清理服务，自动删除过期的旧记录以节省存储空间。
"""

import asyncio
from typing import TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from ..core.data_manager import DataManager


class ExchangeRateCleanupService:
    """汇率历史清理后台服务

    管理汇率历史记录的定时清理，包括：
    - 按配置间隔自动清理旧记录
    - 可配置的保留天数
    - 优雅启动和停止
    """

    def __init__(
        self,
        data_manager: "DataManager",
        config: dict,
    ):
        """初始化汇率历史清理服务

        Args:
            data_manager: 数据管理器实例
            config: 插件配置字典
        """
        self._data_manager = data_manager
        self._config = config

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """启动汇率历史清理后台服务"""
        if self._task and not self._task.done():
            logger.warning("[汇率清理服务] 服务已在运行中")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_service())
        logger.info("[汇率清理服务] 后台服务已启动")

    async def stop(self) -> None:
        """停止汇率历史清理后台服务"""
        if not self._task or self._task.done():
            logger.info("[汇率清理服务] 服务已停止或未启动")
            return

        self._stop_event.set()
        self._task.cancel()

        try:
            await self._task
        except asyncio.CancelledError:
            pass

        logger.info("[汇率清理服务] 后台服务已停止")

    async def _run_service(self) -> None:
        """运行汇率历史清理服务的主循环"""
        # 等待数据库初始化完成（最多等待60秒）
        init_wait_time = 0
        max_wait_time = 60  # 最大等待60秒

        while not self._data_manager.is_db_initialized():
            if self._stop_event.is_set():
                logger.info("[汇率清理服务] 服务在数据库初始化前被停止")
                return
            if init_wait_time >= max_wait_time:
                logger.warning(
                    f"[汇率清理服务] 等待数据库初始化超时({max_wait_time}秒)，服务未启动"
                )
                return
            await asyncio.sleep(1)
            init_wait_time += 1

        if init_wait_time > 0:
            logger.info(f"[汇率清理服务] 数据库初始化完成，等待了 {init_wait_time} 秒")

        logger.info("[汇率清理服务] 清理循环已启动")

        # 获取清理间隔配置（小时转秒）
        stock_config = self._config.get("stock_market", {})
        interval_hours = stock_config.get("cleanup_interval_hours", 24)
        interval_seconds = interval_hours * 3600

        logger.info(
            f"[汇率清理服务] 清理间隔: {interval_hours}小时, "
            f"下次清理时间: {interval_hours}小时后"
        )

        while not self._stop_event.is_set():
            try:
                # 等待清理间隔或停止信号
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=interval_seconds,
                    )
                    # 如果收到停止信号，退出循环
                    if self._stop_event.is_set():
                        break
                except asyncio.TimeoutError:
                    # 正常超时，继续执行清理
                    pass

                # 执行清理
                await self._cleanup()

            except asyncio.CancelledError:
                logger.info("[汇率清理服务] 服务已取消")
                break
            except Exception as e:
                logger.error(f"[汇率清理服务] 服务运行出错: {e}")
                # 出错后等待一段时间再重试
                await asyncio.sleep(3600)  # 等待1小时后重试

    async def _cleanup(self) -> None:
        """执行汇率历史记录清理"""
        # 再次检查数据库是否已初始化
        if not self._data_manager.is_db_initialized():
            logger.debug("[汇率清理服务] 数据库尚未初始化，跳过本次清理")
            return

        try:
            stock_config = self._config.get("stock_market", {})
            keep_days = stock_config.get("cleanup_keep_days", 30)

            # 执行清理
            success = await self._data_manager.db.cleanup_old_exchange_rates(
                days=keep_days
            )

            if success:
                logger.info(f"[汇率清理服务] 成功清理 {keep_days} 天前的汇率历史记录")
            else:
                logger.warning("[汇率清理服务] 清理汇率历史记录失败")

        except Exception as e:
            logger.error(f"[汇率清理服务] 清理过程出错: {e}")

    def is_running(self) -> bool:
        """检查服务是否正在运行

        Returns:
            是否正在运行
        """
        return self._task is not None and not self._task.done()
