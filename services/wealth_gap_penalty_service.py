"""
财富榜差距惩罚后台服务模块

提供财富榜第一与第二名差距检测和惩罚机制：
- 每分钟检测财富榜第一和第二名的差距
- 差距过大时为第一名附加厄运debuff
- 每小时按动态比例扣除现金
- 差距缩小到阈值后去除debuff
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from astrbot.api import logger

if TYPE_CHECKING:
    from ..core.data_manager import DataManager
    from ..core.wealth_calculator import WealthCalculator


class WealthGapPenaltyService:
    """财富榜差距惩罚后台服务

    管理财富榜差距检测和惩罚机制：
    - 每分钟检测财富榜第一和第二名的差距
    - 差距超过阈值时赋予debuff并通知用户
    - 每小时按动态比例扣除现金（可扣到负数）
    - 差距缩小到阈值以下时去除debuff并通知用户
    """

    def __init__(
        self,
        data_manager: "DataManager",
        wealth_calculator: "WealthCalculator",
        config: dict,
        context=None,
    ):
        """初始化财富榜差距惩罚服务

        Args:
            data_manager: 数据管理器实例
            wealth_calculator: 身价计算器实例
            config: 插件配置字典
            context: AstrBot 上下文，用于发送消息
        """
        self._data_manager = data_manager
        self._wealth_calculator = wealth_calculator
        self._config = config
        self._context = context

        self._check_task: asyncio.Task | None = None
        self._penalty_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # 存储群组的 unified_msg_origin，格式: {group_id: umo}
        self._group_umo_cache: dict[str, str] = {}

    def update_group_umo(self, group_id: str, umo: str) -> None:
        """更新群的 unified_msg_origin

        Args:
            group_id: 群ID
            umo: unified_msg_origin 字符串
        """
        self._group_umo_cache[group_id] = umo

    def _get_group_umo(self, group_id: str) -> str | None:
        """获取群的 unified_msg_origin

        Args:
            group_id: 群ID

        Returns:
            unified_msg_origin 字符串，如果不存在则返回 None
        """
        return self._group_umo_cache.get(group_id)

    async def start(self) -> None:
        """启动财富榜差距惩罚服务"""
        if self._check_task and not self._check_task.done():
            logger.warning("[财富差距惩罚] 服务已在运行中")
            return

        self._stop_event.clear()
        # 启动两个独立的任务：检测任务和扣除任务
        self._check_task = asyncio.create_task(self._run_check_loop())
        self._penalty_task = asyncio.create_task(self._run_penalty_loop())
        logger.info("[财富差距惩罚] 后台服务已启动")

    async def stop(self) -> None:
        """停止财富榜差距惩罚服务"""
        if not self._check_task or self._check_task.done():
            logger.info("[财富差距惩罚] 服务已停止或未启动")
            return

        self._stop_event.set()
        
        # 取消两个任务
        if self._check_task:
            self._check_task.cancel()
        if self._penalty_task:
            self._penalty_task.cancel()

        try:
            if self._check_task:
                await self._check_task
        except asyncio.CancelledError:
            pass

        try:
            if self._penalty_task:
                await self._penalty_task
        except asyncio.CancelledError:
            pass

        logger.info("[财富差距惩罚] 后台服务已停止")

    async def _run_check_loop(self) -> None:
        """运行检测循环（检测间隔）"""
        penalty_config = self._config.get("wealth_gap_penalty", {})
        check_interval = penalty_config.get("check_interval_minutes", 1) * 60  # 转换为秒
        
        logger.info(f"[财富差距惩罚] 检测循环启动，间隔: {check_interval}秒")

        while not self._stop_event.is_set():
            logger.debug("[财富差距惩罚] 开始新一轮检测...")
            try:
                await self._check_all_groups()
            except Exception as e:
                logger.error(f"[财富差距惩罚] 检测过程出错: {e}")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=check_interval
                )
            except asyncio.TimeoutError:
                pass

    async def _run_penalty_loop(self) -> None:
        """运行扣除循环（扣除间隔）"""
        penalty_config = self._config.get("wealth_gap_penalty", {})
        penalty_interval = penalty_config.get("penalty_interval_minutes", 60) * 60  # 转换为秒

        while not self._stop_event.is_set():
            try:
                await self._apply_penalty_to_all_groups()
            except Exception as e:
                logger.error(f"[财富差距惩罚] 扣除过程出错: {e}")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=penalty_interval
                )
            except asyncio.TimeoutError:
                pass

    async def _check_all_groups(self) -> None:
        """检查所有启用群的财富榜差距（检测间隔）"""
        logger.debug("[财富差距惩罚] _check_all_groups 被调用")
        
        # 检查数据库是否已初始化
        if not self._data_manager.is_db_initialized():
            logger.debug("[财富差距惩罚] 数据库未初始化，跳过检测")
            return

        penalty_config = self._config.get("wealth_gap_penalty", {})
        if not penalty_config.get("enabled", True):
            logger.debug("[财富差距惩罚] 功能已禁用，跳过检测")
            return

        basic_config = self._config.get("basic", {})
        enabled_groups = basic_config.get("enabled_groups", [])
        
        logger.debug(f"[财富差距惩罚] 检测到 {len(enabled_groups)} 个启用群: {enabled_groups}")

        for group_id in enabled_groups:
            try:
                logger.debug(f"[财富差距惩罚] 开始检查群 {group_id}")
                await self._check_group_wealth_gap(group_id)
            except Exception as e:
                logger.error(f"[财富差距惩罚] 检查群 {group_id} 时出错: {e}")

    async def _apply_penalty_to_all_groups(self) -> None:
        """对所有有debuff的用户执行扣除（扣除间隔）"""
        # 检查数据库是否已初始化
        if not self._data_manager.is_db_initialized():
            return

        penalty_config = self._config.get("wealth_gap_penalty", {})
        if not penalty_config.get("enabled", True):
            return

        basic_config = self._config.get("basic", {})
        enabled_groups = basic_config.get("enabled_groups", [])

        for group_id in enabled_groups:
            try:
                await self._apply_penalty_to_group(group_id)
            except Exception as e:
                logger.error(f"[财富差距惩罚] 扣除群 {group_id} 时出错: {e}")

    async def _check_group_wealth_gap(self, group_id: str) -> None:
        """检查指定群的财富榜差距

        Args:
            group_id: 群ID
        """
        logger.debug(f"[财富差距惩罚] _check_group_wealth_gap 群 {group_id} 开始执行")

        # 获取群内所有用户
        group_users = await self._data_manager.get_group_users(group_id)
        logger.debug(f"[财富差距惩罚] 群 {group_id} 有 {len(group_users)} 个用户")

        if len(group_users) < 2:
            logger.debug(f"[财富差距惩罚] 群 {group_id} 用户不足2个，跳过")
            return  # 至少需要2个用户才能比较

        # 计算每个用户的身价
        user_wealth_list = []
        for user_id in group_users:
            try:
                user_data = await self._data_manager.get_user_data(group_id, user_id)
                total_wealth = await self._wealth_calculator.calculate_wealth_value(
                    group_id, user_data, user_id
                )
                user_wealth_list.append((user_id, total_wealth))
                logger.debug(f"[财富差距惩罚] 用户 {user_id} 身价: {total_wealth}")
            except Exception as e:
                logger.error(f"[财富差距惩罚] 计算用户 {user_id} 身价失败: {e}")

        logger.debug(f"[财富差距惩罚] 成功计算 {len(user_wealth_list)} 个用户身价")

        if len(user_wealth_list) < 2:
            logger.debug(f"[财富差距惩罚] 群 {group_id} 成功计算身价的用户不足2个，跳过")
            return

        # 按身价排序
        user_wealth_list.sort(key=lambda x: x[1], reverse=True)

        # 获取前两名
        first_user_id, first_wealth = user_wealth_list[0]
        second_user_id, second_wealth = user_wealth_list[1]

        # 计算相对差距百分比（以第二名为基准）
        if second_wealth > 0:
            gap_ratio = (first_wealth - second_wealth) / second_wealth
        else:
            gap_ratio = 0.0

        # 获取配置
        penalty_config = self._config.get("wealth_gap_penalty", {})
        gap_threshold = penalty_config.get("gap_threshold", 0.5)  # 默认50%

        logger.debug(
            f"[财富差距惩罚] 群 {group_id} 排名: 第一 {first_user_id}({first_wealth:.1f}), "
            f"第二 {second_user_id}({second_wealth:.1f}), "
            f"相对差距 {gap_ratio*100:.2f}%, 阈值 {gap_threshold*100:.0f}%"
        )

        # 第一步：检查所有有debuff的用户，如果不是当前第一名或不符合条件，去除debuff
        for user_id, user_wealth in user_wealth_list:
            penalty_status = await self._data_manager.get_wealth_gap_penalty(
                group_id, user_id
            )

            if penalty_status["has_debuff"]:
                # 检查是否应该去除debuff
                should_remove = False
                remove_reason = ""

                if user_id != first_user_id:
                    # 不是第一名，去除debuff
                    should_remove = True
                    remove_reason = f"不再是第一名（当前第一: {first_user_id}）"
                elif gap_ratio <= gap_threshold:
                    # 是第一名但差距未超过阈值，去除debuff
                    should_remove = True
                    remove_reason = f"相对差距 {gap_ratio*100:.2f}% <= 阈值 {gap_threshold*100:.0f}%"

                if should_remove:
                    logger.info(
                        f"[财富差距惩罚] 群 {group_id} 用户 {user_id} {remove_reason}，准备去除debuff"
                    )
                    await self._remove_debuff(group_id, user_id)

        # 第二步：检查当前第一名是否应该赋予debuff
        if gap_ratio > gap_threshold:
            penalty_status = await self._data_manager.get_wealth_gap_penalty(
                group_id, first_user_id
            )

            if not penalty_status["has_debuff"]:
                logger.info(
                    f"[财富差距惩罚] 群 {group_id} 用户 {first_user_id} 相对差距 {gap_ratio*100:.2f}% > 阈值 {gap_threshold*100:.0f}%，"
                    f"准备赋予debuff"
                )
                await self._apply_debuff(group_id, first_user_id, gap_ratio, first_wealth)
            else:
                logger.debug(
                    f"[财富差距惩罚] 群 {group_id} 用户 {first_user_id} 已有debuff，无需重复赋予"
                )

    async def _apply_penalty_to_group(self, group_id: str) -> None:
        """对指定群的有debuff用户执行扣除

        Args:
            group_id: 群ID
        """
        # 获取群内所有用户
        group_users = await self._data_manager.get_group_users(group_id)
        if len(group_users) < 2:
            return

        # 计算每个用户的身价
        user_wealth_list = []
        for user_id in group_users:
            try:
                user_data = await self._data_manager.get_user_data(group_id, user_id)
                total_wealth = await self._wealth_calculator.calculate_wealth_value(
                    group_id, user_data, user_id
                )
                user_wealth_list.append((user_id, total_wealth))
            except Exception as e:
                logger.error(f"[财富差距惩罚] 计算用户 {user_id} 身价失败: {e}")

        if len(user_wealth_list) < 2:
            return

        # 按身价排序
        user_wealth_list.sort(key=lambda x: x[1], reverse=True)

        # 获取第一名
        first_user_id, first_wealth = user_wealth_list[0]
        second_user_id, second_wealth = user_wealth_list[1]

        # 计算相对差距百分比（以第二名为基准）
        if second_wealth > 0:
            gap_ratio = (first_wealth - second_wealth) / second_wealth
        else:
            gap_ratio = 0.0

        # 获取配置
        penalty_config = self._config.get("wealth_gap_penalty", {})
        gap_threshold = penalty_config.get("gap_threshold", 0.5)  # 默认50%

        # 获取当前debuff状态
        penalty_status = await self._data_manager.get_wealth_gap_penalty(
            group_id, first_user_id
        )

        # 只有有debuff的用户才处理
        if penalty_status["has_debuff"]:
            # 检查差距是否已缩小到阈值以下
            if gap_ratio <= gap_threshold:
                # 差距已缩小，去除debuff
                logger.info(
                    f"[财富差距惩罚] 群 {group_id} 扣除后用户 {first_user_id} 相对差距 {gap_ratio*100:.2f}% <= 阈值 {gap_threshold*100:.0f}%，"
                    f"准备去除debuff"
                )
                await self._remove_debuff(group_id, first_user_id)
            else:
                # 差距仍超过阈值，执行扣除
                await self._apply_penalty_deduction(
                    group_id, first_user_id, first_wealth, gap_ratio
                )

    async def _apply_debuff(self, group_id: str, user_id: str, gap_ratio: float, first_wealth: float) -> None:
        """赋予厄运debuff

        Args:
            group_id: 群ID
            user_id: 用户ID
            gap_ratio: 相对差距比例（如0.5表示50%）
            first_wealth: 第一名身价
        """
        penalty_config = self._config.get("wealth_gap_penalty", {})
        min_rate = penalty_config.get("min_penalty_rate", 0.01)
        max_rate = penalty_config.get("max_penalty_rate", 0.10)
        # 最大差距比例用于计算，默认100%（即差距100%时达到最大扣除比例）
        max_gap_ratio = 1.0

        # 计算扣除比例
        penalty_rate = self._calculate_penalty_rate(gap_ratio, min_rate, max_rate, max_gap_ratio)

        # 保存debuff状态
        now = int(time.time())
        await self._data_manager.set_wealth_gap_penalty(
            group_id, user_id, True, penalty_rate, now
        )

        # 计算扣除金额（基于身价，可扣到负数）
        penalty_amount = first_wealth * penalty_rate

        # 从现金中扣除（允许扣到负数）
        user_data = await self._data_manager.get_user_data(group_id, user_id)
        current_coins = user_data.get("coins", 0)
        new_coins = current_coins - penalty_amount
        user_data["coins"] = new_coins
        await self._data_manager.save_user_data(group_id, user_id, user_data)

        # 更新上次惩罚时间
        await self._data_manager.update_penalty_last_time(group_id, user_id, now)

        # 发送通知
        await self._send_debuff_notification(
            group_id, user_id, gap_ratio, penalty_rate, is_applying=True
        )

        logger.info(
            f"[财富差距惩罚] 群 {group_id} 用户 {user_id} 获得厄运，"
            f"相对差距: {gap_ratio*100:.2f}%, 扣除比例: {penalty_rate*100:.1f}%, "
            f"基于身价 {first_wealth:.1f} 首次扣除: {penalty_amount:.1f}, 现金剩余: {new_coins:.1f}"
        )

    async def _remove_debuff(self, group_id: str, user_id: str) -> None:
        """去除厄运debuff

        Args:
            group_id: 群ID
            user_id: 用户ID
        """
        # 更新debuff状态
        await self._data_manager.set_wealth_gap_penalty(
            group_id, user_id, False, 0.0, 0
        )

        # 发送通知
        await self._send_debuff_notification(
            group_id, user_id, 0, 0, is_applying=False
        )

        logger.info(f"[财富差距惩罚] 群 {group_id} 用户 {user_id} 【厄运】已解除")

    async def _apply_penalty_deduction(
        self, group_id: str, user_id: str, current_wealth: float, gap_ratio: float
    ) -> None:
        """执行扣除财富操作

        Args:
            group_id: 群ID
            user_id: 用户ID
            current_wealth: 当前身价
            gap_ratio: 相对差距比例（如0.5表示50%）
        """
        penalty_config = self._config.get("wealth_gap_penalty", {})

        # 获取当前debuff状态
        penalty_status = await self._data_manager.get_wealth_gap_penalty(
            group_id, user_id
        )

        now = int(time.time())

        # 更新扣除比例（根据当前差距动态调整）
        min_rate = penalty_config.get("min_penalty_rate", 0.01)
        max_rate = penalty_config.get("max_penalty_rate", 0.10)
        # 最大差距比例用于计算，默认100%
        max_gap_ratio = 1.0
        penalty_rate = self._calculate_penalty_rate(gap_ratio, min_rate, max_rate, max_gap_ratio)

        # 计算扣除金额（基于身价，可扣到负数）
        penalty_amount = current_wealth * penalty_rate

        # 从现金中扣除（允许扣到负数）
        user_data = await self._data_manager.get_user_data(group_id, user_id)
        current_coins = user_data.get("coins", 0)
        new_coins = current_coins - penalty_amount
        user_data["coins"] = new_coins
        await self._data_manager.save_user_data(group_id, user_id, user_data)

        # 更新上次惩罚时间和当前扣除比例
        await self._data_manager.update_penalty_last_time(group_id, user_id, now)
        await self._data_manager.set_wealth_gap_penalty(
            group_id, user_id, True, penalty_rate, penalty_status.get("debuff_start_time", now)
        )

        logger.info(
            f"[财富差距惩罚] 群 {group_id} 用户 {user_id} 基于身价 {current_wealth:.1f} "
            f"扣除 {penalty_amount:.1f}（比例 {penalty_rate*100:.1f}%），现金剩余 {new_coins:.1f}"
        )

        # 将扣除的金额分配给财富榜前10名的其他群友
        if penalty_amount > 0:
            await self._redistribute_penalty_amount(group_id, user_id, penalty_amount)

    def _calculate_penalty_rate(
        self, gap_ratio: float, min_rate: float, max_rate: float, max_gap_ratio: float
    ) -> float:
        """计算扣除比例

        Args:
            gap_ratio: 相对差距比例（如0.5表示50%）
            min_rate: 最小扣除比例
            max_rate: 最大扣除比例
            max_gap_ratio: 用于计算的最大差距比例参考值（如1.0表示100%）

        Returns:
            扣除比例
        """
        if gap_ratio <= 0:
            return min_rate

        # 计算比例：差距越大，扣除比例越高
        ratio = min(gap_ratio / max_gap_ratio, 1.0)
        penalty_rate = min_rate + (max_rate - min_rate) * ratio

        return min(max(penalty_rate, min_rate), max_rate)

    async def _redistribute_penalty_amount(
        self, group_id: str, excluded_user_id: str, penalty_amount: float
    ) -> None:
        """将扣除的金额分配给财富榜前10名的其他群友

        Args:
            group_id: 群ID
            excluded_user_id: 被扣除的用户ID（不参与分配）
            penalty_amount: 扣除的总金额
        """
        try:
            # 获取群内所有用户
            group_users = await self._data_manager.get_group_users(group_id)
            if len(group_users) < 2:
                return

            # 计算每个用户的身价
            user_wealth_list = []
            for user_id in group_users:
                try:
                    user_data = await self._data_manager.get_user_data(group_id, user_id)
                    total_wealth = await self._wealth_calculator.calculate_wealth_value(
                        group_id, user_data, user_id
                    )
                    user_wealth_list.append((user_id, total_wealth))
                except Exception as e:
                    logger.error(f"[财富差距惩罚] 计算用户 {user_id} 身价失败: {e}")

            if len(user_wealth_list) < 2:
                return

            # 按身价排序
            user_wealth_list.sort(key=lambda x: x[1], reverse=True)

            # 获取前10名（排除被扣除的用户）
            top_10_users = [
                (uid, wealth)
                for uid, wealth in user_wealth_list[:10]
                if uid != excluded_user_id
            ]

            if not top_10_users:
                logger.debug(f"[财富差距惩罚] 群 {group_id} 前10名没有其他用户，跳过分配")
                return

            # 计算每人应得金额
            recipient_count = len(top_10_users)
            amount_per_user = penalty_amount / recipient_count

            # 给每个前10名用户增加系统货币
            for recipient_id, _ in top_10_users:
                try:
                    user_data = await self._data_manager.get_user_data(group_id, recipient_id)
                    current_coins = user_data.get("coins", 0)
                    user_data["coins"] = current_coins + amount_per_user
                    await self._data_manager.save_user_data(group_id, recipient_id, user_data)
                except Exception as e:
                    logger.error(f"[财富差距惩罚] 给用户 {recipient_id} 分配金额失败: {e}")

            logger.info(
                f"[财富差距惩罚] 群 {group_id} 扣除金额 {penalty_amount:.1f} "
                f"已均分给 {recipient_count} 个前10名用户，每人 {amount_per_user:.1f}"
            )

            # 发送分配通知
            await self._send_redistribution_notification(
                group_id, excluded_user_id, penalty_amount, recipient_count
            )

        except Exception as e:
            logger.error(f"[财富差距惩罚] 分配扣除金额失败: {e}")

    async def _send_redistribution_notification(
        self, group_id: str, penalized_user_id: str, penalty_amount: float, recipient_count: int
    ) -> None:
        """发送分配通知

        Args:
            group_id: 群ID
            penalized_user_id: 被扣除的用户ID
            penalty_amount: 扣除的总金额
            recipient_count: 分配人数
        """
        if not self._context:
            return

        try:
            from astrbot.api.message_components import Plain
            from astrbot.core.message.message_event_result import MessageChain

            # 获取被扣除用户的昵称
            user_nickname = await self._get_user_nickname(group_id, penalized_user_id)

            message_text = (
                f"【厄运】从群友 {user_nickname} 扣除的 {penalty_amount:.1f} 系统货币 "
                f"已均分给财富榜前10名的 {recipient_count} 位群友"
            )

            # 构建消息链（不AT任何人）
            chain = MessageChain([Plain(message_text)])

            # 获取群的 unified_msg_origin
            umo = self._get_group_umo(group_id)
            if not umo:
                logger.warning(f"[财富差距惩罚] 群 {group_id} 的 umo 未找到，无法发送分配通知")
                return

            # 发送消息
            await self._context.send_message(umo, chain)

        except Exception as e:
            logger.error(f"[财富差距惩罚] 发送分配通知失败: {e}")

    async def _get_user_nickname(self, group_id: str, user_id: str) -> str:
        """获取用户群昵称

        Args:
            group_id: 群ID
            user_id: 用户ID

        Returns:
            用户昵称，如果获取失败则返回用户ID后4位
        """
        try:
            # 从 umo 获取平台信息
            umo = self._get_group_umo(group_id)
            if not umo:
                return f"用户{user_id[-4:]}"

            # 解析 umo 获取平台ID
            platform_id = umo.split(":")[0]

            # 获取平台适配器
            platform = self._context.get_platform_inst(platform_id)
            if not platform:
                return f"用户{user_id[-4:]}"

            # 调用API获取群成员信息
            if platform_id == "aiocqhttp":
                try:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
                        AiocqhttpPlatformAdapter,
                    )

                    if isinstance(platform, AiocqhttpPlatformAdapter):
                        client = platform.bot
                        # 使用 get_group_member_info 方法获取群成员信息
                        resp = await client.get_group_member_info(
                            group_id=int(group_id),
                            user_id=int(user_id),
                            no_cache=True,
                        )
                        # 优先返回群名片(card)，如果没有则返回QQ昵称(nickname)
                        return resp.get("card") or resp.get("nickname") or f"用户{user_id[-4:]}"
                except Exception as e:
                    logger.debug(f"[财富差距惩罚] 获取用户 {user_id} 群昵称失败: {e}")

            return f"用户{user_id[-4:]}"
        except Exception as e:
            logger.debug(f"[财富差距惩罚] 获取用户昵称失败: {e}")
            return f"用户{user_id[-4:]}"

    async def _send_debuff_notification(
        self,
        group_id: str,
        user_id: str,
        gap_ratio: float,
        penalty_rate: float,
        is_applying: bool,
    ) -> None:
        """发送debuff通知

        Args:
            group_id: 群ID
            user_id: 用户ID
            gap_ratio: 相对差距比例（如0.5表示50%）
            penalty_rate: 扣除比例
            is_applying: 是否为赋予debuff（False表示去除）
        """
        if not self._context:
            return

        try:
            from astrbot.api.message_components import At, Plain
            from astrbot.core.message.message_event_result import MessageChain

            if is_applying:
                message_text = (
                    f"⚠️ 厄运降临！\n"
                    f"由于您当前财富榜第一，且与第二名差距过大（{gap_ratio*100:.1f}%），"
                    f"您已获得【厄运】。\n"
                    f"每小时将扣除您 {penalty_rate*100:.1f}% 的现金，"
                    f"扣除的金额将均分给财富榜前10名的其他群友。\n"
                    f"直到差距缩小到阈值以下。"
                )
            else:
                message_text = (
                    f"✨ 厄运解除！\n"
                    f"您与第二名的差距已缩小到阈值以下，【厄运】已解除。"
                )

            # 构建消息链
            chain = MessageChain([At(qq=user_id), Plain(message_text)])

            # 获取群的 unified_msg_origin
            umo = self._get_group_umo(group_id)
            if not umo:
                logger.warning(f"[财富差距惩罚] 群 {group_id} 的 umo 未找到，无法发送通知")
                return

            # 发送消息
            await self._context.send_message(umo, chain)

        except Exception as e:
            logger.error(f"[财富差距惩罚] 发送通知失败: {e}")
