"""Ask the user for input mid-call, on either MCP protocol era.

Handshake-era connections (<= 2025-11-25) keep the server-initiated back-channel, so a tool
calls `ctx.elicit()` and waits. The 2026-07-28 protocol removed that channel (SEP-2322), so a
tool instead returns an `InputRequiredResult`; the client answers and retries the same tool
call with `ctx.input_responses` filled in. `ask()` hides the difference: it returns the answer
on a handshake connection, or an `Answer` whose `pending` result the tool must return as-is.

Tools that use it are typed `Result | InputRequiredResult`. FastMCP strips the guard result
from the output schema, so clients still see the real result type.
"""

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Literal

from fastmcp import Context
from fastmcp.server.elicitation import get_elicitation_schema
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

Action = Literal["accept", "decline", "cancel", "pending"]


@dataclass
class Answer[T]:
    """One elicitation round: a typed answer, a refusal, or a guard result to return."""

    action: Action
    value: T | None = None
    pending: InputRequiredResult | None = None

    @property
    def accepted(self) -> bool:
        return self.action == "accept" and self.value is not None


async def ask[T](ctx: Context, key: str, message: str, response_type: type[T]) -> Answer[T]:
    """Ask the user one question, returning `Answer.pending` when the client must answer first.

    `key` names this request in the multi-round-trip response map, so it must be unique within
    a tool call. `response_type` must be a flat dataclass (the MCP elicitation schema allows
    only string, number, integer, boolean and enum fields).
    """
    if _is_modern(ctx):
        return _ask_modern(ctx, key, message, response_type)

    result = await ctx.elicit(message, response_type)
    if result.action != "accept":
        return Answer(result.action)
    return Answer("accept", value=result.data)


def _is_modern(ctx: Context) -> bool:
    context = ctx.request_context
    return context is not None and context.protocol_version in MODERN_PROTOCOL_VERSIONS


def _ask_modern[T](ctx: Context, key: str, message: str, response_type: type[T]) -> Answer[T]:
    responses = ctx.input_responses
    if responses is not None and key in responses:
        result = responses[key]
        if result.action != "accept":
            return Answer(result.action)
        return Answer("accept", value=_build(response_type, result.content or {}))

    request = ElicitRequest(
        method="elicitation/create",
        params=ElicitRequestFormParams(
            message=message,
            requested_schema=get_elicitation_schema(response_type),
        ),
    )
    guard = InputRequiredResult(
        result_type="input_required",
        input_requests={key: request},
    )
    return Answer("pending", pending=guard)


def _build[T](response_type: type[T], content: dict[str, Any]) -> T:
    """Turn a wire response (a plain object) back into the requested dataclass."""
    if not is_dataclass(response_type):
        return content  # type: ignore[return-value]
    names = {field.name for field in fields(response_type)}
    return response_type(**{name: value for name, value in content.items() if name in names})
