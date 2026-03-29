"""
卡片渲染服务模块

提供签到卡片、信息查询卡片的渲染功能。
使用HTML模板渲染图片，支持自定义字体和背景。
"""

import os
from datetime import datetime

import pytz
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.data_manager import DataManager
from ..core.wealth_system import WealthSystem
from .image_cache import ImageCacheService

SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")


class CardRenderer:
    """卡片渲染器

    负责渲染签到卡片和信息查询卡片。
    """

    def __init__(
        self,
        plugin_dir: str,
        data_manager: DataManager,
        wealth_system: WealthSystem,
        image_cache: ImageCacheService,
    ):
        """初始化卡片渲染器

        Args:
            plugin_dir: 插件目录路径
            data_manager: 数据管理器实例
            wealth_system: 财富系统实例
            image_cache: 图片缓存服务实例
        """
        self.plugin_dir = plugin_dir
        self.data_manager = data_manager
        self.wealth_system = wealth_system
        self.image_cache = image_cache

        self.font_path = os.path.join(plugin_dir, "请以你的名字呼唤我.ttf")
        self.template_path = os.path.join(plugin_dir, "card_template.html")
        self.default_bg_path = os.path.join(plugin_dir, "default_bg.jpg")

        self.html_template = self._load_template()

    def _load_template(self) -> str:
        """加载HTML模板

        Returns:
            HTML模板字符串
        """
        if os.path.exists(self.template_path):
            try:
                with open(self.template_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"读取HTML模板文件失败: {e}")
        return "<h1>模板文件加载失败</h1>"

    def _file_to_base64(self, file_path: str) -> str:
        """将本地文件转换为base64

        Args:
            file_path: 文件路径

        Returns:
            base64编码的字符串
        """
        return self.image_cache.file_to_base64(file_path)

    async def prepare_render_data(
        self,
        event: AstrMessageEvent,
        is_query: bool = False,
        is_penalized: bool = False,
        original_earned: float = 0.0,
        bg_api_url: str = "",
    ) -> dict:
        """准备卡片渲染数据

        Args:
            event: 消息事件
            is_query: 是否为查询模式
            is_penalized: 是否受雇（收益减少）
            original_earned: 原始收益（用于显示惩罚前收益）
            bg_api_url: 背景图API地址

        Returns:
            渲染数据字典
        """
        group_id = str(event.message_obj.group_id)
        user_id = str(event.get_sender_id())
        user_data = await self.data_manager.get_user_data(group_id, user_id)

        # 获取头像和背景图
        avatar_data = await self.image_cache.get_avatar(user_id)
        bg_image_data = ""
        if bg_api_url:
            bg_image_data = await self.image_cache.get_daily_background(bg_api_url)
        if not bg_image_data:
            bg_image_data = self._file_to_base64(self.default_bg_path)

        # 字体路径
        font_path = (
            f"file://{os.path.abspath(self.font_path)}"
            if os.path.exists(self.font_path)
            else ""
        )

        # 财富等级信息
        wealth_level, user_base_rate = self.wealth_system.get_wealth_info(user_data)

        render_data = {
            "font_path": font_path,
            "bg_image_data": bg_image_data,
            "avatar_data": avatar_data,
            "user_id": user_id,
            "user_name": event.get_sender_name(),
            "status": "受雇" if user_data["contracted_by"] else "自由",
            "wealth_level": wealth_level,
            "time_title": "查询时间" if is_query else "签到时间",
            "current_time": datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "income_title": "明日预计收入" if is_query else "今日总收益",
            "coins": user_data["coins"],
            "bank": user_data["bank"],
            "consecutive": user_data["consecutive"],
            "is_query": is_query,
            "is_penalized": is_penalized,
            "original_earned": original_earned,
        }

        if is_query:
            # 查询模式：计算明日预计收入
            income_info = await self.wealth_system.calculate_tomorrow_income(
                user_data, group_id
            )

            # 获取雇员名称列表
            contractor_names = []
            for uid in user_data["contractors"]:
                name = await self._get_user_name_from_platform(event, uid)
                contractor_names.append(name)

            render_data["contractors_display"] = (
                ", ".join(contractor_names) if contractor_names else "无"
            )
            render_data.update(
                {
                    "total_income": income_info["total"],
                    "base_with_bonus": income_info["base"],
                    "contract_bonus": income_info["contract_bonus"],
                    "consecutive_bonus": income_info["consecutive_bonus"],
                    "tomorrow_interest": income_info["interest"],
                }
            )
        else:
            # 签到模式：显示今日收益
            render_data["contractors_display"] = str(len(user_data["contractors"]))
            interest = user_data["bank"] * 0.01
            earned = original_earned
            render_data.update({"earned": earned + interest, "interest": interest})

        return render_data

    async def render_card(self, render_data: dict) -> str:
        """渲染卡片

        Args:
            render_data: 渲染数据字典

        Returns:
            渲染后的图片URL或路径
        """
        try:
            # 注意：html_render 方法由 AstrBot 框架提供，需要在主类中调用
            # 这里返回渲染数据，由主类调用 html_render
            return render_data
        except Exception as e:
            logger.error(f"准备渲染数据失败: {e}")
            return ""

    async def generate_sign_card(
        self,
        event: AstrMessageEvent,
        is_penalized: bool = False,
        original_earned: float = 0.0,
        bg_api_url: str = "",
    ) -> dict:
        """生成签到卡片数据

        Args:
            event: 消息事件
            is_penalized: 是否受雇（收益减少）
            original_earned: 原始收益
            bg_api_url: 背景图API地址

        Returns:
            渲染数据字典
        """
        return await self.prepare_render_data(
            event=event,
            is_query=False,
            is_penalized=is_penalized,
            original_earned=original_earned,
            bg_api_url=bg_api_url,
        )

    async def generate_query_card(
        self,
        event: AstrMessageEvent,
        bg_api_url: str = "",
    ) -> dict:
        """生成信息查询卡片数据

        Args:
            event: 消息事件
            bg_api_url: 背景图API地址

        Returns:
            渲染数据字典
        """
        return await self.prepare_render_data(
            event=event,
            is_query=True,
            bg_api_url=bg_api_url,
        )

    async def _get_user_name_from_platform(
        self, event: AstrMessageEvent, target_id: str
    ) -> str:
        """从平台获取用户名称

        Args:
            event: 消息事件
            target_id: 目标用户ID

        Returns:
            用户名称
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
                        user_id=int(target_id),
                        no_cache=True,
                    )
                    return resp.get("card") or resp.get(
                        "nickname", f"用户{target_id[-4:]}"
                    )
            except Exception as e:
                logger.warning(f"通过API获取用户信息({target_id})失败: {e}")
        return f"用户{target_id[-4:]}"

    def get_template(self) -> str:
        """获取HTML模板

        Returns:
            HTML模板字符串
        """
        return self.html_template
