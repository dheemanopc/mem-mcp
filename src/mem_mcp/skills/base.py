"""Skill framework base contracts — Skill Protocol, NativeSkill ABC, ToolDef, CredentialSpec."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel


@dataclass(frozen=True)
class CredentialSpec:
    """Declaration of a single credential field a skill requires per tenant.

    Used by onboarding flows to know what to ask for and by the vault to
    validate stored credential shapes.
    """

    name: str
    description: str
    sensitive: bool = True
    required: bool = True


@dataclass(frozen=True)
class ToolDef:
    """One tool exposed by a skill. Registered with name ``{skill}_{tool}``."""

    name: str
    description: str
    InputModel: type[BaseModel]
    OutputModel: type[BaseModel]
    read_only: bool = False
    audit: bool = True


@dataclass
class SkillCallContext:
    """Context passed into ``Skill.call_tool`` — includes decrypted credentials."""

    tenant_id: UUID
    identity_id: UUID
    client_id: str
    request_id: str
    credentials: dict[str, Any]
    tool_call_id: UUID


@runtime_checkable
class Skill(Protocol):
    """Contract every skill implements (native or sidecar variants)."""

    name: str

    def list_tools(self) -> list[ToolDef]: ...

    async def call_tool(
        self, tool_name: str, args: BaseModel, ctx: SkillCallContext
    ) -> BaseModel: ...

    def required_credentials(self) -> list[CredentialSpec]: ...

    async def health_check(self) -> bool: ...


class NativeSkill(ABC):
    """In-process skill base class. Subclasses implement call_tool directly."""

    name: ClassVar[str]

    @abstractmethod
    def list_tools(self) -> list[ToolDef]:
        ...

    @abstractmethod
    async def call_tool(
        self, tool_name: str, args: BaseModel, ctx: SkillCallContext
    ) -> BaseModel:
        ...

    @abstractmethod
    def required_credentials(self) -> list[CredentialSpec]:
        ...

    async def health_check(self) -> bool:
        return True


class SkillError(Exception):
    """Base for skill-layer errors. The loader wraps these as JsonRpcError."""


class SkillCredentialsMissingError(SkillError):
    """Raised when a skill tool is called but the tenant has no credentials stored."""


class SkillToolNotFoundError(SkillError):
    """Raised when call_tool is invoked with a tool_name the skill does not export."""
