"""
工具函数模块

提供常用的工具函数，如at检查、目标用户获取、群白名单检查等。
"""

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At


def is_at_bot(event: AstrMessageEvent) -> bool:
    """检查消息是否at了机器人

    Args:
        event: 消息事件

    Returns:
        是否at了机器人
    """
    msg_obj = getattr(event, "message_obj", None)
    if not msg_obj:
        return False

    bot_id = getattr(msg_obj, "self_id", "")
    chain = getattr(msg_obj, "message", None) or []

    for component in chain:
        if isinstance(component, At):
            at_id = str(component.qq)
            if at_id == str(bot_id):
                return True
    return False


def get_target_at_user(event: AstrMessageEvent) -> str | None:
    """获取消息中被at的目标用户ID（排除机器人自身）

    Args:
        event: 消息事件

    Returns:
        目标用户ID，如果没有则返回None
    """
    msg_obj = getattr(event, "message_obj", None)
    if not msg_obj:
        return None

    bot_id = getattr(msg_obj, "self_id", "")
    chain = getattr(msg_obj, "message", None) or []

    for component in chain:
        if isinstance(component, At):
            at_id = str(component.qq)
            # 跳过机器人自身的at
            if at_id != str(bot_id):
                return at_id
    return None


def get_first_at_user(event: AstrMessageEvent) -> str | None:
    """获取消息中第一个被at的用户ID（包括机器人）

    Args:
        event: 消息事件

    Returns:
        目标用户ID，如果没有则返回None
    """
    msg_obj = getattr(event, "message_obj", None)
    if not msg_obj:
        return None

    chain = getattr(msg_obj, "message", None) or []

    for component in chain:
        if isinstance(component, At):
            return str(component.qq)
    return None


def is_group_allowed(group_id: str, enabled_groups: list) -> bool:
    """检查群是否允许使用插件功能

    Args:
        group_id: 群ID
        enabled_groups: 允许使用的群列表

    Returns:
        是否允许使用
    """
    if not enabled_groups:
        return True
    return str(group_id) in [str(g) for g in enabled_groups]


def get_plain_text_from_message(event: AstrMessageEvent) -> str:
    """从消息中提取纯文本内容（不包括At组件）

    遍历消息组件链，提取所有Plain组件的内容，忽略At组件。
    这样可以避免将@用户的QQ号误解析为指令参数。

    Args:
        event: 消息事件

    Returns:
        纯文本内容
    """
    from astrbot.api.message_components import Plain

    msg_obj = getattr(event, "message_obj", None)
    if not msg_obj:
        return ""

    chain = getattr(msg_obj, "message", None) or []
    text_parts = []

    for component in chain:
        if isinstance(component, Plain):
            text_parts.append(component.text)

    return "".join(text_parts)
