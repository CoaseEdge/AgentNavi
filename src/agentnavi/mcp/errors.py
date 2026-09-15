"""MCP 边界使用的稳定、可公开错误。

客户端只接收这里定义的固定中文消息和经过筛选的结构化详情。底层异常文本
可能包含数据库、日志或项目绝对路径，因此未知异常一律映射为通用错误。
``retryable`` 表示调用方修正输入后可以重试，不表示原请求可以无条件重放。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .protocol import Error


@dataclass(frozen=True, slots=True)
class _ErrorDefinition:
    message: str
    retryable: bool


ERROR_DEFINITIONS = MappingProxyType(
    {
        "PROJECT_REQUIRED": _ErrorDefinition(
            message="无法确定项目，请提供明确的 project ID 或 workspace。",
            retryable=True,
        ),
        "PROJECT_NOT_FOUND": _ErrorDefinition(
            message="找不到指定的项目，请检查 project ID。",
            retryable=True,
        ),
        "INVALID_ARGUMENT": _ErrorDefinition(
            message="请求参数无效，请检查参数类型和取值。",
            retryable=True,
        ),
        "INTERNAL_ERROR": _ErrorDefinition(
            message="AgentNavi 处理请求时发生内部错误。",
            retryable=False,
        ),
    }
)


class AgentNaviMCPError(LookupError):
    """携带公开错误码，但不携带底层异常文本。"""

    def __init__(
        self,
        code: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            definition = ERROR_DEFINITIONS[code]
        except KeyError as exc:
            raise ValueError(f"未定义的 MCP 错误码：{code}") from exc
        # Error 会立即建立递归不可变快照并执行 JSON 与隐私检查；异常对象只
        # 暴露这个快照，不能被调用方持有的原始 dict/list 后续突变影响。
        self._dto = Error(
            code=code,
            message=definition.message,
            retryable=definition.retryable,
            details=details or {},
        )
        self.code = self._dto.code
        self.message = self._dto.message
        self.retryable = self._dto.retryable
        self.details = self._dto.details
        super().__init__(self.message)

    def to_dto(self) -> Error:
        return self._dto


def to_public_error(exc: BaseException) -> Error:
    """把边界异常转换为稳定 DTO，未知异常不透传任何内部详情。"""

    if isinstance(exc, AgentNaviMCPError):
        return exc.to_dto()
    return Error(
        code="INTERNAL_ERROR",
        message=ERROR_DEFINITIONS["INTERNAL_ERROR"].message,
        retryable=ERROR_DEFINITIONS["INTERNAL_ERROR"].retryable,
    )


__all__ = ["AgentNaviMCPError", "ERROR_DEFINITIONS", "to_public_error"]
