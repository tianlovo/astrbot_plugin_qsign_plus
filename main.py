import asyncio
import os
import random
from datetime import datetime

import pytz

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .core.data_manager import DataManager
from .core.exchange_rate import ExchangeRateCalculator, ExchangeRateHistory
from .core.owner_currency import OwnerCurrencyManager
from .core.wealth_calculator import WealthCalculator
from .core.wealth_system import WealthSystem
from .services.card_renderer import CardRenderer
from .services.exchange_rate_service import ExchangeRateService
from .services.image_cache import ImageCacheService
from .utils.helpers import (
    get_first_at_user,
    get_plain_text_from_message,
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
    "2.15.1",
    "https://github.com/tianlovo/astrbot_plugin_qsign_plus",
)
class ContractSystem(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # Initialize services
        self.data_manager = DataManager(PLUGIN_DIR)
        self.wealth_system = WealthSystem(self.data_manager, config)
        self.wealth_calculator = WealthCalculator(self.data_manager, config)
        self.image_cache = ImageCacheService()
        self.card_renderer = CardRenderer(
            PLUGIN_DIR,
            self.data_manager,
            self.wealth_system,
            self.image_cache,
        )

        # Query state management: {group_id: {user_id: {"text_message_id": str, "is_generating": bool}}}
        self._query_states: dict[str, dict[str, dict]] = {}

        # Admin cache: {group_id: {"admin_ids": list[str], "expire_time": timestamp}}
        self._admin_cache: dict[str, dict] = {}
        self._admin_cache_ttl = 300  # 缓存有效期5分钟

        # Load data to cache
        asyncio.create_task(self.data_manager.init())

        # 同步兑换码配置到数据库
        asyncio.create_task(self._sync_redeem_codes())

        # 初始化股市系统
        stock_config = config.get("stock_market", {})
        self.exchange_calculator = ExchangeRateCalculator(
            volatility=stock_config.get("volatility", 0.02),
            mean_reversion_speed=stock_config.get("mean_reversion_speed", 0.1),
            mean_reversion_level=stock_config.get("mean_reversion_level", 1.0),
            trend_mode=stock_config.get("trend_mode", "off"),
            trend_direction=stock_config.get("trend_direction", 0),
            trend_bull_probability=stock_config.get("trend_bull_probability", 30),
            trend_bear_probability=stock_config.get("trend_bear_probability", 30),
            trend_range_probability=stock_config.get("trend_range_probability", 40),
            trend_min_days=stock_config.get("trend_min_days", 3),
            trend_max_days=stock_config.get("trend_max_days", 10),
            trend_min_strength=stock_config.get("trend_min_strength", 0.01),
            trend_max_strength=stock_config.get("trend_max_strength", 0.05),
        )
        self.exchange_history = ExchangeRateHistory(db=self.data_manager.db)
        self.owner_currency_manager = OwnerCurrencyManager(
            self.data_manager, self.exchange_calculator
        )

        # 初始化并启动汇率更新后台服务
        self.exchange_rate_service = ExchangeRateService(
            data_manager=self.data_manager,
            exchange_calculator=self.exchange_calculator,
            exchange_history=self.exchange_history,
            config=config,
        )
        asyncio.create_task(self.exchange_rate_service.start())

    async def _sync_redeem_codes(self):
        """同步兑换码配置到数据库"""
        try:
            # 等待数据库初始化完成
            while not self.data_manager.is_db_initialized():
                await asyncio.sleep(0.5)

            # template_list 类型返回的是列表
            redeem_codes = self.config.get("redeem_codes", [])
            if not isinstance(redeem_codes, list):
                redeem_codes = []
            await self.data_manager.sync_redeem_codes_from_config(redeem_codes)
        except Exception as e:
            logger.error(f"同步兑换码配置失败: {e}")

    def _get_currency_name(self) -> str:
        """获取货币名称

        Returns:
            货币名称，默认为"金币"
        """
        basic_config = self.config.get("basic", {})
        return basic_config.get("currency_name", "金币")

    def _is_maintenance_mode(self) -> bool:
        """检查是否处于维护模式

        Returns:
            是否处于维护模式
        """
        basic_config = self.config.get("basic", {})
        return basic_config.get("maintenance_mode", False)

    def _is_super_admin(self, user_id: str) -> bool:
        """检查用户是否为超级管理员

        Args:
            user_id: 用户ID

        Returns:
            是否为超级管理员
        """
        admin_config = self.config.get("admin", {})
        super_admins = admin_config.get("super_admins", [])
        return str(user_id) in [str(admin) for admin in super_admins]

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
        """检查用户是否为群主或管理员（缓存优先）

        Args:
            event: 消息事件
            user_id: 用户ID

        Returns:
            是否为群主或管理员
        """
        import time

        group_id = str(event.message_obj.group_id)
        now = time.time()

        # 检查缓存是否有效
        if group_id in self._admin_cache:
            cache_entry = self._admin_cache[group_id]
            if cache_entry["expire_time"] > now:
                # 缓存有效，直接使用缓存判断
                logger.debug(f"[AdminCheck] 使用缓存判断用户 {user_id} 是否为管理员")
                return str(user_id) in cache_entry["admin_ids"]

        # 缓存无效或不存在，获取管理员列表（会自动更新缓存）
        try:
            admin_ids = await self._get_group_admin_ids(event)
            return str(user_id) in admin_ids
        except Exception as e:
            # API 调用失败，尝试使用过期缓存
            logger.warning(f"[AdminCheck] 获取管理员列表失败: {e}")
            if group_id in self._admin_cache:
                logger.info(f"[AdminCheck] 使用过期缓存判断用户 {user_id}")
                return str(user_id) in self._admin_cache[group_id]["admin_ids"]
            # 没有缓存，回退到直接获取用户角色
            logger.warning("[AdminCheck] 无可用缓存，直接获取用户角色")
            role = await self._get_user_role(event, user_id)
            return role in ["owner", "admin"]

    async def _get_group_admin_ids(self, event: AstrMessageEvent) -> list[str]:
        """获取群管理员列表（带缓存）

        Args:
            event: 消息事件

        Returns:
            管理员ID列表（包括群主和管理员）
        """
        import time

        group_id = str(event.message_obj.group_id)
        now = time.time()

        # 检查缓存是否有效
        if group_id in self._admin_cache:
            cache_entry = self._admin_cache[group_id]
            if cache_entry["expire_time"] > now:
                logger.debug(f"[AdminCache] 使用缓存的管理员列表，群: {group_id}")
                return cache_entry["admin_ids"]

        # 缓存无效或不存在，重新获取
        admin_ids = []
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    resp = await client.api.call_action(
                        "get_group_member_list",
                        group_id=event.message_obj.group_id,
                    )
                    for member in resp:
                        role = member.get("role", "member")
                        if role in ["owner", "admin"]:
                            admin_ids.append(str(member.get("user_id", "")))

                    # 更新缓存
                    self._admin_cache[group_id] = {
                        "admin_ids": admin_ids,
                        "expire_time": now + self._admin_cache_ttl,
                    }
                    logger.info(
                        f"[AdminCache] 更新管理员列表缓存，群: {group_id}，管理员数: {len(admin_ids)}"
                    )
            except Exception as e:
                logger.warning(f"获取群管理员列表失败: {e}")
                # 如果获取失败但有缓存，使用过期缓存作为备选
                if group_id in self._admin_cache:
                    logger.info(f"[AdminCache] 使用过期缓存作为备选，群: {group_id}")
                    return self._admin_cache[group_id]["admin_ids"]

        return admin_ids

    @filter.regex(r"^购买")
    async def purchase(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
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

        # 获取用户数据
        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        # 检查目标是否是用户的雇主
        if employer_data.get("contracted_by") == target_id:
            await send_text_reply(event, "您当前被该用户雇佣，请先赎身。")
            return

        # 检查目标用户角色
        target_role = await self._get_user_role(event, target_id)

        # 群主不可被雇佣，请通过股市系统交易群主货币
        # 不发送消息，让 buy_owner_currency 方法处理
        if target_role == "owner":
            return

        # 检查雇佣数量限制
        max_contractors = await self.wealth_system.get_max_contractor_limit(
            group_id, employer_data, user_id
        )
        current_contractors = len(employer_data["contractors"])
        if max_contractors > 0 and current_contractors >= max_contractors:
            await send_text_reply(
                event,
                f"已达到最大雇佣数量（{current_contractors}人）。提升财富等级可增加雇佣上限。",
            )
            return

        # 使用身价计算器计算购买价格
        base_cost = await self.wealth_calculator.calculate_purchase_price(
            group_id, target_data, target_id, target_role
        )

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
                currency = self._get_currency_name()
                await send_text_reply(
                    event,
                    f"现金不足，恶意收购需要支付 {total_cost:.1f} {currency}（含{takeover_rate * 100}%额外费用）。",
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

            # 记录购买价格
            await self.data_manager.record_purchase(
                group_id, user_id, target_id, total_cost
            )

            target_name = await self._get_user_name_from_platform(event, target_id)
            original_owner_name = await self._get_user_name_from_platform(
                event, original_owner_id
            )
            currency = self._get_currency_name()
            await send_text_reply(
                event,
                f"恶意收购成功！您花费 {total_cost:.1f} {currency}从 {original_owner_name} 手中抢走了 {target_name}。"
                f"原雇主获得了全部转让费 {compensation:.1f} {currency}。",
            )
            return

        if employer_data["coins"] < total_cost:
            currency = self._get_currency_name()
            await send_text_reply(
                event, f"现金不足，雇佣需要支付目标身价：{total_cost:.1f}{currency}。"
            )
            return

        employer_data["coins"] -= total_cost

        # Update contractor relationship in database
        await self.data_manager.add_contractor(group_id, user_id, target_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, employer_data)

        await self.data_manager.increment_purchase_count(target_id)

        # 记录购买价格
        await self.data_manager.record_purchase(
            group_id, user_id, target_id, total_cost
        )

        target_name = await self._get_user_name_from_platform(event, target_id)
        currency = self._get_currency_name()
        await send_text_reply(
            event, f"成功雇佣 {target_name}，消耗{total_cost:.1f}{currency}。"
        )

    @filter.regex(r"^详细价格\s*")
    async def detailed_price(self, event: AstrMessageEvent):
        """查询购买指定成员的详细价格（调试模式，仅维护模式可用）"""
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式 - 详细价格指令仅在维护模式下可用
        if not self._is_maintenance_mode():
            await send_text_reply(event, "该指令仅在系统维护模式下可用。")
            return

        user_id = str(event.get_sender_id())

        # 获取目标用户（支持at和空格可选）
        target_id = get_target_at_user(event)
        if not target_id:
            target_id = get_first_at_user(event)

        currency = self._get_currency_name()

        # 如果没有at任何人，或at自己，查询自己的详细身价
        if not target_id or target_id == user_id:
            target_id = user_id

        # 获取目标用户角色
        target_role = await self._get_user_role(event, target_id)

        # 群主不可被雇佣，请通过股市系统交易群主货币
        if target_role == "owner":
            await send_text_reply(event, "群主不可被雇佣，请通过股市系统交易群主货币。")
            return

        # 获取目标用户数据
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        # 获取身价详细分解
        wealth_detailed = await self.wealth_calculator.calculate_wealth_value_detailed(
            group_id, target_data, target_id
        )

        # 获取详细价格分解
        price_detailed = await self.wealth_calculator.calculate_purchase_price_detailed(
            group_id, target_data, target_id, target_role
        )

        target_name = await self._get_user_name_from_platform(event, target_id)

        # 构建详细价格信息
        role_text = ""
        if target_role == "owner":
            role_text = "（群主）"
        elif target_role == "admin":
            role_text = "（管理员）"

        info_text = f"💰 {target_name}{role_text} 的详细价格信息\n"
        info_text += "=" * 40 + "\n\n"

        # ===== 身价详细拆解 =====
        info_text += "【身价详细拆解】\n"
        info_text += f"  现金: {wealth_detailed['coins']:.1f} {currency}\n"
        info_text += f"  银行存款: {wealth_detailed['bank']:.1f} {currency}\n"
        info_text += f"  基础身价(现金+银行): {wealth_detailed['base_wealth']:.1f} {currency}\n\n"

        # 雇员潜在价值详细拆解
        if wealth_detailed["contractor_count"] > 0:
            info_text += f"  雇员数量: {wealth_detailed['contractor_count']} 人\n"
            info_text += f"  雇员总潜在价值: {wealth_detailed['total_contractor_value']:.1f} {currency}\n\n"

            info_text += "  每个雇员的潜在价值:\n"
            for i, contractor in enumerate(wealth_detailed["contractor_details"], 1):
                contractor_name = await self._get_user_name_from_platform(
                    event, contractor["contractor_id"]
                )
                info_text += f"    {i}. {contractor_name}\n"
                info_text += f"       当前身价: {contractor['contractor_value']:.1f} {currency}\n"
                info_text += (
                    f"       出售获得: {contractor['sell_value']:.1f} {currency} "
                )
                info_text += f"({contractor['contractor_value']:.1f} × {contractor['sell_return_rate'] * 100:.0f}%)\n"
                info_text += (
                    f"       赎身返还: {contractor['redeem_value']:.1f} {currency} "
                )
                info_text += f"({contractor['purchase_price']:.1f} × {contractor['redeem_return_rate'] * 100:.0f}%)\n"
                info_text += f"       → 潜在价值: {contractor['potential_value']:.1f} {currency} "
                info_text += "(max(出售, 赎身))\n\n"
        else:
            info_text += "  雇员数量: 0 人\n\n"

        info_text += f"  总身价: {wealth_detailed['total_wealth']:.1f} {currency}\n"
        info_text += f"  (基础身价 {wealth_detailed['base_wealth']:.1f} + 雇员价值 {wealth_detailed['total_contractor_value']:.1f})\n\n"

        info_text += "-" * 40 + "\n\n"

        # ===== 价格计算过程 =====
        info_text += "【价格计算过程】\n"
        info_text += f"  财富等级: {price_detailed['wealth_level']}\n"
        info_text += f"  契约等级: {price_detailed['contract_level']}\n\n"

        info_text += f"  1. 当前身价: {price_detailed['wealth_value']:.1f} {currency}\n"
        info_text += f"  2. 契约加成: +{price_detailed['contract_level']} × {price_detailed['price_bonus_rate'] * 100:.0f}%\n"
        info_text += (
            f"  3. 动态身价: {price_detailed['dynamic_wealth']:.1f} {currency}\n"
        )
        info_text += f"     ({price_detailed['wealth_value']:.1f} × (1 + {price_detailed['contract_level']} × {price_detailed['price_bonus_rate']:.2f}))\n\n"

        info_text += "【价格调整】\n"
        if price_detailed["min_price_applied"]:
            info_text += f"  4. 最低价格限制: {price_detailed['min_purchase_price']:.1f} {currency}\n"
            info_text += f"     (动态身价 {price_detailed['dynamic_wealth']:.1f} 低于最低价格，已调整)\n"
        else:
            info_text += "  4. 最低价格限制: 未触发\n"
            info_text += f"     (动态身价 {price_detailed['dynamic_wealth']:.1f} >= 最低价格 {price_detailed['min_purchase_price']:.1f})\n"

        if price_detailed["admin_bonus_applied"]:
            info_text += (
                f"  5. 管理员加成: +{price_detailed['admin_bonus_rate'] * 100:.0f}%\n"
            )
            info_text += (
                f"     (+{price_detailed['admin_bonus_amount']:.1f} {currency})\n"
            )
        else:
            info_text += "  5. 管理员加成: 无\n"

        info_text += "\n" + "=" * 40 + "\n"
        info_text += f"【最终购买价格】{price_detailed['final_price']:.1f} {currency}\n"

        await send_text_reply(event, info_text)

    @filter.regex(r"^价格\s*")
    async def price(self, event: AstrMessageEvent):
        """查询购买指定成员的价格，或查询自己的身价"""
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())

        # 获取目标用户（支持at和空格可选）
        target_id = get_target_at_user(event)
        if not target_id:
            target_id = get_first_at_user(event)

        currency = self._get_currency_name()

        # 如果没有at任何人，或at自己，查询自己的身价
        if not target_id or target_id == user_id:
            user_data = await self.data_manager.get_user_data(group_id, user_id)

            # 获取自己的角色（用于显示管理员加成）
            my_role = await self._get_user_role(event, user_id)

            # 使用身价计算器计算购买价格（包含管理员加成）
            display_price = await self.wealth_calculator.calculate_purchase_price(
                group_id, user_data, user_id, my_role
            )

            role_text = ""
            if my_role == "owner":
                role_text = "（群主身份，身价加成）"
            elif my_role == "admin":
                role_text = "（管理员身份，身价加成）"

            await send_text_reply(
                event,
                f"💰 您的身价信息{role_text}\n身价: {display_price:.1f} {currency}",
            )
            return

        # 获取目标用户角色
        target_role = await self._get_user_role(event, target_id)

        # 群主不可被雇佣，请通过股市系统交易群主货币
        if target_role == "owner":
            await send_text_reply(event, "群主不可被雇佣，请通过股市系统交易群主货币。")
            return

        # 获取用户数据
        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        # 检查是否已经是自己的雇员
        if target_id in employer_data["contractors"]:
            await send_text_reply(event, "该用户已经是您的雇员了。")
            return

        # 使用身价计算器计算购买价格
        base_cost = await self.wealth_calculator.calculate_purchase_price(
            group_id, target_data, target_id, target_role
        )

        total_cost = base_cost
        original_owner_id = target_data.get("contracted_by")

        target_name = await self._get_user_name_from_platform(event, target_id)

        if original_owner_id:
            # 已被雇佣，计算恶意收购价格
            trade_config = self.config.get("trade", {})
            takeover_rate = trade_config.get("takeover_fee_rate", 0.1)
            extra_cost = base_cost * takeover_rate
            total_cost += extra_cost

            original_owner_name = await self._get_user_name_from_platform(
                event, original_owner_id
            )

            await send_text_reply(
                event,
                f"💰 {target_name} 的价格信息\n"
                f"基础身价: {base_cost:.1f} {currency}\n"
                f"当前雇主: {original_owner_name}\n"
                f"恶意收购额外费用: {extra_cost:.1f} {currency} ({takeover_rate * 100}%)\n"
                f"总计需要: {total_cost:.1f} {currency}",
            )
        else:
            # 未被雇佣
            await send_text_reply(
                event,
                f"💰 {target_name} 的价格信息\n"
                f"身价: {total_cost:.1f} {currency}\n"
                f"状态: 自由身，可直接雇佣",
            )

    @filter.regex(r"^出售")
    async def sell(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        target_id = get_target_at_user(event)

        # 如果没有找到非机器人的at，尝试获取第一个at（可能是机器人）
        if not target_id:
            target_id = get_first_at_user(event)

        if not target_id:
            await send_text_reply(event, "请使用@指定要出售的对象。")
            return

        user_id = str(event.get_sender_id())

        # 检查目标是否是群主，如果是则让 sell_owner_currency 处理
        target_role = await self._get_user_role(event, target_id)
        if target_role == "owner":
            return

        employer_data = await self.data_manager.get_user_data(group_id, user_id)
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        if target_id not in employer_data["contractors"]:
            await send_text_reply(event, "该用户不在你的雇员列表中。")
            return

        trade_config = self.config.get("trade", {})
        sell_rate = trade_config.get("sell_return_rate", 0.8)

        # 获取目标用户角色（用于计算管理员价格加成）
        target_role = await self._get_user_role(event, target_id)

        # 使用身价计算器计算出售价格
        base_sell_price = await self.wealth_calculator.calculate_dynamic_wealth_value(
            group_id, target_data, target_id
        )

        # 管理员和群主享受价格加成（与购买时一致）
        if target_role in ["owner", "admin"]:
            admin_config = self.config.get("admin", {})
            admin_bonus = admin_config.get("admin_price_bonus", 0.5)
            base_sell_price *= 1 + admin_bonus

        sell_price = base_sell_price * sell_rate

        employer_data["coins"] += sell_price

        # Update contractor relationship in database
        await self.data_manager.remove_contractor(group_id, user_id, target_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, employer_data)

        target_name = await self._get_user_name_from_platform(event, target_id)
        currency = self._get_currency_name()
        await send_text_reply(
            event, f"成功解雇 {target_name}，获得补偿金{sell_price:.1f}{currency}。"
        )

    @filter.regex(r"^购买")
    async def buy_owner_currency(self, event: AstrMessageEvent):
        """购买群主货币"""
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 获取目标用户（群主）
        target_id = get_target_at_user(event) or get_first_at_user(event)
        if not target_id:
            await send_text_reply(event, "请使用@指定要购买的群主。")
            return

        user_id = str(event.get_sender_id())

        # 检查目标是否是群主
        # 如果不是群主，不发送提示，直接返回（让 purchase 方法处理）
        target_role = await self._get_user_role(event, target_id)
        if target_role != "owner":
            return

        # 检查维护模式（只在确认是群主币购买后才检查）
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        # 解析数量（支持忽略空格，限制三位小数）
        # 从消息中提取纯文本（不包括At组件），避免将QQ号误解析为数量
        message_text = get_plain_text_from_message(event)
        # 移除所有空格
        message_text = message_text.replace(" ", "").replace("\u3000", "")
        import re

        # 严格匹配："购买" + 数字（数字必须在消息末尾）
        # 支持最多3位小数
        amount_match = re.search(r"购买([\d]+(?:\.[\d]{1,3})?)$", message_text)
        if not amount_match:
            await send_text_reply(event, "请指定购买数量，例如：购买 @群主 10.5")
            return

        try:
            amount = float(amount_match.group(1))
            if amount <= 0:
                await send_text_reply(event, "购买数量必须大于0。")
                return
        except ValueError:
            await send_text_reply(event, "无效的购买数量。")
            return

        # 获取当前汇率
        stock_config = self.config.get("stock_market", {})
        current_rate = await self.exchange_history.get_current_rate(group_id)
        if current_rate is None:
            current_rate = stock_config.get("base_exchange_rate", 1.0)
            await self.exchange_history.record_rate(group_id, current_rate)

        # 执行购买
        (
            success,
            message,
            actual_amount,
        ) = await self.owner_currency_manager.buy_currency(
            group_id, user_id, amount, current_rate
        )

        if success:
            # 获取群主昵称
            owner_name = await self._get_user_name_from_platform(event, target_id)
            currency_name = self._get_currency_name()
            currency_unit = self.owner_currency_manager.format_currency_name(owner_name)
            cost = self.exchange_calculator.calculate_buy_cost(amount, current_rate)

            await send_text_reply(
                event,
                f"购买成功！您花费 {cost:.1f} {currency_name} 购买了 {actual_amount:.3f} {currency_unit}\n"
                f"当前汇率: 1 {currency_unit} = {current_rate:.4f} {currency_name}\n"
                f"{self.owner_currency_manager.DISCLAIMER}",
            )
        else:
            await send_text_reply(event, message)

    @filter.regex(r"^出售")
    async def sell_owner_currency(self, event: AstrMessageEvent):
        """出售群主货币"""
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 获取目标用户（群主）
        target_id = get_target_at_user(event) or get_first_at_user(event)
        if not target_id:
            await send_text_reply(event, "请使用@指定要出售的群主。")
            return

        user_id = str(event.get_sender_id())

        # 检查目标是否是群主
        # 如果不是群主，不发送提示，直接返回（让 sell 方法处理）
        target_role = await self._get_user_role(event, target_id)
        if target_role != "owner":
            return

        # 检查维护模式（只在确认是群主币出售后才检查）
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        # 解析数量（支持忽略空格，限制三位小数）
        # 从消息中提取纯文本（不包括At组件），避免将QQ号误解析为数量
        message_text = get_plain_text_from_message(event)
        # 移除所有空格
        message_text = message_text.replace(" ", "").replace("\u3000", "")
        import re

        # 严格匹配："出售" + 数字（数字必须在消息末尾）
        # 支持最多3位小数
        amount_match = re.search(r"出售([\d]+(?:\.[\d]{1,3})?)$", message_text)
        if not amount_match:
            await send_text_reply(event, "请指定出售数量，例如：出售 @群主 10.5")
            return

        try:
            amount = float(amount_match.group(1))
            if amount <= 0:
                await send_text_reply(event, "出售数量必须大于0。")
                return
        except ValueError:
            await send_text_reply(event, "无效的出售数量。")
            return

        # 获取当前汇率
        current_rate = await self.exchange_history.get_current_rate(group_id)
        if current_rate is None:
            stock_config = self.config.get("stock_market", {})
            current_rate = stock_config.get("base_exchange_rate", 1.0)

        # 执行出售
        success, message, revenue = await self.owner_currency_manager.sell_currency(
            group_id, user_id, amount, current_rate
        )

        if success:
            owner_name = await self._get_user_name_from_platform(event, target_id)
            currency_name = self._get_currency_name()
            currency_unit = self.owner_currency_manager.format_currency_name(owner_name)

            await send_text_reply(
                event,
                f"出售成功！您出售了 {amount:.3f} {currency_unit}，获得 {revenue:.1f} {currency_name}\n"
                f"当前汇率: 1 {currency_unit} = {current_rate:.4f} {currency_name}\n"
                f"{self.owner_currency_manager.DISCLAIMER}",
            )
        else:
            await send_text_reply(event, message)

    @filter.regex(r"^汇率")
    async def query_exchange_rate(self, event: AstrMessageEvent):
        """查询汇率历史和当前汇率"""
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 获取群主信息
        owner_id = None
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    resp = await client.api.call_action(
                        "get_group_member_list",
                        group_id=event.message_obj.group_id,
                    )
                    for member in resp:
                        if member.get("role") == "owner":
                            owner_id = str(member.get("user_id"))
                            break
            except Exception as e:
                logger.warning(f"获取群主信息失败: {e}")

        owner_name = "群主"
        if owner_id:
            owner_name = await self._get_user_name_from_platform(event, owner_id)

        currency_name = self._get_currency_name()
        currency_unit = self.owner_currency_manager.format_currency_name(owner_name)

        # 获取当前汇率
        current_rate = await self.exchange_history.get_current_rate(group_id)
        if current_rate is None:
            stock_config = self.config.get("stock_market", {})
            current_rate = stock_config.get("base_exchange_rate", 1.0)

        # 获取最近5次汇率记录（最新在前）
        recent_5 = await self.exchange_history.get_last_n_rates(group_id, limit=5)

        # 获取最近7天每日平均汇率（最新在前）
        daily_avg = await self.exchange_history.get_daily_average_rates(
            group_id, days=7
        )

        # 构建文字版汇率信息
        info_text = f"📊 {currency_unit} 汇率信息\n"
        info_text += "=" * 30 + "\n\n"
        info_text += (
            f"当前汇率: 1 {currency_unit} = {current_rate:.4f} {currency_name}\n\n"
        )

        # 显示最近5次汇率
        if recent_5:
            info_text += "【最近5次汇率】\n"
            for record in recent_5:
                from datetime import datetime

                date_str = datetime.fromtimestamp(record.recorded_at).strftime(
                    "%m-%d %H:%M"
                )
                info_text += f"  {date_str}: {record.rate:.4f}\n"
            info_text += "\n"
        else:
            info_text += "暂无最近汇率数据\n\n"

        # 显示最近7天每日平均汇率
        if daily_avg:
            info_text += "【近7天每日平均汇率】\n"
            for record in daily_avg:
                info_text += f"  {record['date']}: {record['avg_rate']:.4f} (共{record['count']}次)\n"
        else:
            info_text += "暂无每日平均汇率数据\n"

        info_text += f"\n{self.owner_currency_manager.DISCLAIMER}"

        await send_text_reply(event, info_text)

    @filter.regex(r"^give\s*")
    async def give_money(self, event: AstrMessageEvent):
        """超级管理员给指定用户增加现金"""
        # 检查是否为超级管理员
        user_id = str(event.get_sender_id())
        if not self._is_super_admin(user_id):
            # 非超级管理员，静默处理
            return

        group_id = str(event.message_obj.group_id)

        # 获取目标用户（支持at）
        target_id = get_target_at_user(event) or get_first_at_user(event)
        if not target_id:
            await send_text_reply(
                event, "请使用@指定要增加现金的用户。\n用法：give @用户 金额"
            )
            return

        # 解析金额
        message_text = get_plain_text_from_message(event)
        message_text = message_text.replace(" ", "").replace("\u3000", "")
        import re

        amount_match = re.search(r"give.*?[\d]+(?:\.[\d]{1,1})?$", message_text)
        if not amount_match:
            await send_text_reply(event, "请指定金额。\n用法：give @用户 金额")
            return

        try:
            # 提取金额（去掉@用户的QQ号）
            # 从消息中移除所有数字（包括QQ号），然后尝试解析金额
            # 更好的方法是直接匹配最后的数字
            amount_str = re.search(r"([\d]+(?:\.[\d]{1,1})?)$", message_text)
            if not amount_str:
                await send_text_reply(event, "无效的金额。\n用法：give @用户 金额")
                return
            amount = float(amount_str.group(1))
            if amount <= 0:
                await send_text_reply(event, "金额必须大于0。")
                return
        except ValueError:
            await send_text_reply(event, "无效的金额。\n用法：give @用户 金额")
            return

        # 限制金额精度为一位小数
        from .utils.helpers import truncate_decimal

        amount = truncate_decimal(amount, precision=1)

        # 获取目标用户数据并增加现金
        target_data = await self.data_manager.get_user_data(group_id, target_id)
        target_data["coins"] += amount
        await self.data_manager.save_user_data(group_id, target_id, target_data)

        # 获取目标用户名称
        target_name = await self._get_user_name_from_platform(event, target_id)
        currency = self._get_currency_name()

        await send_text_reply(
            event,
            f"✅ 操作成功！\n已为 {target_name} 增加 {amount:.1f} {currency}\n当前现金：{target_data['coins']:.1f} {currency}",
        )

    @filter.regex(r"^take\s*")
    async def take_money(self, event: AstrMessageEvent):
        """超级管理员从指定用户扣除现金"""
        # 检查是否为超级管理员
        user_id = str(event.get_sender_id())
        if not self._is_super_admin(user_id):
            # 非超级管理员，静默处理
            return

        group_id = str(event.message_obj.group_id)

        # 获取目标用户（支持at）
        target_id = get_target_at_user(event) or get_first_at_user(event)
        if not target_id:
            await send_text_reply(
                event, "请使用@指定要扣除现金的用户。\n用法：take @用户 金额"
            )
            return

        # 解析金额
        message_text = get_plain_text_from_message(event)
        message_text = message_text.replace(" ", "").replace("\u3000", "")
        import re

        amount_match = re.search(r"take.*?[\d]+(?:\.[\d]{1,1})?$", message_text)
        if not amount_match:
            await send_text_reply(event, "请指定金额。\n用法：take @用户 金额")
            return

        try:
            amount_str = re.search(r"([\d]+(?:\.[\d]{1,1})?)$", message_text)
            if not amount_str:
                await send_text_reply(event, "无效的金额。\n用法：take @用户 金额")
                return
            amount = float(amount_str.group(1))
            if amount <= 0:
                await send_text_reply(event, "金额必须大于0。")
                return
        except ValueError:
            await send_text_reply(event, "无效的金额。\n用法：take @用户 金额")
            return

        # 限制金额精度为一位小数
        from .utils.helpers import truncate_decimal

        amount = truncate_decimal(amount, precision=1)

        # 获取目标用户数据
        target_data = await self.data_manager.get_user_data(group_id, target_id)

        # 检查用户现金是否足够
        if target_data["coins"] < amount:
            await send_text_reply(
                event,
                f"❌ 操作失败！\n用户现金不足，当前现金：{target_data['coins']:.1f} {self._get_currency_name()}",
            )
            return

        # 扣除现金
        target_data["coins"] -= amount
        await self.data_manager.save_user_data(group_id, target_id, target_data)

        # 获取目标用户名称
        target_name = await self._get_user_name_from_platform(event, target_id)
        currency = self._get_currency_name()

        await send_text_reply(
            event,
            f"✅ 操作成功！\n已从 {target_name} 扣除 {amount:.1f} {currency}\n当前现金：{target_data['coins']:.1f} {currency}",
        )

    @filter.regex(r"^签到$")
    async def sign_in(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
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

        # 获取群管理员列表（用于计算管理员雇员加成）
        admin_ids = await self._get_group_admin_ids(event)

        (
            earned,
            original_earned,
            base_with_bonus,
            contract_bonus,
            consecutive_bonus,
            interest,
        ) = await self.wealth_system.calculate_sign_income(
            user_data, group_id, user_id, is_penalized, admin_ids
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
            # 使用实时身价计算财富等级
            wealth_level, _ = await self.wealth_system.get_wealth_info(
                group_id, user_data, user_id
            )
            currency = self._get_currency_name()

            sign_text = "【签到成功】\n"
            sign_text += f"👤 用户: {user_name}\n"
            sign_text += f"💎 财富等级: {wealth_level}\n"
            sign_text += f"📊 状态: {'受雇' if is_penalized else '自由'}\n"
            sign_text += f"📅 签到时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
            sign_text += f"🔥 连续签到: {user_data['consecutive']} 天\n\n"

            sign_text += "【今日收益明细】\n"
            sign_text += f"💵 基础收益: {base_with_bonus:.1f} {currency}\n"
            if contract_bonus > 0:
                sign_text += f"👥 雇员加成: {contract_bonus:.1f} {currency}\n"
            if consecutive_bonus > 0:
                sign_text += f"🔥 连续签到加成: {consecutive_bonus:.1f} {currency}\n"
            sign_text += f"🏦 银行利息: {interest:.1f} {currency}\n"
            sign_text += f"📊 小计: {original_earned:.1f} {currency}\n"
            if is_penalized:
                sign_text += f"⚠️ 受雇惩罚后: {earned:.1f} {currency}\n"
            sign_text += f"✅ 今日总收益: {earned:.1f} {currency}\n\n"

            sign_text += "【资产状况】\n"
            sign_text += f"💰 现金: {user_data['coins']:.1f} {currency}\n"
            sign_text += f"🏦 银行存款: {user_data['bank']:.1f} {currency}\n"
            sign_text += (
                f"💎 身价: {user_data['coins'] + user_data['bank']:.1f} {currency}\n\n"
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

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        # 获取群所有用户数据并计算实时身价
        group_users = await self.data_manager.get_group_users(group_id)

        if not group_users:
            await send_text_reply(event, "本群暂无签到数据，无法生成排行榜。")
            return

        # 计算每个用户的实时身价
        user_wealth_list = []
        for user_id in group_users:
            user_data = await self.data_manager.get_user_data(group_id, user_id)
            # 使用实时身价计算
            total_wealth = await self.wealth_calculator.calculate_wealth_value(
                group_id, user_data, user_id
            )
            user_wealth_list.append((user_id, total_wealth))

        # 按身价排序并取前10
        user_wealth_list.sort(key=lambda x: x[1], reverse=True)
        top_10_users = user_wealth_list[:10]

        user_ids_to_fetch = [user[0] for user in top_10_users]
        name_coroutines = [
            self._get_user_name_from_platform(event, uid) for uid in user_ids_to_fetch
        ]
        names = await asyncio.gather(*name_coroutines)

        currency = self._get_currency_name()
        leaderboard_str = "本群财富排行榜\n" + "-" * 20 + "\n"
        for rank, ((user_id, total_wealth), user_name) in enumerate(
            zip(top_10_users, names), start=1
        ):
            leaderboard_str += (
                f"第{rank}名: {user_name} - {total_wealth:.1f} {currency}\n"
            )

        await send_text_reply(event, leaderboard_str.strip())

    @filter.regex(r"^赎身$")
    async def terminate_contract(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        if not user_data["contracted_by"]:
            await send_text_reply(event, "您是自由身，无需赎身。")
            return

        employer_id = user_data["contracted_by"]

        # 查询购买记录中的价格
        purchase_price = await self.data_manager.get_latest_purchase_price(
            group_id, user_id
        )

        if purchase_price <= 0:
            # 没有购买记录（旧数据兼容），计算当前价格并记录
            # 获取目标用户角色（用于计算管理员价格加成）
            target_role = await self._get_user_role(event, user_id)

            # 使用身价计算器计算购买价格
            purchase_price = await self.wealth_calculator.calculate_purchase_price(
                group_id, user_data, user_id, target_role
            )

            # 记录到购买历史（兼容旧数据）
            await self.data_manager.record_purchase(
                group_id, employer_id, user_id, purchase_price
            )

        # 赎身费用 = 购买价格（不再乘以比例）
        cost = purchase_price

        currency = self._get_currency_name()
        if user_data["coins"] < cost:
            await send_text_reply(
                event, f"{currency}不足，需要支付赎身费用：{cost:.1f}{currency}。"
            )
            return

        employer_data = await self.data_manager.get_user_data(group_id, employer_id)

        user_data["coins"] -= cost

        # Update contractor relationship in database
        await self.data_manager.remove_contractor(group_id, employer_id, user_id)

        # Save user data
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        # 计算雇主补偿 = 赎身费用 × 返还率
        trade_config = self.config.get("trade", {})
        redeem_return_rate = trade_config.get("redeem_return_rate", 0.5)
        compensation = cost * redeem_return_rate
        employer_data["coins"] += compensation

        # Save employer data
        await self.data_manager.save_user_data(group_id, employer_id, employer_data)

        employer_name = await self._get_user_name_from_platform(event, employer_id)
        await send_text_reply(
            event,
            f"赎身成功，消耗{cost:.1f}{currency}，重获自由！\n"
            f"原雇主 {employer_name} 获得了 {compensation:.1f} {currency}作为补偿（赎身费用的{redeem_return_rate * 100:.0f}%）。",
        )

    async def _get_group_owner_id(self, event: AstrMessageEvent) -> str | None:
        """获取群群主ID

        Args:
            event: 消息事件

        Returns:
            群主ID，如果获取失败则返回 None
        """
        if event.get_platform_name() == "aiocqhttp":
            try:
                from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                    AiocqhttpMessageEvent,
                )

                if isinstance(event, AiocqhttpMessageEvent):
                    client = event.bot
                    resp = await client.api.call_action(
                        "get_group_member_list",
                        group_id=event.message_obj.group_id,
                    )
                    for member in resp:
                        if member.get("role") == "owner":
                            return str(member.get("user_id", ""))
            except Exception as e:
                logger.warning(f"获取群群主ID失败: {e}")
        return None

    @filter.regex(r"^(我的信息|签到查询|我的资产|详细信息|我的详细信息)$")
    async def sign_query(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())
        message_str = event.message_str.strip()

        # Check if detailed info is requested
        is_detailed = "详细" in message_str

        # Check if image card is enabled
        enable_image_card = basic_config.get("enable_image_card", True)

        # 获取群主信息
        owner_id = await self._get_group_owner_id(event)
        owner_name = ""
        owner_currency_balance = 0.0
        if owner_id:
            owner_name = await self._get_user_name_from_platform(event, owner_id)
            owner_currency_balance = (
                await self.data_manager.db.get_owner_currency_balance(group_id, user_id)
            )

        if not enable_image_card:
            # Send text-only query result
            user_data = await self.data_manager.get_user_data(group_id, user_id)
            user_name = await self._get_user_name_from_platform(event, user_id)

            # 获取群管理员列表（用于计算管理员雇员加成）
            admin_ids = await self._get_group_admin_ids(event)

            # Calculate tomorrow income
            income_info = await self.wealth_system.calculate_tomorrow_income(
                user_data, group_id, user_id, admin_ids
            )

            # 使用实时身价计算
            total_wealth = await self.wealth_calculator.calculate_wealth_value(
                group_id, user_data, user_id
            )
            currency = self._get_currency_name()

            if is_detailed:
                # Detailed info output
                # 使用实时身价计算财富等级
                wealth_level, _ = await self.wealth_system.get_wealth_info(
                    group_id, user_data, user_id
                )
                from datetime import datetime

                now = datetime.now(SHANGHAI_TZ)

                info_text = f"【{user_name} 的资产信息】\n"
                info_text += f"👤 用户ID: {user_id}\n"
                info_text += f"💎 财富等级: {wealth_level}\n"
                info_text += (
                    f"📊 状态: {'受雇' if user_data['contracted_by'] else '自由'}\n"
                )
                info_text += f"📅 查询时间: {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"

                info_text += "【资产状况】\n"
                info_text += f"💰 现金: {user_data['coins']:.1f} {currency}\n"
                info_text += f"🏦 银行存款: {user_data['bank']:.1f} {currency}\n"
                info_text += f"💎 身价: {total_wealth:.1f} {currency}\n"
                if owner_name:
                    info_text += f"👑 {owner_name}币: {owner_currency_balance:.1f}\n"
                info_text += f"🔥 连续签到: {user_data['consecutive']} 天\n\n"

                info_text += "【明日预计收入】\n"
                info_text += f"💵 基础收益: {income_info['base']:.1f} {currency}\n"
                if income_info["contract_bonus"] > 0:
                    info_text += (
                        f"👥 雇员加成: {income_info['contract_bonus']:.1f} {currency}\n"
                    )
                if income_info["consecutive_bonus"] > 0:
                    info_text += f"🔥 连续签到加成: {income_info['consecutive_bonus']:.1f} {currency}\n"
                info_text += f"🏦 银行利息: {income_info['interest']:.1f} {currency}\n"
                info_text += (
                    f"📊 明日预计总收入: {income_info['total']:.1f} {currency}\n\n"
                )

                # Add contractor info
                if user_data.get("contractors"):
                    contractor_names = []
                    for cid in user_data["contractors"]:
                        cname = await self._get_user_name_from_platform(event, cid)
                        contractor_names.append(cname)
                    info_text += f"👥 雇员 ({len(user_data['contractors'])}人): {', '.join(contractor_names)}\n"

                if user_data.get("contracted_by"):
                    owner_name = await self._get_user_name_from_platform(
                        event, user_data["contracted_by"]
                    )
                    info_text += f"🔒 雇主: {owner_name}"
            else:
                # Simple info output (default)
                # 使用实时身价计算财富等级
                wealth_level, _ = await self.wealth_system.get_wealth_info(
                    group_id, user_data, user_id
                )

                info_text = f"【{user_name} 的资产】\n"
                info_text += f"💎 财富等级: {wealth_level}\n"
                info_text += f"💰 现金: {user_data['coins']:.1f} {currency}\n"
                info_text += f"🏦 银行: {user_data['bank']:.1f} {currency}\n"
                info_text += f"💎 身价: {total_wealth:.1f} {currency}\n"
                if owner_name:
                    info_text += f"👑 {owner_name}币: {owner_currency_balance:.1f}\n"

                # Add contractor info
                if user_data.get("contractors"):
                    contractor_names = []
                    for cid in user_data["contractors"]:
                        cname = await self._get_user_name_from_platform(event, cid)
                        contractor_names.append(cname)
                    info_text += f"👥 雇员: {', '.join(contractor_names)}\n"

                if user_data.get("contracted_by"):
                    owner_name = await self._get_user_name_from_platform(
                        event, user_data["contracted_by"]
                    )
                    info_text += f"🔒 雇主: {owner_name}\n"

                info_text += f"📈 明日预计: {income_info['total']:.1f} {currency}"

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

            # 使用实时身价计算
            total_wealth = await self.wealth_calculator.calculate_wealth_value(
                group_id, user_data, user_id
            )
            currency = self._get_currency_name()
            info_text = f"【{user_name} 的资产信息】\n"
            info_text += f"💰 现金: {user_data['coins']:.1f} {currency}\n"
            info_text += f"🏦 银行存款: {user_data['bank']:.1f} {currency}\n"
            info_text += f"💎 身价: {total_wealth:.1f} {currency}\n"

            # Add contractor info
            if user_data.get("contractors"):
                contractor_names = []
                for cid in user_data["contractors"]:
                    cname = await self._get_user_name_from_platform(event, cid)
                    contractor_names.append(cname)
                info_text += f"👥 雇员: {', '.join(contractor_names)}\n"

            if user_data.get("contracted_by"):
                owner_name = await self._get_user_name_from_platform(
                    event, user_data["contracted_by"]
                )
                info_text += f"🔒 雇主: {owner_name}\n"

            # Add consecutive sign-in info
            if user_data.get("consecutive", 0) > 0:
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

    @filter.regex(r"^(存款|存钱)\s*([0-9.]+)$")
    async def deposit(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        # Parse amount from message
        message_str = event.message_str
        match = __import__("re").match(r"^(存款|存钱)\s*([0-9.]+)$", message_str)
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

        # 限制金额精度为一位小数
        amount = round(amount, 1)

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        # 使用整数比较避免浮点精度问题
        amount_int = int(amount * 10)
        coins_int = int(user_data["coins"] * 10)
        if amount_int > coins_int:
            await send_text_reply(
                event, f"现金不足，当前现金：{user_data['coins']:.1f}"
            )
            return

        user_data["coins"] -= amount
        user_data["bank"] += amount

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        currency = self._get_currency_name()
        await send_text_reply(event, f"成功存入 {amount:.1f} {currency}到银行。")

    @filter.regex(r"^(取款|取钱)\s*([0-9.]+)$")
    async def withdraw(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        # Parse amount from message
        message_str = event.message_str
        match = __import__("re").match(r"^(取款|取钱)\s*([0-9.]+)$", message_str)
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

        # 限制金额精度为一位小数
        amount = round(amount, 1)

        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        # 使用整数比较避免浮点精度问题
        amount_int = int(amount * 10)
        bank_int = int(user_data["bank"] * 10)
        if amount_int > bank_int:
            await send_text_reply(
                event, f"银行存款不足，当前存款：{user_data['bank']:.1f}"
            )
            return

        user_data["bank"] -= amount
        user_data["coins"] += amount

        # Save user data to database
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        currency = self._get_currency_name()
        await send_text_reply(event, f"成功取出 {amount:.1f} {currency}。")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_at_bot(self, event: AstrMessageEvent):
        """监听at机器人事件，随机发放金币奖励"""
        # 检查数据库是否已初始化
        if not self.data_manager.is_db_initialized():
            return

        # 检查是否是at机器人
        if (
            not hasattr(event, "is_at_or_wake_command")
            or not event.is_at_or_wake_command
        ):
            return

        # 维护模式下不发放AT奖励（静默处理）
        if self._is_maintenance_mode():
            return

        # 获取at奖励配置
        at_reward_config = self.config.get("at_reward", {})
        enable_at_reward = at_reward_config.get("enable_at_reward", True)

        if not enable_at_reward:
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        user_id = str(event.get_sender_id())
        user_name = await self._get_user_name_from_platform(event, user_id)

        # 获取时区配置
        timezone_str = at_reward_config.get("at_reward_timezone", "Asia/Shanghai")
        try:
            tz = pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone("Asia/Shanghai")

        # 获取当前日期
        now = datetime.now(tz)
        today = now.strftime("%Y-%m-%d")

        # 检查今日奖励次数
        daily_limit = at_reward_config.get("at_reward_daily_limit", 5)
        reward_count = await self.data_manager.get_user_at_reward_count(
            group_id, user_id, today
        )

        if reward_count >= daily_limit:
            # 已达上限，静默处理
            logger.info(
                f"[AtReward] 用户 {user_name}({user_id}) 已达到今日at奖励上限 {daily_limit} 次"
            )
            return

        # 概率判定（使用整数比较，避免浮点精度问题）
        probability = at_reward_config.get("at_reward_probability", 0.3)
        # 确保概率在有效范围内
        probability = max(0.0, min(1.0, float(probability)))

        # 将概率转换为千分比整数（0-1000）
        probability_int = int(probability * 1000)
        random_int = random.randint(1, 1000)

        logger.info(
            f"[AtReward] 用户 {user_name}({user_id}) at机器人，概率判定: 随机值={random_int}/1000, 目标概率={probability_int}/1000 ({probability:.1%})"
        )

        if random_int > probability_int:
            # 未中奖，静默处理
            logger.info(
                f"[AtReward] 用户 {user_name}({user_id}) 未触发奖励 (随机值{random_int} > 目标概率{probability_int})"
            )
            return

        # 计算奖励金额
        reward_min = at_reward_config.get("at_reward_min", 1.0)
        reward_max = at_reward_config.get("at_reward_max", 10.0)
        reward_amount = round(random.uniform(reward_min, reward_max), 1)

        currency = self._get_currency_name()
        logger.info(
            f"[AtReward] 用户 {user_name}({user_id}) 触发奖励，获得 {reward_amount:.1f} {currency}"
        )

        # 发放奖励
        user_data = await self.data_manager.get_user_data(group_id, user_id)
        user_data["coins"] += reward_amount
        await self.data_manager.save_user_data(group_id, user_id, user_data)

        # 记录奖励
        await self.data_manager.record_at_reward(
            group_id, user_id, today, reward_amount
        )

        # 获取新的奖励次数和总金额
        new_count = await self.data_manager.get_user_at_reward_count(
            group_id, user_id, today
        )
        new_total = await self.data_manager.get_user_at_reward_total(
            group_id, user_id, today
        )

        logger.info(
            f"[AtReward] 用户 {user_name}({user_id}) 今日at奖励: {new_count}/{daily_limit} 次，累计 {new_total:.1f} {currency}"
        )

        # 发送奖励消息（简化版，仅提示获得的用户和金额）
        reward_msg = f"🎉 {user_name} 获得了随机掉落的 {reward_amount:.1f} {currency}！"
        await send_text_reply(event, reward_msg)

    @filter.regex(r"^(兑换码|兑换)\s*(.+)$")
    async def redeem(self, event: AstrMessageEvent):
        if not is_at_bot(event):
            return

        group_id = str(event.message_obj.group_id)
        basic_config = self.config.get("basic", {})
        if not is_group_allowed(group_id, basic_config.get("enabled_groups", [])):
            return

        # 检查维护模式
        if self._is_maintenance_mode():
            await send_text_reply(event, "系统维护中，暂时无法使用此功能，请稍后再试。")
            return

        # 解析兑换码
        message_str = event.message_str
        match = __import__("re").match(r"^(兑换码|兑换)\s*(.+)$", message_str)
        if not match:
            await send_text_reply(event, "兑换码格式不正确，请使用：兑换 <兑换码>")
            return

        code = match.group(2).strip()
        if not code:
            await send_text_reply(event, "请输入兑换码。")
            return

        logger.info(f"[Redeem] 用户尝试兑换，解析到的兑换码: '{code}'")

        user_id = str(event.get_sender_id())

        # 使用兑换码
        success, message, reward_amount = await self.data_manager.use_redeem_code(
            group_id, user_id, code
        )

        currency = self._get_currency_name()
        if success:
            await send_text_reply(
                event, f"🎉 兑换成功！您获得了 {reward_amount:.1f} {currency}！"
            )
        else:
            await send_text_reply(event, f"❌ {message}")

    async def terminate(self):
        """插件终止时关闭资源"""
        # 停止汇率更新后台服务
        if hasattr(self, "exchange_rate_service"):
            await self.exchange_rate_service.stop()

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
