"""memory.write_batch tool — store multiple memories in a single request."""

from __future__ import annotations

from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mem_mcp.mcp.errors import JsonRpcError
from mem_mcp.mcp.tool_descriptions import TOOL_DESCRIPTIONS
from mem_mcp.mcp.tools._base import BaseTool, ToolContext
from mem_mcp.mcp.tools.write import MemoryWriteInput, MemoryWriteOutput, MemoryWriteTool


class _BatchEntryResult(BaseModel):
    """Per-entry result. Mirrors MemoryWriteOutput on success, error on failure."""

    ok: bool
    id: UUID | None = None
    version: int | None = None
    deduped: bool | None = None
    merged_into: UUID | None = None
    created_at: str | None = None  # ISO 8601 datetime string
    error: dict[str, Any] | None = None  # {"code": "...", "message": "...", "data": {...}}


class MemoryWriteBatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memories: list[MemoryWriteInput] = Field(min_length=1, max_length=200)
    on_error: Literal["fail_all", "continue"] = "continue"


class MemoryWriteBatchOutput(BaseModel):
    results: list[_BatchEntryResult]
    written_count: int
    failed_count: int
    request_id: str


class MemoryWriteBatchTool(BaseTool):
    """Store multiple memories in a single request (batch write)."""

    name: ClassVar[str] = "memory_write_batch"
    description: ClassVar[str] = TOOL_DESCRIPTIONS["memory_write_batch"]
    required_scope: ClassVar[str] = "memory.write"
    InputModel: ClassVar[type[BaseModel]] = MemoryWriteBatchInput
    OutputModel: ClassVar[type[BaseModel]] = MemoryWriteBatchOutput

    async def __call__(self, ctx: ToolContext, inp: BaseModel) -> BaseModel:
        assert isinstance(inp, MemoryWriteBatchInput)

        results: list[_BatchEntryResult] = []
        write_tool = MemoryWriteTool()

        for i, entry in enumerate(inp.memories):
            try:
                # Call the existing MemoryWriteTool for each entry
                output = await write_tool(ctx, entry)
                assert isinstance(output, MemoryWriteOutput)

                results.append(
                    _BatchEntryResult(
                        ok=True,
                        id=output.id,
                        version=output.version,
                        deduped=output.deduped,
                        merged_into=output.merged_into,
                        created_at=output.created_at.isoformat(),
                    )
                )
            except JsonRpcError as exc:
                error_dict: dict[str, Any] = {
                    "code": exc.code if isinstance(exc.code, str) else str(exc.code),
                    "message": exc.message,
                }
                if exc.data:
                    error_dict["data"] = exc.data

                results.append(
                    _BatchEntryResult(
                        ok=False,
                        error=error_dict,
                    )
                )

                # Stop on first error if on_error=fail_all
                if inp.on_error == "fail_all":
                    # Mark remaining entries as skipped
                    for _ in inp.memories[i + 1 :]:
                        results.append(
                            _BatchEntryResult(
                                ok=False,
                                error={
                                    "code": "skipped",
                                    "message": "skipped due to on_error=fail_all",
                                },
                            )
                        )
                    break
            except Exception as exc:
                # Catch any unexpected errors
                results.append(
                    _BatchEntryResult(
                        ok=False,
                        error={
                            "code": "internal_error",
                            "message": str(exc)[:200],
                        },
                    )
                )

                if inp.on_error == "fail_all":
                    for _ in inp.memories[i + 1 :]:
                        results.append(
                            _BatchEntryResult(
                                ok=False,
                                error={
                                    "code": "skipped",
                                    "message": "skipped due to on_error=fail_all",
                                },
                            )
                        )
                    break

        written_count = sum(1 for r in results if r.ok)
        failed_count = len(results) - written_count

        return MemoryWriteBatchOutput(
            results=results,
            written_count=written_count,
            failed_count=failed_count,
            request_id=ctx.request_id,
        )
