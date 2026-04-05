"""
自动签到服务模块

提供自动签到功能的逻辑处理，包括：
- 检测用户是否需要自动签到
- 记录用户自动签到状态
- 处理时区和日期变更逻辑
"""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytz

from astrbot.api import logger

if TYPE_CHECKING:
    from ..core.data_manager import DataManager

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")


class AutoCheckinService:
    """自动签到服务

    管理自动签到的状态跟踪和触发逻辑。
    """

    def __init__(self, data_manager: "DataManager"):
        """初始化自动签到服务

        Args:
            data_manager: 数据管理器实例
        """
        self._data_manager = data_manager
        self._timezone = SHANGHAI_TZ
        # 内存缓存，记录今日已自动签到的用户 {group_id: {user_id: True}}
        self._auto_checked_in_today: dict[str, dict[str, bool]] = {}

    def should_auto_checkin(self, user_id: str, group_id: str) -> bool:
        """检查用户是否应该触发自动签到

        判断条件：
        1. 用户今日尚未通过任何方式签到（手动或自动）
        2. 当前时间在上海时区0点之后（新的一天）

        Args:
            user_id: 用户ID
            group_id: 群ID

        Returns:
            是否应该触发自动签到
        """
        # 检查内存缓存中是否已自动签到
        if group_id in self._auto_checked_in_today:
            if self._auto_checked_in_today[group_id].get(user_id, False):
                return False

        # 检查数据库中今日是否已手动签到
        # 异步方法需要在调用处处理，这里只检查内存状态
        return True

    def mark_auto_checked_in(self, user_id: str, group_id: str) -> None:
        """标记用户已自动签到

        Args:
            user_id: 用户ID
            group_id: 群ID
        """
        if group_id not in self._auto_checked_in_today:
            self._auto_checked_in_today[group_id] = {}
        self._auto_checked_in_today[group_id][user_id] = True
        logger.debug(f"[自动签到] 用户 {user_id} 在群 {group_id} 已标记为自动签到")

    def is_new_day(self, last_checkin_time: datetime | None) -> bool:
        """检查是否是新的一天（基于上海时区）

        Args:
            last_checkin_time: 上次签到时间

        Returns:
            是否是新的一天（上次签到不是今天）
        """
        if last_checkin_time is None:
            return True

        now = datetime.now(self._timezone)
        # 确保 last_checkin_time 有时区信息
        if last_checkin_time.tzinfo is None:
            last_checkin_time = self._timezone.localize(last_checkin_time)

        # 比较日期部分
        return last_checkin_time.date() < now.date()

    async def has_checked_in_today(self, user_id: str, group_id: str) -> bool:
        """检查用户今日是否已签到（手动或自动）

        Args:
            user_id: 用户ID
            group_id: 群ID

        Returns:
            今日是否已签到
        """
        # 检查内存缓存中的自动签到状态
        if group_id in self._auto_checked_in_today:
            if self._auto_checked_in_today[group_id].get(user_id, False):
                return True

        # 检查数据库中的手动签到状态
        try:
            user_data = await self._data_manager.get_user_data(group_id, user_id)
            last_sign = user_data.get("last_sign")

            if not last_sign:
                return False

            # 解析上次签到时间（支持多种格式）
            try:
                # 尝试解析 ISO 格式 (2026-04-03T11:06:22.859891)
                if 'T' in last_sign:
                    last_sign_dt = datetime.fromisoformat(last_sign.replace('Z', '+00:00'))
                else:
                    # 尝试解析普通格式 (2026-04-03 11:06:22)
                    last_sign_dt = datetime.strptime(last_sign, "%Y-%m-%d %H:%M:%S")
                return not self.is_new_day(last_sign_dt)
            except ValueError:
                logger.warning(f"[自动签到] 无法解析上次签到时间: {last_sign}")
                return False

        except Exception as e:
            logger.error(f"[自动签到] 检查用户签到状态失败: {e}")
            return False

    def reset_daily_cache(self) -> None:
        """重置每日缓存（应在每天0点后调用）"""
        self._auto_checked_in_today.clear()
        logger.info("[自动签到] 每日缓存已重置")

    async def perform_auto_checkin(
        self,
        user_id: str,
        group_id: str,
        event,
        sign_in_handler,
    ) -> dict:
        """执行自动签到

        Args:
            user_id: 用户ID
            group_id: 群ID
            event: 消息事件
            sign_in_handler: 签到处理函数

        Returns:
            包含以下字段的字典:
            - should_reply: 是否需要回复
            - already_signed: 是否已经签到过
            - success: 是否成功触发签到（仅当 already_signed=False 时有效）
        """
        result = {
            "should_reply": False,
            "already_signed": False,
            "success": False,
        }

        # 1. 先检查内存缓存，如果已有记录则跳过（今日已处理过该用户）
        if group_id in self._auto_checked_in_today:
            if user_id in self._auto_checked_in_today[group_id]:
                return result

        # 2. 检查今日是否已签到（数据库中）
        has_signed = await self.has_checked_in_today(user_id, group_id)

        # 3. 标记到内存缓存（无论是否已签到，都标记为已处理，今日不再检查）
        self.mark_auto_checked_in(user_id, group_id)

        # 4. 设置需要回复
        result["should_reply"] = True

        # 5. 如果已签到，返回已签到状态
        if has_signed:
            result["already_signed"] = True
            return result

        # 6. 未签到，触发自动签到
        try:
            await sign_in_handler(event, is_auto=True)
            result["success"] = True
        except Exception as e:
            logger.error(f"[自动签到] 执行自动签到失败: {e}")
            result["success"] = False

        return result
