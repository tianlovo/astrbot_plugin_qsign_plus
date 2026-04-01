"""
打卡奖励后台服务模块

提供群打卡数据轮询和金币奖励发放功能。
使用 AsyncIOScheduler 实现定时轮询。
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import astrbot.api.message_components as Comp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from astrbot.api import logger

from ..core.data_manager import DataManager


class CheckinRewardService:
    """打卡奖励服务

    负责轮询群打卡数据，为打卡成员发放金币奖励。
    奖励机制：越早打卡奖励越多，前3名有额外奖励。
    """

    def __init__(
        self,
        data_manager: DataManager,
        config: dict,
        bot_instance=None,
    ):
        """初始化打卡奖励服务

        Args:
            data_manager: 数据管理器实例
            config: 配置字典
            bot_instance: 机器人实例，用于发送通知
        """
        self.data_manager = data_manager
        self.config = config
        self.bot_instance = bot_instance
        self.scheduler = AsyncIOScheduler()

        # 加载配置
        checkin_config = config.get("checkin_reward", {})
        self.enabled = checkin_config.get("enable_checkin_reward", True)
        self.poll_interval = checkin_config.get("poll_interval", 60)  # 默认60秒
        self.base_reward = checkin_config.get("base_reward", 100.0)
        self.first_extra = checkin_config.get("first_extra_reward", 50.0)
        self.second_extra = checkin_config.get("second_extra_reward", 30.0)
        self.third_extra = checkin_config.get("third_extra_reward", 20.0)
        self.decay_rate = checkin_config.get("decay_rate", 0.1)

        # 运行时状态
        self._current_date: str = ""
        self._daily_checkin_count: dict[str, int] = {}  # {group_id: count}
        self._processed_checkins: dict[str, set] = {}  # {group_id: {user_id}}
        self._first_batch_sent: dict[
            str, bool
        ] = {}  # {group_id: bool} 是否已发送首次批次通知

    async def start(self) -> None:
        """启动打卡奖励服务"""
        if not self.enabled:
            logger.info("[CheckinReward] 打卡奖励服务已禁用")
            return

        # 初始化今日日期
        self._current_date = datetime.now().strftime("%Y-%m-%d")
        self._daily_checkin_count = {}
        self._processed_checkins = {}
        self._first_batch_sent = {}

        # 添加轮询任务
        self.scheduler.add_job(
            self._poll_checkin_data,
            IntervalTrigger(seconds=self.poll_interval),
            id="checkin_poll",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(
            f"[CheckinReward] 打卡奖励服务已启动，轮询间隔: {self.poll_interval}秒"
        )

    async def stop(self) -> None:
        """停止打卡奖励服务"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("[CheckinReward] 打卡奖励服务已停止")

    def update_bot_instance(self, bot_instance) -> None:
        """更新机器人实例

        Args:
            bot_instance: 机器人实例
        """
        self.bot_instance = bot_instance

    async def _poll_checkin_data(self) -> None:
        """轮询群打卡数据"""
        try:
            # 检查是否新的一天
            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._current_date:
                self._reset_daily_data(today)

            # 获取配置的群组列表
            basic_config = self.config.get("basic", {})
            enabled_groups = basic_config.get("enabled_groups", [])

            if not enabled_groups:
                # 如果没有配置群组，则轮询所有有数据的群组
                enabled_groups = await self._get_all_groups()

            # 轮询每个群组
            for group_id in enabled_groups:
                await self._process_group_checkins(str(group_id))

        except Exception as e:
            logger.error(f"[CheckinReward] 轮询打卡数据失败: {e}")

    def _reset_daily_data(self, new_date: str) -> None:
        """重置每日数据

        Args:
            new_date: 新日期字符串
        """
        self._current_date = new_date
        self._daily_checkin_count = {}
        self._processed_checkins = {}
        self._first_batch_sent = {}
        logger.info(f"[CheckinReward] 新的一天，数据已重置: {new_date}")

    async def _get_all_groups(self) -> list[str]:
        """获取所有有数据的群组

        Returns:
            群组ID列表
        """
        # 这里需要从数据库获取所有群组
        # 暂时返回空列表，需要通过其他方式获取
        return []

    async def _process_group_checkins(self, group_id: str) -> None:
        """处理单个群组的打卡数据

        Args:
            group_id: 群ID
        """
        try:
            # 获取群打卡数据（通过QQ API）
            checkin_data = await self._fetch_group_checkin_data(group_id)
            if not checkin_data:
                return

            # 初始化群组数据
            if group_id not in self._processed_checkins:
                self._processed_checkins[group_id] = set()
            if group_id not in self._daily_checkin_count:
                self._daily_checkin_count[group_id] = 0
            if group_id not in self._first_batch_sent:
                self._first_batch_sent[group_id] = False

            # 处理新打卡成员
            new_checkins = []
            for user_id, checkin_time in checkin_data.items():
                if user_id not in self._processed_checkins[group_id]:
                    new_checkins.append((user_id, checkin_time))

            if not new_checkins:
                return

            # 按打卡时间排序
            new_checkins.sort(key=lambda x: x[1])

            # 判断是否是当天的首次打卡批次
            is_first_batch = self._daily_checkin_count[group_id] == 0

            # 处理每个新打卡成员
            rewarded_users = []
            for user_id, checkin_time in new_checkins:
                rank = self._daily_checkin_count[group_id] + 1
                reward = self._calculate_reward(rank)

                # 发放奖励
                success = await self._grant_reward(group_id, user_id, reward)
                if success:
                    self._processed_checkins[group_id].add(user_id)
                    self._daily_checkin_count[group_id] += 1
                    rewarded_users.append((user_id, rank, reward))

            # 发送通知
            if rewarded_users and self.bot_instance:
                await self._send_reward_notification(
                    group_id, rewarded_users, is_first_batch
                )

        except Exception as e:
            logger.error(f"[CheckinReward] 处理群组 {group_id} 打卡数据失败: {e}")

    async def _fetch_group_checkin_data(
        self, group_id: str
    ) -> dict[str, datetime] | None:
        """获取群打卡数据

        Args:
            group_id: 群ID

        Returns:
            {user_id: checkin_time} 字典，如果没有数据返回 None
        """
        if not self.bot_instance:
            return None

        try:
            # 调用 QQ API 获取群打卡数据
            # 注意：这里需要根据实际 API 调整
            result = await self.bot_instance.api.call_action(
                "get_group_signin_list",
                group_id=int(group_id),
            )

            if not result or not isinstance(result, list):
                return None

            checkin_data = {}
            for item in result:
                user_id = str(item.get("user_id", ""))
                signin_time = item.get("signin_time", 0)
                if user_id and signin_time:
                    checkin_time = datetime.fromtimestamp(signin_time)
                    # 只处理今天的打卡
                    if checkin_time.strftime("%Y-%m-%d") == self._current_date:
                        checkin_data[user_id] = checkin_time

            return checkin_data

        except Exception as e:
            logger.debug(f"[CheckinReward] 获取群 {group_id} 打卡数据失败: {e}")
            return None

    def _calculate_reward(self, rank: int) -> float:
        """计算奖励金额

        Args:
            rank: 打卡排名（从1开始）

        Returns:
            奖励金额
        """
        # 只有第1~3名有额外奖励
        if rank == 1:
            return self.base_reward + self.first_extra
        elif rank == 2:
            return self.base_reward * 0.8 + self.second_extra
        elif rank == 3:
            return self.base_reward * 0.6 + self.third_extra
        else:
            # 第4名及以后递减，最低50%，没有额外奖励
            decay = min(0.5, (rank - 1) * self.decay_rate)
            return self.base_reward * max(0.5, 1 - decay)

    async def _grant_reward(self, group_id: str, user_id: str, reward: float) -> bool:
        """发放奖励

        Args:
            group_id: 群ID
            user_id: 用户ID
            reward: 奖励金额

        Returns:
            是否成功
        """
        try:
            # 获取用户当前数据
            user_data = await self.data_manager.get_user_data(group_id, user_id)

            # 增加金币
            user_data["coins"] += reward

            # 保存数据
            await self.data_manager.save_user_data(group_id, user_id, user_data)

            # 记录打卡记录
            await self._record_checkin(group_id, user_id, reward)

            logger.info(
                f"[CheckinReward] 发放奖励成功: 群 {group_id}, 用户 {user_id}, "
                f"排名 {self._daily_checkin_count.get(group_id, 0) + 1}, 奖励 {reward:.1f}"
            )
            return True

        except Exception as e:
            logger.error(f"[CheckinReward] 发放奖励失败: {e}")
            return False

    async def _record_checkin(self, group_id: str, user_id: str, reward: float) -> None:
        """记录打卡记录

        Args:
            group_id: 群ID
            user_id: 用户ID
            reward: 奖励金额
        """
        try:
            rank = self._daily_checkin_count.get(group_id, 0) + 1
            await self.data_manager.record_checkin(
                group_id=group_id,
                user_id=user_id,
                checkin_date=self._current_date,
                rank=rank,
                reward=reward,
            )
        except Exception as e:
            logger.error(f"[CheckinReward] 记录打卡失败: {e}")

    async def _send_reward_notification(
        self,
        group_id: str,
        rewarded_users: list[tuple[str, int, float]],
        is_first_batch: bool,
    ) -> None:
        """发送奖励通知

        Args:
            group_id: 群ID
            rewarded_users: [(user_id, rank, reward), ...] 列表
            is_first_batch: 是否是当天的首次打卡批次
        """
        try:
            if not rewarded_users:
                return

            # 获取前3名用户信息
            top3_users = rewarded_users[:3]

            # 构建消息链
            message_chain = []

            # 如果是首次批次，添加额外恭喜消息
            if is_first_batch and not self._first_batch_sent.get(group_id, False):
                message_chain.append(Comp.Plain("🎉 今日首批打卡成员出现！\n\n"))
                self._first_batch_sent[group_id] = True

            # 添加恭喜文字
            message_chain.append(Comp.Plain("恭喜 "))

            # 添加前3名的 @（使用 Comp.At 组件）
            for i, (user_id, rank, reward) in enumerate(top3_users):
                message_chain.append(Comp.At(qq=user_id))
                if i < len(top3_users) - 1:
                    message_chain.append(Comp.Plain(" "))

            # 如果有更多人，添加"等等"
            if len(rewarded_users) > 3:
                message_chain.append(Comp.Plain(" 等等"))

            message_chain.append(Comp.Plain(" 完成今日打卡，奖励已到账！"))

            # 添加详细信息
            message_chain.append(Comp.Plain("\n\n【本批次打卡详情】\n"))
            for user_id, rank, reward in rewarded_users:
                medal = (
                    "🥇"
                    if rank == 1
                    else "🥈"
                    if rank == 2
                    else "🥉"
                    if rank == 3
                    else "🏅"
                )
                # 只有前3名显示额外奖励信息
                if rank <= 3:
                    extra = ""
                    if rank == 1:
                        extra = f" (含第1名额外{self.first_extra:.0f})"
                    elif rank == 2:
                        extra = f" (含第2名额外{self.second_extra:.0f})"
                    elif rank == 3:
                        extra = f" (含第3名额外{self.third_extra:.0f})"
                    message_chain.append(
                        Comp.Plain(f"{medal} 第{rank}名: +{reward:.1f}金币{extra}\n")
                    )
                else:
                    message_chain.append(
                        Comp.Plain(f"{medal} 第{rank}名: +{reward:.1f}金币\n")
                    )

            # 发送群消息
            if self.bot_instance:
                await self.bot_instance.api.call_action(
                    "send_group_msg",
                    group_id=int(group_id),
                    message=message_chain,
                )
                logger.info(f"[CheckinReward] 已发送奖励通知到群 {group_id}")

        except Exception as e:
            logger.error(f"[CheckinReward] 发送奖励通知失败: {e}")
