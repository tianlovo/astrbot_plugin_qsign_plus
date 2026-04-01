import asyncio
import os
from datetime import datetime

import pytz

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .core.data_manager import DataManager
from .core.wealth_system import WealthSystem
from .services.card_renderer import CardRenderer
from .services.image_cache import ImageCacheService
from .utils.helpers import (
    get_first_at_user,
    get_target_at_user,
    is_at_bot,
    is_group_allowed,
)
from .utils.message_utils import recall_message, send_image_reply, send_text_reply

PLUGIN_DIR = os.path.dirname(__file__)
SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")


@register(
    "astrbot_plugin_qsign_plus",
    "tianluoqaq",
    "二次元签到插件",
    "2.8.0",
    "https://github.com/tianlovo/astrbot_plugin_qsign_plus",
)
class ContractSystem(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # Initialize services
        self.data_manager = DataManager(PLUGIN_DIR)
        self.wealth_system = WealthSystem(self.data_manager, config)
        self.image_cache = ImageCacheService()
        self.card_renderer = CardRenderer(
            PLUGIN_DIR,
            self.data_manager,
            self.wealth_system,
            self.image_cache,
        )

        # Query state management: {group_id: {user_id: {"text_message_id": str, "is_generating": bool}}}
        self._query_states: dict[str, dict[str, dict]] = {}

        # Initialize checkin reward service
        from .services.checkin_reward_service import CheckinRewardService

        self.checkin_reward_service = CheckinRewardService(
            self.data_manager, config, None
        )

        # Load data to cache
        asyncio.create_task(self._initialize_services())

    async def _initialize_services(self) -> None:
        """初始化所有服务"""
        await self.data_manager.init()
        # 启动打卡奖励服务
        await self.checkin_reward_service.start()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=999)
    async def _capture_bot_instance(self, event: AstrMessageEvent):
        """捕获机器人实例并更新到服务"""
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    # 更新打卡奖励服务的 bot 实例
                    if self.checkin_reward_service.bot_instance is None:
                        self.checkin_reward_service.update_bot_instance(event.bot)
                        logger.info("[CheckinReward] 已捕获 aiocqhttp 机器人实例")
            except ImportError:
                logger.warning("[CheckinReward] 无法导入 AiocqhttpMessageEvent")

    async def _get_user_role(self, event: AstrMessageEvent, user_id: str) -> str:
        """获取用户在群中的角色

        Args:
            event: 消息事件
            user_id: 用户ID

        Returns:
            角色: "owner"(群主), "admin"(管理员), "member"(普通成员)
        """
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    resp = await client.api.call_action(
                        "get_group_member_info",
                        group_id=event.message_obj.group_id,
                        user_id=int(user_id),
                        no_cache=True,
                    )
                    return resp.get("role", "member")
            except Exception as e:
                logger.warning(f"获取用户角色失败({user_id}): {e}")
        return "member"

    async def _is_user_admin(self, event: AstrMessageEvent, user_id: str) -> bool:
        """检查用户是否为群主或管理员

        Args:
            event: 消息事件
            user_id: 用户ID

        Returns:
            是否为群主或管理员
        """
        role = await self._get_user_role(event, user_id)
        return role in ["owner", "admin"]

    @filter.regex(r"^购买")
    async def purchase(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        target_id = get_target_at_user(event)

        # 如果没有找到非机器人的at，尝试获取第一个at（可能是机器人）
        if not target_id:
            target_id = get_first_at_user(event)

        if not target_id:
            await send_text_reply(event, "请使用@指定要购买的对象。")
            return

        user_id = str(event.get_sender_id())

        if user_id == target_id:
            await send_text_reply(event, "您不能购买自己。")
            return

        # 检查目标用户角色
        target_role = await self._get_user_role(event, target_id)
        admin_config = self.config.get("admin", {})

        # 群主默认不可被购买（除非配置允许）
        if target_role == "owner":
            owner_can_be_purchased = admin_config.get("owner_can_be_purchased", False)
            if not owner_can_be_purchased:
                await send_text_reply(event, "群主不可被购买！")
                return

        # 获取基础身价
        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        if len(employer_data["contractors"]) >= 3:
            await send_text_reply(event, "已达到最大雇佣数量（3人）。")
            return

        base_cost = await self.wealth_system.calculate_dynamic_wealth_value(
            group_id, target_data, target_id
        )

        # 管理员和群主享受价格加成
        if target_role in ["owner", "admin"]:
            admin_bonus = admin_config.get("admin_price_bonus", 0.5)
            base_cost *= 1 + admin_bonus

        total_cost = base_cost
        original_owner_id = target_data.get("contracted_by")

        if original_owner_id:
            if original_owner_id == user_id:
                await send_text_reply(event, "该用户已经是您的雇员了。")
                return

            trade_config = self.config.get("trade", {})
            takeover_rate = trade_config.get("takeover_fee_rate", 0.1)
            extra_cost = base_cost * takeover_rate
            total_cost += extra_cost
            compensation = total_cost

            if employer_data["coins"] < total_cost:
                await send_text_reply(
                    event,
                    f"现金不足，恶意收购需要支付 {total_cost:.1f} 金币（含{takeover_rate * 100}%额外费用）。",
                )
                return

            original_owner_data = await self.data_manager.get_user_data(
                group_id, original_owner_id
            )

            # Update coins
            employer_data["coins"] -= total_cost
            original_owner_data["coins"] += compensation

            # Update contractor relationships in database
            await self.data_manager.remove_contractor(
                group_id, original_owner_id, target_id
            )
            await self.data_manager.add_contractor(group_id, user_id, target_id)

            # Save user data
            await self.data_manager.save_user_data(group_id, user_id, employer_data)
            await self.data_manager.save_user_data(
                group_id, original_owner_id, original_owner_data
            )

            await self.data_manager.increment_purchase_count(target_id)

            target_name = await self._get_user_name_from_platform(event, target_id)
            original_owner_name = await self._get_user_name_from_platform(
                event, original_owner_id
            )
            await send_text_reply(
                event,
                f"恶意收购成功！您花费 {total_cost:.1f} 金币从 {original_owner_name} 手中抢走了 {target_name}。"
                f"原雇主获得了全部转让费 {compensation:.1f} 金币。",
            )
            return

        if employer_data["coins"] < total_cost:
            await send_text_reply(
                event, f"现金不足，雇佣需要支付目标身价：{total_cost:.1f}金币。"
            )
            return

        employer_data["coins"] -= total_cost

        # Update contractor relationship in database
        await self.data_manager.add_contractor(group_id, user_id, target_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, employer_data)

        await self.data_manager.increment_purchase_count(target_id)

        target_name = await self._get_user_name_from_platform(event, target_id)
        await send_text_reply(
            event, f"成功雇佣 {target_name}，消耗{total_cost:.1f}金币。"
        )

    @filter.regex(r"^出售")
    async def sell(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        target_id = get_target_at_user(event)

        # 如果没有找到非机器人的at，尝试获取第一个at（可能是机器人）
        if not target_id:
            target_id = get_first_at_user(event)

        if not target_id:
            await send_text_reply(event, "请使用@指定要出售的对象。")
            return

        user_id = str(event.get_sender_id())

        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        if target_id not in employer_data["contractors"]:
            await send_text_reply(event, "该用户不在你的雇员列表中。")
            return

        trade_config = self.config.get("trade", {})
        sell_rate = trade_config.get("sell_return_rate", 0.8)
        sell_price = (
            await self.wealth_system.calculate_dynamic_wealth_value(
                group_id, target_data, target_id
            )
            * sell_rate
        )

        employer_data["coins"] += sell_price

        # Update contractor relationship in database
        await self.data_manager.remove_contractor(group_id, user_id, target_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, employer_data)

        target_name = await self._get_user_name_from_platform(event, target_id)
        await send_text_reply(
            event, f"成功解雇 {target_name}，获得补偿金{sell_price:.1f}金币。"
        )

    @filter.regex(r"^签到$")
    async def sign_in(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        now = datetime.now(SHANGHAI_TZ)
        today = now.date()

        if user_data["last_sign"]:
            last_sign_dt = datetime.fromisoformat(user_data["last_sign"])
            last_sign_aware = SHANGHAI_TZ.localize(last_sign_dt)
            if last_sign_aware.date() == today:
                await send_text_reply(event, "你今天已经签到过了，明天再来吧。")
                return
            if (today - last_sign_aware.date()).days == 1:
                user_data["consecutive"] += 1
            else:
                user_data["consecutive"] = 1
        else:
            user_data["consecutive"] = 1

        interest = user_data["bank"] * 0.01
        user_data["bank"] += interest

        is_penalized = bool(user_data["contracted_by"])

        (
            earned,
            original_earned,
            base_with_bonus,
            contract_bonus,
            consecutive_bonus,
            interest,
        ) = await self.wealth_system.calculate_sign_income(
            user_data, group_id, is_penalized
        )

        user_data["coins"] += earned
        user_data["last_sign"] = now.replace(tzinfo=None).isoformat()

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        # Check if image card is enabled
        basic_config = self.config.get("basic", {})
        enable_image_card = basic_config.get("enable_image_card", True)

        if not enable_image_card:
            # Send text-only sign-in result with full details
            user_name = await self._get_user_name_from_platform(event, user_id)
            wealth_level, _ = self.wealth_system.get_wealth_info(user_data)

            sign_text = f"【签到成功】\n"
            sign_text += f"👤 用户: {user_name}\n"
            sign_text += f"💎 财富等级: {wealth_level}\n"
            sign_text += f"📊 状态: {'受雇' if is_penalized else '自由'}\n"
            sign_text += f"📅 签到时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            sign_text += f"🔥 连续签到: {user_data['consecutive']} 天\n\n"

            sign_text += f"【今日收益明细】\n"
            sign_text += f"💵 基础收益: {base_with_bonus:.1f} 金币\n"
            if contract_bonus > 0:
                sign_text += f"👥 雇员加成: {contract_bonus:.1f} 金币\n"
            if consecutive_bonus > 0:
                sign_text += f"🔥 连续签到加成: {consecutive_bonus:.1f} 金币\n"
            sign_text += f"🏦 银行利息: {interest:.1f} 金币\n"
            sign_text += f"📊 小计: {original_earned:.1f} 金币\n"
            if is_penalized:
                sign_text += f"⚠️ 受雇惩罚后: {earned:.1f} 金币\n"
            sign_text += f"✅ 今日总收益: {earned:.1f} 金币\n\n"

            sign_text += f"【资产状况】\n"
            sign_text += f"💰 现金: {user_data['coins']:.1f} 金币\n"
            sign_text += f"🏦 银行存款: {user_data['bank']:.1f} 金币\n"
            sign_text += (
                f"💎 总资产: {user_data['coins'] + user_data['bank']:.1f} 金币\n\n"
            )

            sign_text += f"👥 雇员数量: {len(user_data['contractors'])} 人"
            await send_text_reply(event, sign_text)
            return

        # Generate card
        bg_api_url = basic_config.get("bg_api_url", "https://t.alcy.cc/ycy")
        render_data = await self.card_renderer.generate_sign_card(
            event,
            is_penalized=is_penalized,
            original_earned=original_earned,
            bg_api_url=bg_api_url,
        )

        try:
            html_url = await self.html_render(
                self.card_renderer.get_template(), render_data
            )
            if html_url:
                await send_image_reply(event, html_url)
            else:
                await send_text_reply(event, "签到成功！但图片生成失败。")
        except Exception as e:
            logger.error(f"HTML 渲染失败: {e}")
            await send_text_reply(event, "签到成功！但图片生成失败。")

    @filter.regex(r"^(排行榜|财富榜)$")
    async def leaderboard(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # Get leaderboard from database
        top_10_users = await self.data_manager.get_leaderboard(group_id, limit=10)

        if not top_10_users:
            await send_text_reply(event, "本群暂无签到数据，无法生成排行榜。")
            return

        user_ids_to_fetch = [user[0] for user in top_10_users]
        name_coroutines = [
            self._get_user_name_from_platform(event, uid) for uid in user_ids_to_fetch
        ]
        names = await asyncio.gather(*name_coroutines)

        leaderboard_str = "本群财富排行榜\n" + "-" * 20 + "\n"
        for rank, ((user_id, total_wealth), user_name) in enumerate(
            zip(top_10_users, names), start=1
        ):
            leaderboard_str += f"第{rank}名: {user_name} - {total_wealth:.1f} 金币\n"

        await send_text_reply(event, leaderboard_str.strip())

    @filter.regex(r"^赎身$")
    async def terminate_contract(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        if not user_data["contracted_by"]:
            await send_text_reply(event, "您是自由身，无需赎身。")
            return

        cost = await self.wealth_system.calculate_dynamic_wealth_value(
            group_id, user_data, user_id
        )

        if user_data["coins"] < cost:
            await send_text_reply(
                event, f"金币不足，需要支付赎身费用：{cost:.1f}金币。"
            )
            return

        employer_id = user_data["contracted_by"]
        employer_data = await self.data_manager.get_user_data(group_id, employer_id)

        user_data["coins"] -= cost

        # Update contractor relationship in database
        await self.data_manager.remove_contractor(group_id, employer_id, user_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        trade_config = self.config.get("trade", {})
        redeem_rate = trade_config.get("redeem_return_rate", 0.5)
        compensation = cost * redeem_rate
        employer_data["coins"] += compensation

        # Save employer data
        await self.data_manager.save_user_data(group_id, employer_id, employer_data)

        employer_name = await self._get_user_name_from_platform(event, employer_id)
        await send_text_reply(
            event,
            f"赎身成功，消耗{cost:.1f}金币，重获自由！"
            f"原雇主 {employer_name} 获得了 {compensation:.1f} 金币作为补偿。",
        )

    @filter.regex(r"^(我的信息|签到查询|我的资产)$")
    async def sign_query(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())

        # Check if image card is enabled
        enable_image_card = basic_config.get("enable_image_card", True)

        if not enable_image_card:
            # Send text-only query result with full details
            user_data = await self.data_manager.get_user_data(group_id, user_id)
            user_name = await self._get_user_name_from_platform(event, user_id)
            wealth_level, _ = self.wealth_system.get_wealth_info(user_data)

            from datetime import datetime

            now = datetime.now(SHANGHAI_TZ)

            # Calculate tomorrow income
            income_info = await self.wealth_system.calculate_tomorrow_income(
                user_data, group_id
            )

            # Format user info text
            total_wealth = user_data["coins"] + user_data["bank"]
            info_text = f"【{user_name} 的资产信息】\n"
            info_text += f"👤 用户ID: {user_id}\n"
            info_text += f"💎 财富等级: {wealth_level}\n"
            info_text += (
                f"📊 状态: {'受雇' if user_data['contracted_by'] else '自由'}\n"
            )
            info_text += f"📅 查询时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"

            info_text += f"【资产状况】\n"
            info_text += f"💰 现金: {user_data['coins']:.1f} 金币\n"
            info_text += f"🏦 银行存款: {user_data['bank']:.1f} 金币\n"
            info_text += f"💎 总资产: {total_wealth:.1f} 金币\n"
            info_text += f"🔥 连续签到: {user_data['consecutive']} 天\n\n"

            info_text += f"【明日预计收入】\n"
            info_text += f"💵 基础收益: {income_info['base']:.1f} 金币\n"
            if income_info["contract_bonus"] > 0:
                info_text += f"👥 雇员加成: {income_info['contract_bonus']:.1f} 金币\n"
            if income_info["consecutive_bonus"] > 0:
                info_text += (
                    f"🔥 连续签到加成: {income_info['consecutive_bonus']:.1f} 金币\n"
                )
            info_text += f"🏦 银行利息: {income_info['interest']:.1f} 金币\n"
            info_text += f"📊 明日预计总收入: {income_info['total']:.1f} 金币\n\n"

            # Add contractor info
            if user_data["contractors"]:
                contractor_names = []
                for cid in user_data["contractors"]:
                    cname = await self._get_user_name_from_platform(event, cid)
                    contractor_names.append(cname)
                info_text += f"👥 雇员 ({len(user_data['contractors'])}人): {', '.join(contractor_names)}\n"

            if user_data["contracted_by"]:
                owner_name = await self._get_user_name_from_platform(
                    event, user_data["contracted_by"]
                )
                info_text += f"🔒 雇主: {owner_name}"

            await send_text_reply(event, info_text)
            return

        # Check if there's already a query in progress for this user
        if group_id in self._query_states and user_id in self._query_states[group_id]:
            if self._query_states[group_id][user_id].get("is_generating", False):
                await send_text_reply(event, "正在生成您的信息卡片，请稍候...")
                return

        # Initialize query state
        if group_id not in self._query_states:
            self._query_states[group_id] = {}
        self._query_states[group_id][user_id] = {
            "text_message_id": None,
            "is_generating": True,
        }

        try:
            # Get user data for text version
            user_data = await self.data_manager.get_user_data(group_id, user_id)
            user_name = await self._get_user_name_from_platform(event, user_id)

            # Format user info text
            total_wealth = user_data["coins"] + user_data["bank"]
            info_text = f"【{user_name} 的资产信息】\n"
            info_text += f"💰 现金: {user_data['coins']:.1f} 金币\n"
            info_text += f"🏦 银行存款: {user_data['bank']:.1f} 金币\n"
            info_text += f"💎 总资产: {total_wealth:.1f} 金币\n"

            # Add contractor info
            if user_data["contractors"]:
                contractor_names = []
                for cid in user_data["contractors"]:
                    cname = await self._get_user_name_from_platform(event, cid)
                    contractor_names.append(cname)
                info_text += f"👥 雇员: {', '.join(contractor_names)}\n"

            if user_data["contracted_by"]:
                owner_name = await self._get_user_name_from_platform(
                    event, user_data["contracted_by"]
                )
                info_text += f"🔒 雇主: {owner_name}\n"

            # Add consecutive sign-in info
            if user_data["consecutive"] > 0:
                info_text += f"📅 连续签到: {user_data['consecutive']} 天\n"

            info_text += "\n正在生成图片卡片，请稍候..."

            # Send text message and get message ID
            text_message_id = await send_text_reply(event, info_text)
            if text_message_id:
                self._query_states[group_id][user_id]["text_message_id"] = (
                    text_message_id
                )

            # Generate image asynchronously
            bg_api_url = basic_config.get("bg_api_url", "https://t.alcy.cc/ycy")
            render_data = await self.card_renderer.generate_query_card(
                event, bg_api_url=bg_api_url
            )

            try:
                html_url = await self.html_render(
                    self.card_renderer.get_template(), render_data
                )
                if html_url:
                    # Recall text message and send image
                    if text_message_id:
                        await recall_message(event, text_message_id)
                    await send_image_reply(event, html_url)
                else:
                    # Image generation failed, text message remains
                    logger.warning("图片生成失败，保留文字消息")
            except Exception as e:
                logger.error(f"HTML 渲染失败: {e}")
                # Image generation failed, text message remains with info

        finally:
            # Clean up query state
            if (
                group_id in self._query_states
                and user_id in self._query_states[group_id]
            ):
                del self._query_states[group_id][user_id]
                # Clean up empty group
                if not self._query_states[group_id]:
                    del self._query_states[group_id]

    @filter.regex(r"^(存款|存钱)\s+([0-9.]+)$")
    async def deposit(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # Parse amount from message
        message_str = event.message_str
        match = __import__("re").match(r"^(存款|存钱)\s+([0-9.]+)$", message_str)
        if not match:
            await send_text_reply(event, "金额格式不正确，请使用：存款 <数字>")
            return

        try:
            amount = float(match.group(2))
            if amount <= 0:
                await send_text_reply(event, "存款金额必须大于0。")
                return
        except ValueError:
            await send_text_reply(event, "金额格式不正确，请使用：存款 <数字>")
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        if amount > user_data["coins"]:
            await send_text_reply(
                event, f"现金不足，当前现金：{user_data['coins']:.1f}"
            )
            return

        user_data["coins"] -= amount
        user_data["bank"] += amount

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        await send_text_reply(event, f"成功存入 {amount:.1f} 金币到银行。")

    @filter.regex(r"^(取款|取钱)\s+([0-9.]+)$")
    async def withdraw(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # Parse amount from message
        message_str = event.message_str
        match = __import__("re").match(r"^(取款|取钱)\s+([0-9.]+)$", message_str)
        if not match:
            await send_text_reply(event, "金额格式不正确，请使用：取款 <数字>")
            return

        try:
            amount = float(match.group(2))
            if amount <= 0:
                await send_text_reply(event, "取款金额必须大于0。")
                return
        except ValueError:
            await send_text_reply(event, "金额格式不正确，请使用：取款 <数字>")
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        if amount > user_data["bank"]:
            await send_text_reply(
                event, f"银行存款不足，当前存款：{user_data['bank']:.1f}"
            )
            return

        user_data["bank"] -= amount
        user_data["coins"] += amount

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        await send_text_reply(event, f"成功取出 {amount:.1f} 金币。")

    async def terminate(self):
        """插件终止时关闭资源"""
        await self.checkin_reward_service.stop()
        await self.image_cache.close()
        await self.data_manager.close()

    async def _get_user_name_from_platform(
        self, event: AstrMessageEvent, target_id: str
    ) -> str:
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    resp = await client.api.call_action(
                        "get_group_member_info",
                        group_id=event.message_obj.group_id,
                        user_id=int(target_id),
                        no_cache=True,
                    )
                    return resp.get("card") or resp.get(
                        "nickname", f"用户{target_id[-4:]}"
                    )
            except Exception as e:
                logger.warning(f"通过API获取用户信息({target_id})失败: {e}")
        return f"用户{target_id[-4:]}"
