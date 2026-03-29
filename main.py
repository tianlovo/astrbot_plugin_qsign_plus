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
from .utils.helpers import get_target_at_user, is_at_bot, is_group_allowed

PLUGIN_DIR = os.path.dirname(__file__)
SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")


@register(
    "astrbot_plugin_qsign_plus",
    "tianluoqaq",
    "二次元签到插件",
    "2.4.0",
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

        # Load data to cache
        asyncio.create_task(self.data_manager.init())

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

        if not target_id:
            yield event.plain_result("请使用@指定要购买的对象。")
            return

        user_id = str(event.get_sender_id())

        if user_id == target_id:
            yield event.plain_result("您不能购买自己。")
            return

        # 检查目标用户角色
        target_role = await self._get_user_role(event, target_id)
        admin_config = self.config.get("admin", {})

        # 群主默认不可被购买（除非配置允许）
        if target_role == "owner":
            owner_can_be_purchased = admin_config.get("owner_can_be_purchased", False)
            if not owner_can_be_purchased:
                yield event.plain_result("群主不可被购买！")
                return

        # 获取基础身价
        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        if len(employer_data["contractors"]) >= 3:
            yield event.plain_result("已达到最大雇佣数量（3人）。")
            return

        base_cost = await self.wealth_system.calculate_dynamic_wealth_value(
            group_id, target_data, target_id
        )

        # 管理员和群主享受价格加成
        if target_role in ["owner", "admin"]:
            admin_bonus = admin_config.get("admin_price_bonus", 0.5)
            base_cost *= (1 + admin_bonus)

        total_cost = base_cost
        original_owner_id = target_data.get("contracted_by")

        if original_owner_id:
            if original_owner_id == user_id:
                yield event.plain_result("该用户已经是您的雇员了。")
                return

            trade_config = self.config.get("trade", {})
            takeover_rate = trade_config.get("takeover_fee_rate", 0.1)
            extra_cost = base_cost * takeover_rate
            total_cost += extra_cost
            compensation = total_cost

            if employer_data["coins"] < total_cost:
                yield event.plain_result(
                    f"现金不足，恶意收购需要支付 {total_cost:.1f} 金币（含{takeover_rate * 100}%额外费用）。"
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

            self.data_manager.increment_purchase_count(target_id)
            await self.data_manager.save_purchase_data()

            target_name = await self._get_user_name_from_platform(event, target_id)
            original_owner_name = await self._get_user_name_from_platform(
                event, original_owner_id
            )
            yield event.plain_result(
                f"恶意收购成功！您花费 {total_cost:.1f} 金币从 {original_owner_name} 手中抢走了 {target_name}。"
                f"原雇主获得了全部转让费 {compensation:.1f} 金币。"
            )
            return

        if employer_data["coins"] < total_cost:
            yield event.plain_result(
                f"现金不足，雇佣需要支付目标身价：{total_cost:.1f}金币。"
            )
            return

        employer_data["coins"] -= total_cost

        # Update contractor relationship in database
        await self.data_manager.add_contractor(group_id, user_id, target_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, employer_data)

        self.data_manager.increment_purchase_count(target_id)
        await self.data_manager.save_purchase_data()

        target_name = await self._get_user_name_from_platform(event, target_id)
        yield event.plain_result(f"成功雇佣 {target_name}，消耗{total_cost:.1f}金币。")

    @filter.regex(r"^出售")
    async def sell(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        target_id = get_target_at_user(event)

        if not target_id:
            yield event.plain_result("请使用@指定要出售的对象。")
            return

        user_id = str(event.get_sender_id())

        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        if target_id not in employer_data["contractors"]:
            yield event.plain_result("该用户不在你的雇员列表中。")
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
        yield event.plain_result(
            f"成功解雇 {target_name}，获得补偿金{sell_price:.1f}金币。"
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
                yield event.plain_result("你今天已经签到过了，明天再来吧。")
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
            _,
            _,
            _,
            _,
        ) = await self.wealth_system.calculate_sign_income(
            user_data, group_id, is_penalized
        )

        user_data["coins"] += earned
        user_data["last_sign"] = now.replace(tzinfo=None).isoformat()

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        # Generate card
        basic_config = self.config.get("basic", {})
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
                yield event.image_result(html_url)
            else:
                yield event.plain_result("签到成功！但图片生成失败。")
        except Exception as e:
            logger.error(f"HTML 渲染失败: {e}")
            yield event.plain_result("签到成功！但图片生成失败。")

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
            yield event.plain_result("本群暂无签到数据，无法生成排行榜。")
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

        yield event.plain_result(leaderboard_str.strip())

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
            yield event.plain_result("您是自由身，无需赎身。")
            return

        cost = await self.wealth_system.calculate_dynamic_wealth_value(
            group_id, user_data, user_id
        )

        if user_data["coins"] < cost:
            yield event.plain_result(f"金币不足，需要支付赎身费用：{cost:.1f}金币。")
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
        yield event.plain_result(
            f"赎身成功，消耗{cost:.1f}金币，重获自由！"
            f"原雇主 {employer_name} 获得了 {compensation:.1f} 金币作为补偿。"
        )

    @filter.regex(r"^(我的信息|签到查询|我的资产)$")
    async def sign_query(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        bg_api_url = basic_config.get("bg_api_url", "https://t.alcy.cc/ycy")
        render_data = await self.card_renderer.generate_query_card(
            event, bg_api_url=bg_api_url
        )

        try:
            html_url = await self.html_render(
                self.card_renderer.get_template(), render_data
            )
            if html_url:
                yield event.image_result(html_url)
            else:
                yield event.plain_result("查询失败，图片生成服务出现问题。")
        except Exception as e:
            logger.error(f"HTML 渲染失败: {e}")
            yield event.plain_result("查询失败，图片生成服务出现问题。")

    @filter.regex(r"^(存款|存钱)\s+([0-9.]+)$")
    async def deposit(self, event: AstrMessageEvent, amount_str: str):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        try:
            amount = float(amount_str)
            if amount <= 0:
                yield event.plain_result("存款金额必须大于0。")
                return
        except ValueError:
            yield event.plain_result("金额格式不正确，请使用：存款 <数字>")
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        if amount > user_data["coins"]:
            yield event.plain_result(f"现金不足，当前现金：{user_data['coins']:.1f}")
            return

        user_data["coins"] -= amount
        user_data["bank"] += amount

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        yield event.plain_result(f"成功存入 {amount:.1f} 金币到银行。")

    @filter.regex(r"^(取款|取钱)\s+([0-9.]+)$")
    async def withdraw(self, event: AstrMessageEvent, amount_str: str):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        try:
            amount = float(amount_str)
            if amount <= 0:
                yield event.plain_result("取款金额必须大于0。")
                return
        except ValueError:
            yield event.plain_result("金额格式不正确，请使用：取款 <数字>")
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        if amount > user_data["bank"]:
            yield event.plain_result(f"银行存款不足，当前存款：{user_data['bank']:.1f}")
            return

        user_data["bank"] -= amount
        user_data["coins"] += amount

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        yield event.plain_result(f"成功取出 {amount:.1f} 金币。")

    async def terminate(self):
        """插件终止时关闭资源"""
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
