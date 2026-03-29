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
    "astrbot_plugin_sign",
    "tianluoqaq",
    "二次元签到插件",
    "2.3.0",
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
        asyncio.create_task(self.data_manager.load_all_data())

    async def _is_user_admin(self, event: AstrMessageEvent, user_id: str) -> bool:
        """检查用户是否为群主或管理员

        Args:
            event: 消息事件
            user_id: 用户ID

        Returns:
            是否为群主或管理员
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
                    role = resp.get("role", "member")
                    return role in ["owner", "admin"]
            except Exception as e:
                logger.warning(f"检查用户权限失败({user_id}): {e}")
        return False

    @filter.regex(r"^购买")
    async def purchase(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
            return

        target_id = get_target_at_user(event)

        if not target_id:
            yield event.plain_result("请使用@指定要购买的对象。")
            return

        user_id = str(event.get_sender_id())

        if user_id == target_id:
            yield event.plain_result("您不能购买自己。")
            return

        # 检查目标用户是否为群主/管理员
        if await self._is_user_admin(event, target_id):
            admin_bonus = self.config.get("admin_price_bonus", 0.5)
            yield event.plain_result(
                f"无法购买群主或管理员！管理员身价加成 {admin_bonus * 100}%，不可被购买。"
            )
            return

        employer_data = self.data_manager.get_user_data(group_id, user_id)
        target_data = self.data_manager.get_user_data(group_id, target_id)

        if len(employer_data["contractors"]) >= 3:
            yield event.plain_result("已达到最大雇佣数量（3人）。")
            return

        base_cost = self.wealth_system.calculate_dynamic_wealth_value(
            target_data, target_id
        )
        total_cost = base_cost
        original_owner_id = target_data.get("contracted_by")

        if original_owner_id:
            if original_owner_id == user_id:
                yield event.plain_result("该用户已经是您的雇员了。")
                return

            takeover_rate = self.config.get("takeover_fee_rate", 0.1)
            extra_cost = base_cost * takeover_rate
            total_cost += extra_cost
            compensation = total_cost

            if employer_data["coins"] < total_cost:
                yield event.plain_result(
                    f"现金不足，恶意收购需要支付 {total_cost:.1f} 金币（含{takeover_rate * 100}%额外费用）。"
                )
                return

            original_owner_data = self.data_manager.get_user_data(
                group_id, original_owner_id
            )
            if target_id in original_owner_data["contractors"]:
                original_owner_data["contractors"].remove(target_id)

            original_owner_data["coins"] += compensation
            employer_data["coins"] -= total_cost

            employer_data["contractors"].append(target_id)
            target_data["contracted_by"] = user_id

            self.data_manager.increment_purchase_count(target_id)

            await self.data_manager.save_sign_data()
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
        employer_data["contractors"].append(target_id)
        target_data["contracted_by"] = user_id

        self.data_manager.increment_purchase_count(target_id)
        await self.data_manager.save_sign_data()
        await self.data_manager.save_purchase_data()

        target_name = await self._get_user_name_from_platform(event, target_id)
        yield event.plain_result(f"成功雇佣 {target_name}，消耗{total_cost:.1f}金币。")

    @filter.regex(r"^出售")
    async def sell(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
            return

        target_id = get_target_at_user(event)

        if not target_id:
            yield event.plain_result("请使用@指定要出售的对象。")
            return

        user_id = str(event.get_sender_id())

        employer_data = self.data_manager.get_user_data(group_id, user_id)
        target_data = self.data_manager.get_user_data(group_id, target_id)
        if target_id not in employer_data["contractors"]:
            yield event.plain_result("该用户不在你的雇员列表中。")
            return

        sell_rate = self.config.get("sell_return_rate", 0.8)
        sell_price = (
            self.wealth_system.calculate_dynamic_wealth_value(target_data, target_id)
            * sell_rate
        )
        employer_data["coins"] += sell_price
        employer_data["contractors"].remove(target_id)
        target_data["contracted_by"] = None
        await self.data_manager.save_sign_data()
        target_name = await self._get_user_name_from_platform(event, target_id)
        yield event.plain_result(
            f"成功解雇 {target_name}，获得补偿金{sell_price:.1f}金币。"
        )

    @filter.regex(r"^签到$")
    async def sign_in(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())
        user_data = self.data_manager.get_user_data(group_id, user_id)
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
        ) = self.wealth_system.calculate_sign_income(user_data, group_id, is_penalized)

        user_data["coins"] += earned
        user_data["last_sign"] = now.replace(tzinfo=None).isoformat()
        await self.data_manager.save_sign_data()

        # Generate card
        bg_api_url = self.config.get("bg_api_url", "https://t.alcy.cc/ycy")
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
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
            return

        group_data = self.data_manager.sign_data.get(group_id)
        if not group_data:
            yield event.plain_result("本群暂无签到数据，无法生成排行榜。")
            return
        all_users_wealth = []
        for user_id, user_data in group_data.items():
            total_wealth = user_data.get("coins", 0.0) + user_data.get("bank", 0.0)
            all_users_wealth.append((user_id, total_wealth))
        sorted_users = sorted(all_users_wealth, key=lambda item: item[1], reverse=True)
        top_10_users = sorted_users[:10]
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
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())
        user_data = self.data_manager.get_user_data(group_id, user_id)
        if not user_data["contracted_by"]:
            yield event.plain_result("您是自由身，无需赎身。")
            return

        cost = self.wealth_system.calculate_dynamic_wealth_value(user_data, user_id)
        if user_data["coins"] < cost:
            yield event.plain_result(f"金币不足，需要支付赎身费用：{cost:.1f}金币。")
            return

        employer_id = user_data["contracted_by"]
        employer_data = self.data_manager.get_user_data(group_id, employer_id)

        user_data["coins"] -= cost
        if user_id in employer_data["contractors"]:
            employer_data["contractors"].remove(user_id)
        user_data["contracted_by"] = None

        redeem_rate = self.config.get("redeem_return_rate", 0.5)
        compensation = cost * redeem_rate
        employer_data["coins"] += compensation

        await self.data_manager.save_sign_data()

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
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
            return

        bg_api_url = self.config.get("bg_api_url", "https://t.alcy.cc/ycy")
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
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
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
        user_data = self.data_manager.get_user_data(group_id, user_id)
        if amount > user_data["coins"]:
            yield event.plain_result(f"现金不足，当前现金：{user_data['coins']:.1f}")
            return
        user_data["coins"] -= amount
        user_data["bank"] += amount
        await self.data_manager.save_sign_data()
        yield event.plain_result(f"成功存入 {amount:.1f} 金币到银行。")

    @filter.regex(r"^(取款|取钱)\s+([0-9.]+)$")
    async def withdraw(self, event: AstrMessageEvent, amount_str: str):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        if not is_group_allowed(group_id, self.config.get("enabled_groups", [])):
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
        user_data = self.data_manager.get_user_data(group_id, user_id)
        if amount > user_data["bank"]:
            yield event.plain_result(f"银行存款不足，当前存款：{user_data['bank']:.1f}")
            return
        user_data["bank"] -= amount
        user_data["coins"] += amount
        await self.data_manager.save_sign_data()
        yield event.plain_result(f"成功取出 {amount:.1f} 金币。")

    async def terminate(self):
        await self.image_cache.close()

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
