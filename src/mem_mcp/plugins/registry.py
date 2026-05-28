"""Plugin registry — discovers, loads, and orchestrates plugin lifecycle.

Discovery via Python entry points (production) + register_for_test() (tests).
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from mem_mcp.plugins.contract import SDK_API_VERSION, Plugin

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "mem_mcp.skills"


class PluginLoadError(Exception):
    """Raised when a plugin entry point fails to load or instantiate."""


class PluginVersionError(PluginLoadError):
    """Raised when a plugin declares an incompatible api_version."""


class PluginRegistry:
    """Holds loaded plugins by id; dispatches lifecycle calls.

    Use discover() in production code (scans entry points).
    Use register_for_test() in tests.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._discovered = False

    def discover(self) -> None:
        """Scan entry points and instantiate every plugin in the group.

        Idempotent: a second call is a no-op (use clear() to reset for tests).
        """
        if self._discovered:
            return
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            try:
                cls = ep.load()
                if not isinstance(cls, type) or not issubclass(cls, Plugin):
                    raise PluginLoadError(
                        f"entry point {ep.name!r} loaded {cls!r}, expected Plugin subclass"
                    )
                instance = cls()
                self._validate_and_register(instance, source=f"entry_point:{ep.name}")
            except Exception:
                log.exception("plugin_load_failed", extra={"entry_point": ep.name})
                # Continue loading others; one bad plugin doesn't crash startup.
        self._discovered = True

    def register_for_test(self, plugin: Plugin) -> None:
        """Manually register a plugin instance (test-only fast path)."""
        self._validate_and_register(plugin, source="register_for_test")

    def _validate_and_register(self, plugin: Plugin, *, source: str) -> None:
        if plugin.api_version != SDK_API_VERSION:
            raise PluginVersionError(
                f"plugin {plugin.id!r} declares api_version={plugin.api_version!r}, "
                f"core supports {SDK_API_VERSION!r}"
            )
        if not plugin.id or not plugin.id.replace("-", "").replace("_", "").isalnum():
            raise PluginLoadError(f"plugin id must be kebab-case alphanumeric; got {plugin.id!r}")
        if plugin.id in self._plugins:
            raise PluginLoadError(
                f"plugin id {plugin.id!r} already registered "
                f"(existing source: previous; new source: {source})"
            )
        self._plugins[plugin.id] = plugin
        log.info(
            "plugin_registered",
            extra={
                "plugin_id": plugin.id,
                "display_name": plugin.display_name,
                "api_version": plugin.api_version,
                "credential_scope": plugin.credential_scope,
                "source": source,
            },
        )

    def get(self, plugin_id: str) -> Plugin | None:
        return self._plugins.get(plugin_id)

    def all(self) -> list[Plugin]:
        return list(self._plugins.values())

    def clear(self) -> None:
        """Test-only: reset the registry."""
        self._plugins.clear()
        self._discovered = False
