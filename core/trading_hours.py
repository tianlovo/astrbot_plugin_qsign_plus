"""
交易时段管理模块

提供股市交易时段的配置解析、时段检查、下一时段计算等功能。
"""

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

import pytz

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api import AstrBotConfig

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")

WEEKDAY_MAP = {
    "周一": 0,
    "周二": 1,
    "周三": 2,
    "周四": 3,
    "周五": 4,
    "周六": 5,
    "周日": 6,
}


class TradingHoursService:
    """交易时段服务

    管理股市交易时段的配置解析和时段检查。
    """

    def __init__(self, config: "AstrBotConfig"):
        """初始化交易时段服务

        Args:
            config: 插件配置对象
        """
        stock_config = config.get("stock_market", {})
        self._sessions = stock_config.get("trading_hours", [])
        self._timezone = SHANGHAI_TZ

        # 如果没有配置任何时段，则默认全天可交易
        self._always_trading = not self._sessions

        if self._always_trading:
            logger.info("[交易时段] 未配置交易时段，默认全天可交易")
        else:
            enabled_count = sum(1 for s in self._sessions if s.get("enabled", True))
            logger.info(f"[交易时段] 已配置 {enabled_count} 个交易时段")

    def is_trading_time(self) -> bool:
        """检查当前是否在交易时段内

        Returns:
            是否在交易时段内
        """
        if self._always_trading:
            return True

        now = datetime.now(self._timezone)
        current_weekday = now.weekday()
        current_time = now.time()

        for session in self._sessions:
            if not session.get("enabled", True):
                continue

            weekdays = session.get("weekdays", [])
            if not weekdays:
                continue

            # 检查星期是否匹配
            weekday_nums = [WEEKDAY_MAP.get(d) for d in weekdays if d in WEEKDAY_MAP]
            if current_weekday not in weekday_nums:
                continue

            # 解析时间
            try:
                start_time = self._parse_time(session.get("start_time", "00:00"))
                end_time = self._parse_time(session.get("end_time", "23:59"))
            except ValueError:
                continue

            # 检查时间是否在时段内
            if start_time <= current_time <= end_time:
                return True

        return False

    def get_current_session(self) -> str | None:
        """获取当前时段名称

        Returns:
            当前时段名称，如果不在时段内返回 None
        """
        if self._always_trading:
            return None

        now = datetime.now(self._timezone)
        current_weekday = now.weekday()
        current_time = now.time()

        for session in self._sessions:
            if not session.get("enabled", True):
                continue

            weekdays = session.get("weekdays", [])
            if not weekdays:
                continue

            weekday_nums = [WEEKDAY_MAP.get(d) for d in weekdays if d in WEEKDAY_MAP]
            if current_weekday not in weekday_nums:
                continue

            try:
                start_time = self._parse_time(session.get("start_time", "00:00"))
                end_time = self._parse_time(session.get("end_time", "23:59"))
            except ValueError:
                continue

            if start_time <= current_time <= end_time:
                return session.get("name", "交易时段")

        return None

    def get_next_opening(self) -> tuple[str, datetime] | None:
        """获取下一交易时段名称和开始时间

        Returns:
            (时段名称, 开始时间) 的元组，如果没有找到返回 None
        """
        if self._always_trading:
            return None

        now = datetime.now(self._timezone)
        candidates = []

        # 检查未来7天内的所有时段
        for day_offset in range(7):
            check_date = now + timedelta(days=day_offset)
            check_weekday = check_date.weekday()

            for session in self._sessions:
                if not session.get("enabled", True):
                    continue

                weekdays = session.get("weekdays", [])
                if not weekdays:
                    continue

                weekday_nums = [WEEKDAY_MAP.get(d) for d in weekdays if d in WEEKDAY_MAP]
                if check_weekday not in weekday_nums:
                    continue

                try:
                    start_time = self._parse_time(session.get("start_time", "00:00"))
                except ValueError:
                    continue

                # 组合日期和时间
                start_datetime = datetime.combine(check_date.date(), start_time)
                start_datetime = self._timezone.localize(start_datetime)

                # 只收集未来的时段
                if start_datetime > now:
                    candidates.append((session.get("name", "交易时段"), start_datetime))

        if not candidates:
            return None

        # 返回最近的时段
        return min(candidates, key=lambda x: x[1])

    def get_all_sessions(self) -> list[dict]:
        """获取所有配置的时段

        Returns:
            时段配置列表
        """
        return self._sessions

    def format_next_opening(self) -> str:
        """格式化下一交易时段信息

        Returns:
            格式化后的提示字符串
        """
        next_session = self.get_next_opening()
        if next_session:
            name, dt = next_session
            now = datetime.now(self._timezone)
            time_diff = dt - now

            if time_diff.days > 0:
                return f"下一交易时段：{name} {dt.strftime('%m-%d %H:%M')}"
            else:
                hours = int(time_diff.total_seconds() // 3600)
                minutes = int((time_diff.total_seconds() % 3600) // 60)
                if hours > 0:
                    return f"下一交易时段：{name} 还有{hours}小时{minutes}分钟"
                else:
                    return f"下一交易时段：{name} 还有{minutes}分钟"
        return "暂无 upcoming 交易时段"

    @staticmethod
    def _parse_time(time_str: str) -> "time":
        """解析时间字符串

        Args:
            time_str: 时间字符串，格式 HH:MM

        Returns:
            time 对象

        Raises:
            ValueError: 格式错误时抛出
        """
        try:
            hour, minute = map(int, time_str.split(":"))
            return time(hour=hour, minute=minute)
        except (ValueError, AttributeError) as e:
            raise ValueError(f"无效的时间格式: {time_str}") from e
