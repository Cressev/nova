"""工具错误词汇表（对照 dsh core/tools 的结构化错误码）。

dsh 的工具失败不只是人话 message：每个失败都带 `{name, code}` 结构化
info（UNKNOWN_TOOL / INVALID_ARGS / INVALID_TOOL_OUTPUT / ABORTED /
ABORTED_BEFORE_DISPATCH），让重试、审计、UI 可以按码路由而不是解析中文。
Nova 在此对齐：所有失败路径统一经 `ToolFailureError`，error_code 进
result data 与 tool_done 事件。
"""

from __future__ import annotations

# 未知工具名（模型请求了未注册的工具）
UNKNOWN_TOOL = "UNKNOWN_TOOL"
# 模型生成的参数违反工具声明的 JSON Schema
INVALID_ARGS = "INVALID_ARGS"
# 工具返回值不是无损 JSON / 违反输出契约
INVALID_TOOL_OUTPUT = "INVALID_TOOL_OUTPUT"
# 协作式超时预算耗尽（body 已启动但未在预算内返回）
TOOL_TIMEOUT = "TOOL_TIMEOUT"
# 取消发生在工具 body 启动之后
TOOL_ABORTED = "ABORTED"
# 取消发生在工具 body 启动之前（body 从未执行）
TOOL_ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"
# 权限拒绝（用户 deny、hook deny 或黑名单）
TOOL_PERMISSION_DENIED = "PERMISSION_DENIED"


class ToolFailureError(Exception):
    """带结构化错误码的工具失败。

    message 面向模型（中文，可读）；code 面向机器（上方常量）。
    dsh 语义：Human-readable failure message without the `Error: ` envelope。
    """

    def __init__(self, message: str, *, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
