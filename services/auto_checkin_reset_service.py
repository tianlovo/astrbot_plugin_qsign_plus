"""
自动签到缓存重置服务模块

提供每天0点（上海时区）重置自动签到缓存的后台服务。
"""

import asyncio
from datetime import datetime, timedelta

import pytz
from astrbot.api import logger

from ..core.auto_checkin_service import AutoCheckinService

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")


class AutoCheckinResetService:
    """自动签到缓存重置服务

    管理自动签到缓存的每日重置，确保每天0点后用户可以再次触发自动签到。
    """

    def __init__(self, auto_checkin_service: AutoCheckinService):
        """初始化缓存重置服务

        Args:
            auto_checkin_service: 自动签到服务实例
        """
        self._auto_checkin_service = auto_checkin_service
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """启动缓存重置后台服务"""
        if self._task and not self._task.done():
            logger.warning("[自动签到重置服务] 服务已在运行中")
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_service())
        logger.info("[自动签到重置服务] 后台服务已启动")

    async def stop(self) -> None:
        """停止缓存重置后台服务"""
        if not self._task or self._task.done():
            logger.info("[自动签到重置服务] 服务已停止或未启动")
            return

        self._stop_event.set()
        self._task.cancel()

        try:
            await self._task
        except asyncio.CancelledError:
            pass

        logger.info("[自动签到重置服务] 后台服务已停止")

    async def _run_service(self) -> None:
        """运行缓存重置服务主循环"""
        while not self._stop_event.is_set():
            try:
                # 计算到下一个0点的等待时间
                wait_seconds = self._calculate_wait_seconds()
                logger.debug(f"[自动签到重置服务] 距离下次重置还有 {wait_seconds} 秒")

                # 等待直到下一个0点或停止事件
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=wait_seconds
                    )
                    # 如果是因为停止事件唤醒，直接退出
                    if self._stop_event.is_set():
                        break
                except asyncio.TimeoutError:
                    # 超时，到达下一个0点，执行重置
                    pass

                # 重置缓存
                self._auto_checkin_service.reset_daily_cache()

            except Exception as e:
                logger.error(f"[自动签到重置服务] 服务运行出错: {e}")
                # 出错后等待1分钟再试
                await asyncio.sleep(60)

    def _calculate_wait_seconds(self) -> float:
        """计算到下一个0点的等待秒数

        Returns:
            等待秒数
        """
        now = datetime.now(SHANGHAI_TZ)
        # 下一个0点
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_seconds = (next_midnight - now).total_seconds()
        return max(1, wait_seconds)  # 至少等待1秒
