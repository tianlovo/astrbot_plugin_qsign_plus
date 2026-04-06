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

        # 计算差距
        gap = first_wealth - second_wealth

        # 获取配置
        penalty_config = self._config.get("wealth_gap_penalty", {})
        gap_threshold = penalty_config.get("gap_threshold", 2000)

        logger.debug(
            f"[财富差距惩罚] 群 {group_id} 排名: 第一 {first_user_id}({first_wealth}), "
            f"第二 {second_user_id}({second_wealth}), 差距 {gap}, 阈值 {gap_threshold}"
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
                elif gap <= gap_threshold:
                    # 是第一名但差距未超过阈值，去除debuff
                    should_remove = True
                    remove_reason = f"差距 {gap} <= 阈值 {gap_threshold}"

                if should_remove:
                    logger.info(
                        f"[财富差距惩罚] 群 {group_id} 用户 {user_id} {remove_reason}，准备去除debuff"
                    )
                    await self._remove_debuff(group_id, user_id)

        # 第二步：检查当前第一名是否应该赋予debuff
        if gap > gap_threshold:
            penalty_status = await self._data_manager.get_wealth_gap_penalty(
                group_id, first_user_id
            )

            if not penalty_status["has_debuff"]:
                logger.info(
                    f"[财富差距惩罚] 群 {group_id} 用户 {first_user_id} 差距 {gap} > 阈值 {gap_threshold}，"
                    f"准备赋予debuff"
                )
                await self._apply_debuff(group_id, first_user_id, gap)
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

        # 计算差距
        gap = first_wealth - second_wealth

        # 获取配置
        penalty_config = self._config.get("wealth_gap_penalty", {})
        gap_threshold = penalty_config.get("gap_threshold", 2000)

        # 获取当前debuff状态
        penalty_status = await self._data_manager.get_wealth_gap_penalty(
            group_id, first_user_id
        )

        # 只有有debuff的用户才处理
        if penalty_status["has_debuff"]:
            # 检查差距是否已缩小到阈值以下
            if gap <= gap_threshold:
                # 差距已缩小，去除debuff
                logger.info(
                    f"[财富差距惩罚] 群 {group_id} 扣除后用户 {first_user_id} 差距 {gap} <= 阈值 {gap_threshold}，"
                    f"准备去除debuff"
                )
                await self._remove_debuff(group_id, first_user_id)
            else:
                # 差距仍超过阈值，执行扣除
                await self._apply_penalty_deduction(
                    group_id, first_user_id, first_wealth, gap
                )

    async def _apply_debuff(self, group_id: str, user_id: str, gap: float) -> None:
        """赋予厄运debuff

        Args:
            group_id: 群ID
            user_id: 用户ID
            gap: 当前差距
        """
        penalty_config = self._config.get("wealth_gap_penalty", {})
        min_rate = penalty_config.get("min_penalty_rate", 0.01)
        max_rate = penalty_config.get("max_penalty_rate", 0.10)
        max_gap = penalty_config.get("max_gap_for_calculation", 10000)

        # 计算扣除比例
        penalty_rate = self._calculate_penalty_rate(gap, min_rate, max_rate, max_gap)

        # 保存debuff状态
        now = int(time.time())
        await self._data_manager.set_wealth_gap_penalty(
            group_id, user_id, True, penalty_rate, now
        )

        # 获取用户数据并执行首次扣除
        user_data = await self._data_manager.get_user_data(group_id, user_id)
        current_coins = user_data.get("coins", 0)
        penalty_amount = current_coins * penalty_rate
        new_coins = current_coins - penalty_amount
        user_data["coins"] = new_coins
        await self._data_manager.save_user_data(group_id, user_id, user_data)

        # 更新上次惩罚时间
        await self._data_manager.update_penalty_last_time(group_id, user_id, now)

        # 发送通知
        await self._send_debuff_notification(
            group_id, user_id, gap, penalty_rate, is_applying=True
        )

        logger.info(
            f"[财富差距惩罚] 群 {group_id} 用户 {user_id} 获得厄运，"
            f"差距: {gap:.1f}, 扣除比例: {penalty_rate*100:.1f}%, "
            f"首次扣除: {penalty_amount:.1f}, 剩余: {new_coins:.1f}"
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
        self, group_id: str, user_id: str, current_wealth: float, gap: float
    ) -> None:
        """执行扣除财富操作

        Args:
            group_id: 群ID
            user_id: 用户ID
            current_wealth: 当前身价
            gap: 当前差距
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
        max_gap = penalty_config.get("max_gap_for_calculation", 10000)
        penalty_rate = self._calculate_penalty_rate(gap, min_rate, max_rate, max_gap)

        # 获取用户数据
        user_data = await self._data_manager.get_user_data(group_id, user_id)
        current_coins = user_data.get("coins", 0)

        # 计算扣除金额（基于现金）
        penalty_amount = current_coins * penalty_rate

        # 扣除现金（可扣到负数）
        new_coins = current_coins - penalty_amount
        user_data["coins"] = new_coins
        await self._data_manager.save_user_data(group_id, user_id, user_data)

        # 更新上次惩罚时间和当前扣除比例
        await self._data_manager.update_penalty_last_time(group_id, user_id, now)
        await self._data_manager.set_wealth_gap_penalty(
            group_id, user_id, True, penalty_rate, penalty_status.get("debuff_start_time", now)
        )

        logger.info(
            f"[财富差距惩罚] 群 {group_id} 用户 {user_id} 扣除 {penalty_amount:.1f} "
            f"现金（比例 {penalty_rate*100:.1f}%），剩余 {new_coins:.1f}"
        )

    def _calculate_penalty_rate(
        self, gap: float, min_rate: float, max_rate: float, max_gap: float
    ) -> float:
        """计算扣除比例

        Args:
            gap: 当前差距
            min_rate: 最小扣除比例
            max_rate: 最大扣除比例
            max_gap: 用于计算的最大差距参考值

        Returns:
            扣除比例
        """
        if gap <= 0:
            return min_rate

        # 计算比例：差距越大，扣除比例越高
        ratio = min(gap / max_gap, 1.0)
        penalty_rate = min_rate + (max_rate - min_rate) * ratio

        return min(max(penalty_rate, min_rate), max_rate)

    async def _send_debuff_notification(
        self,
        group_id: str,
        user_id: str,
        gap: float,
        penalty_rate: float,
        is_applying: bool,
    ) -> None:
        """发送debuff通知

        Args:
            group_id: 群ID
            user_id: 用户ID
            gap: 差距
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
                    f"由于您当前财富榜第一，且与第二名差距过大（{gap:.1f}），"
                    f"您已获得【厄运】。\n"
                    f"每小时将扣除您 {penalty_rate*100:.1f}% 的现金，"
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
