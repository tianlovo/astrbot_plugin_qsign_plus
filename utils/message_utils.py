"""消息工具模块

提供带引用的消息回复功能，增强消息交互体验。
"""

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger


def create_reply_chain(event: AstrMessageEvent, text: str | None = None) -> list:
    """创建带引用的消息链

    Args:
        event: 消息事件对象
        text: 可选的文本内容

    Returns:
        消息链列表，包含 Reply 组件和可选的 Plain 组件
    """
    message_id = event.message_obj.message_id
    chain = [Comp.Reply(id=message_id)]
    if text:
        chain.append(Comp.Plain(text))
    return chain


async def send_text_reply(event: AstrMessageEvent, text: str) -> str | None:
    """发送带引用的纯文本回复

    Args:
        event: 消息事件对象
        text: 回复文本内容

    Returns:
        发送的消息ID，失败返回None
    """
    chain = create_reply_chain(event, text)
    result = await event.send(event.chain_result(chain))
    # 尝试获取发送的消息ID
    try:
        if result and hasattr(result, 'message_id'):
            return str(result.message_id)
    except Exception:
        pass
    return None


async def send_image_reply(
    event: AstrMessageEvent, image_url: str, text: str | None = None
) -> None:
    """发送带引用的图片回复

    Args:
        event: 消息事件对象
        image_url: 图片URL或本地路径
        text: 可选的附加文本内容
    """
    message_id = event.message_obj.message_id
    chain = [Comp.Reply(id=message_id)]

    if text:
        chain.append(Comp.Plain(text))

    chain.append(Comp.Image(file=image_url))
    await event.send(event.chain_result(chain))


async def recall_message(event: AstrMessageEvent, message_id: str) -> bool:
    """撤回指定消息

    Args:
        event: 消息事件对象
        message_id: 要撤回的消息ID

    Returns:
        是否成功撤回
    """
    try:
        if event.get_platform_name() == "aiocqhttp":
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            if isinstance(event, AiocqhttpMessageEvent):
                client = event.bot
                group_id = event.message_obj.group_id
                await client.api.call_action(
                    "delete_msg",
                    message_id=int(message_id),
                )
                return True
    except Exception as e:
        logger.warning(f"撤回消息失败: {e}")
    return False
