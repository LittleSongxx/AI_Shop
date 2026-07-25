"""工具白名单与参数归属校验。

风险等级与白名单本身在 ``app.domain.tool_policy``，这里只做"守卫"该做的事：
查表放行 + 校验参数里的 userId 是不是调用者自己的。表放在 domain 下是因为
``forced_tools`` / ``mcp_tool_router`` 也要查同一张表，它们不该依赖 harness 层。
"""

from app.domain.tool_policy import (
    ALL_ALLOWED_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    is_allowed,
    is_write_tool,
)

__all__ = ["ALL_ALLOWED_TOOLS", "READ_TOOLS", "WRITE_TOOLS", "ToolGuardrail"]

class ToolGuardrail:

    def is_allowed(self, tool_name: str) -> bool:

        return is_allowed(tool_name)

    def is_write_tool(self, tool_name: str) -> bool:

        return is_write_tool(tool_name)

    #: 模型可能用驼峰也可能用下划线，两种都要看——只查一种等于放过另一种。
    _USER_ID_KEYS = ("userId", "user_id")

    def claimed_user_id(self, args: dict) -> str | None:
        """取模型在参数里自称的用户身份，没写则为 None。"""
        for key in self._USER_ID_KEYS:
            value = args.get(key)
            if value is not None:
                return str(value)
        return None

    def validate_tool_args(self, tool_name: str, args: dict, user_id: str) -> bool:
        """判断参数里自称的身份是否就是调用者本人。

        必须在调用方覆写 userId **之前**调用，否则看到的是覆写后的值，恒为 True。
        返回 False 不代表已被拦住：真正的拦截靠调用方覆写身份，这里只负责让这件事可见。
        """
        claimed = self.claimed_user_id(args)
        return claimed is None or claimed == user_id
