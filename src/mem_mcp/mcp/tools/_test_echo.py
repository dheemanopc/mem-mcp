"""Test-only echo tool. Used by mcp tests; never registered in production."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.mcp.tools._base import BaseTool, ToolContext


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(..., min_length=1, max_length=1024)


class EchoOutput(BaseModel):
    echoed: str
    request_id: str


class EchoTool(BaseTool):
    """Echo back the input message + request_id (test-only tool)."""

    name: ClassVar[str] = "_test.echo"
    required_scope: ClassVar[str] = "memory.read"
    InputModel: ClassVar[type[BaseModel]] = EchoInput
    OutputModel: ClassVar[type[BaseModel]] = EchoOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, EchoInput)
        return EchoOutput(echoed=inp.message, request_id=ctx.request_id)
