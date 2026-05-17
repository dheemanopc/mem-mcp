"""Unit tests for the skill framework (Phase 1.A): vault, audit-memory helpers, loader."""

from __future__ import annotations

from typing import Any

import pytest


class TestSkillVaultEncryption:
    def test_round_trip_returns_same_dict(self) -> None:
        from mem_mcp.skills.vault import SkillVault
        import os
        from uuid import uuid4

        v = SkillVault(os.urandom(32))
        t = uuid4()
        creds = {"api_key": "abc", "access_token": "xyz", "n": 42}
        blob = v.encrypt_creds(t, "kite", creds)
        assert v.decrypt_creds(t, "kite", blob) == creds

    def test_blob_format_nonce_plus_ciphertext(self) -> None:
        from mem_mcp.skills.vault import SkillVault, NONCE_LEN, TAG_LEN
        import os
        from uuid import uuid4

        v = SkillVault(os.urandom(32))
        blob = v.encrypt_creds(uuid4(), "kite", {"a": 1})
        # nonce (12) + at-least-tag (16) for empty plaintext; with {"a":1} (7 chars) total >= 35
        assert len(blob) >= NONCE_LEN + TAG_LEN

    def test_wrong_aad_fails(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultError
        import os, pytest
        from uuid import uuid4

        v = SkillVault(os.urandom(32))
        t1, t2 = uuid4(), uuid4()
        blob = v.encrypt_creds(t1, "kite", {"x": 1})
        with pytest.raises(SkillVaultError):
            v.decrypt_creds(t2, "kite", blob)  # tenant differs → tag mismatch

    def test_wrong_skill_name_fails(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultError
        import os, pytest
        from uuid import uuid4

        v = SkillVault(os.urandom(32))
        t = uuid4()
        blob = v.encrypt_creds(t, "kite", {"x": 1})
        with pytest.raises(SkillVaultError):
            v.decrypt_creds(t, "github", blob)  # skill name differs → tag mismatch

    def test_wrong_key_fails(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultError
        import os, pytest
        from uuid import uuid4

        v1 = SkillVault(os.urandom(32))
        v2 = SkillVault(os.urandom(32))
        t = uuid4()
        blob = v1.encrypt_creds(t, "kite", {"x": 1})
        with pytest.raises(SkillVaultError):
            v2.decrypt_creds(t, "kite", blob)

    def test_short_blob_rejected(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultError
        import os, pytest
        from uuid import uuid4

        v = SkillVault(os.urandom(32))
        with pytest.raises(SkillVaultError):
            v.decrypt_creds(uuid4(), "kite", b"\x00" * 10)

    def test_invalid_master_key_size_rejected(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultError
        import pytest

        with pytest.raises(SkillVaultError):
            SkillVault(b"too-short")


class TestSkillVaultFromSettings:
    def test_from_settings_unset_raises_disabled(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultDisabled
        from unittest.mock import MagicMock
        import pytest

        fake_settings = MagicMock(skill_vault_master_key=None)
        with pytest.raises(SkillVaultDisabled):
            SkillVault.from_settings(fake_settings)

    def test_from_settings_empty_raises_disabled(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultDisabled
        from unittest.mock import MagicMock
        import pytest

        fake_settings = MagicMock(skill_vault_master_key="")
        with pytest.raises(SkillVaultDisabled):
            SkillVault.from_settings(fake_settings)

    def test_from_settings_bad_base64_raises_error(self) -> None:
        from mem_mcp.skills.vault import SkillVault, SkillVaultError
        from unittest.mock import MagicMock
        import pytest

        fake_settings = MagicMock(skill_vault_master_key="!!!not-base64!!!")
        with pytest.raises(SkillVaultError):
            SkillVault.from_settings(fake_settings)

    def test_from_settings_valid_base64_works(self) -> None:
        from mem_mcp.skills.vault import SkillVault
        from unittest.mock import MagicMock
        import os, base64

        key_b64 = base64.b64encode(os.urandom(32)).decode()
        fake_settings = MagicMock(skill_vault_master_key=key_b64)
        v = SkillVault.from_settings(fake_settings)
        assert v is not None


class TestAuditMemoryHelpers:
    def test_hash_args_stable_across_dict_order(self) -> None:
        from mem_mcp.skills.audit_memory import hash_args
        assert hash_args({"a": 1, "b": 2}) == hash_args({"b": 2, "a": 1})

    def test_hash_args_handles_nested(self) -> None:
        from mem_mcp.skills.audit_memory import hash_args
        assert hash_args({"a": [1, 2, 3]}) == hash_args({"a": [1, 2, 3]})
        assert hash_args({"a": [1, 2]}) != hash_args({"a": [2, 1]})

    def test_build_audit_tags_default(self) -> None:
        from mem_mcp.skills.audit_memory import build_audit_tags
        tags = build_audit_tags("kite", "get_holdings", "success")
        assert tags == [
            "ai-trader-event",
            "skill-kite",
            "skill-tool-get-holdings",
            "skill-result-success",
        ]

    def test_build_audit_tags_dedupes_extra(self) -> None:
        from mem_mcp.skills.audit_memory import build_audit_tags
        tags = build_audit_tags(
            "kite", "get_holdings", "success",
            extra_tags=["symbol-RECLTD", "skill-kite"],  # dup of auto-tag
        )
        assert tags.count("skill-kite") == 1
        assert "symbol-RECLTD" in tags

    def test_build_audit_metadata_includes_error_on_error(self) -> None:
        from mem_mcp.skills.audit_memory import build_audit_metadata
        from uuid import uuid4
        m = build_audit_metadata(
            "kite", "place_order", {"x": 1}, "error", 100, uuid4(),
            error_code="kite_rejected", error_message="insufficient funds " * 100,
        )
        assert m["error_code"] == "kite_rejected"
        assert len(m["error_message"]) == 500  # truncated

    def test_build_audit_metadata_no_error_on_success(self) -> None:
        from mem_mcp.skills.audit_memory import build_audit_metadata
        from uuid import uuid4
        m = build_audit_metadata("kite", "get_quote", {}, "success", 10, uuid4())
        assert "error_code" not in m
        assert "error_message" not in m

    def test_should_audit_default_true(self) -> None:
        from mem_mcp.skills.audit_memory import should_audit
        assert should_audit(None, "anything") is True

    def test_should_audit_respects_policy(self) -> None:
        from mem_mcp.skills.audit_memory import should_audit
        policy = {"get_quote": False, "place_order": True}
        assert should_audit(policy, "get_quote") is False
        assert should_audit(policy, "place_order") is True
        assert should_audit(policy, "unspecified") is True

    def test_parse_enabled_skills_variants(self) -> None:
        from mem_mcp.skills.loader import parse_enabled_skills
        assert parse_enabled_skills("") == []
        assert parse_enabled_skills(" ") == []
        assert parse_enabled_skills("kite") == ["kite"]
        assert parse_enabled_skills("kite,github") == ["kite", "github"]
        assert parse_enabled_skills(" Kite , GITHUB ,") == ["kite", "github"]


class TestSkillLoaderRegistration:
    def _fake_skill(self) -> Any:
        from mem_mcp.skills.base import NativeSkill, ToolDef, CredentialSpec, SkillCallContext
        from pydantic import BaseModel

        class StubInput(BaseModel):
            x: int

        class StubOutput(BaseModel):
            y: int

        class _StubSkill(NativeSkill):
            name = "stub"

            def list_tools(self) -> list[ToolDef]:
                return [
                    ToolDef(
                        name="echo",
                        description="echo tool",
                        InputModel=StubInput,
                        OutputModel=StubOutput,
                        read_only=True,
                        audit=True,
                    ),
                    ToolDef(
                        name="bang",
                        description="bang tool",
                        InputModel=StubInput,
                        OutputModel=StubOutput,
                        read_only=False,
                        audit=False,
                    ),
                ]

            async def call_tool(
                self, tool_name: str, args: BaseModel, ctx: SkillCallContext
            ) -> BaseModel:
                assert isinstance(args, StubInput)
                if tool_name == "echo":
                    return StubOutput(y=args.x + 1)
                if tool_name == "bang":
                    raise RuntimeError("boom")
                raise NotImplementedError

            def required_credentials(self) -> list[CredentialSpec]:
                return [CredentialSpec(name="key", description="k")]

        return _StubSkill()

    def test_registers_namespaced_names(self) -> None:
        from mem_mcp.mcp.registry import ToolRegistry
        from mem_mcp.skills.loader import SkillLoader
        from mem_mcp.skills.vault import SkillVault
        import os

        reg = ToolRegistry()
        loader = SkillLoader(vault=SkillVault(os.urandom(32)))
        count = loader.register_skill(reg, self._fake_skill())
        assert count == 2
        names = reg.names()
        assert "stub_echo" in names
        assert "stub_bang" in names

    def test_wrapper_inherits_read_only_scope(self) -> None:
        from mem_mcp.mcp.registry import ToolRegistry
        from mem_mcp.skills.loader import SkillLoader
        from mem_mcp.skills.vault import SkillVault
        import os

        reg = ToolRegistry()
        SkillLoader(vault=SkillVault(os.urandom(32))).register_skill(
            reg, self._fake_skill()
        )
        defs = reg.list_definitions()
        by_name = {d["name"]: d for d in defs}
        assert by_name["stub_echo"]["required_scope"] == "memory.read"
        assert by_name["stub_bang"]["required_scope"] == "memory.write"

    def test_wrapper_input_output_schemas_propagated(self) -> None:
        from mem_mcp.mcp.registry import ToolRegistry
        from mem_mcp.skills.loader import SkillLoader
        from mem_mcp.skills.vault import SkillVault
        import os

        reg = ToolRegistry()
        SkillLoader(vault=SkillVault(os.urandom(32))).register_skill(
            reg, self._fake_skill()
        )
        defs = reg.list_definitions()
        echo = next(d for d in defs if d["name"] == "stub_echo")
        assert echo["inputSchema"]["properties"]["x"]["type"] == "integer"
        assert echo["outputSchema"]["properties"]["y"]["type"] == "integer"
