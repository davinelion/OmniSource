"""Extensible plugin ports for formats, validators, enrichers, and feeds.

A plugin is a small object with a stable name and one capability.  Core
pipeline code depends on these protocols, not on a concrete source format.
Third-party plugins can be loaded from an explicit allow-list in
``config/plugins.json``; arbitrary filesystem modules are never auto-imported.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@runtime_checkable
class SourceFormatPlugin(Protocol):
    name: str
    supported_types: tuple[str, ...]

    def detect(self, payload: Any, url: str = "") -> bool: ...

    def normalize(self, payload: Any, *, url: str = "") -> dict[str, Any]: ...


@runtime_checkable
class ValidatorPlugin(Protocol):
    name: str

    def validate(self, payload: Any, *, context: dict[str, Any] | None = None) -> list[str]: ...


@runtime_checkable
class EnrichmentPlugin(Protocol):
    name: str

    def enrich(self, record: dict[str, Any], *, context: dict[str, Any] | None = None) -> dict[str, Any]: ...


@runtime_checkable
class FeedGeneratorPlugin(Protocol):
    name: str
    formats: tuple[str, ...]

    def generate(self, records: list[dict[str, Any]], *, metadata: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass
class PluginManager:
    """Registry for built-ins and explicitly enabled external plugins."""

    formats: dict[str, SourceFormatPlugin] = field(default_factory=dict)
    validators: dict[str, ValidatorPlugin] = field(default_factory=dict)
    enrichers: dict[str, EnrichmentPlugin] = field(default_factory=dict)
    feed_generators: dict[str, FeedGeneratorPlugin] = field(default_factory=dict)

    def register(self, plugin: Any) -> Any:
        """Register a plugin by capability; return it for decorator use."""
        name = str(getattr(plugin, "name", "")).strip()
        if not name or any(char in name for char in "/\\\x00"):
            raise ValueError("plugin name must be a non-empty safe identifier")
        registered = False
        if isinstance(plugin, SourceFormatPlugin):
            for source_type in plugin.supported_types:
                self.formats[str(source_type)] = plugin
            registered = True
        if isinstance(plugin, ValidatorPlugin):
            self.validators[name] = plugin
            registered = True
        if isinstance(plugin, EnrichmentPlugin):
            self.enrichers[name] = plugin
            registered = True
        if isinstance(plugin, FeedGeneratorPlugin):
            for format_name in plugin.formats:
                self.feed_generators[str(format_name)] = plugin
            registered = True
        if not registered:
            raise TypeError(f"plugin {name!r} implements no supported capability")
        return plugin

    def format_for(self, source_type: str) -> SourceFormatPlugin | None:
        return self.formats.get(source_type)

    def validate(self, payload: Any, *, context: dict[str, Any] | None = None) -> list[str]:
        errors: list[str] = []
        for plugin in self.validators.values():
            errors.extend(str(error) for error in plugin.validate(payload, context=context))
        return errors

    def enrich(self, record: dict[str, Any], *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        value = dict(record)
        for plugin in self.enrichers.values():
            candidate = plugin.enrich(dict(value), context=context)
            if not isinstance(candidate, dict):
                raise TypeError(f"enrichment plugin {plugin.name!r} returned a non-object")
            value = candidate
        return value

    def generate_feed(
        self,
        format_name: str,
        records: list[dict[str, Any]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plugin = self.feed_generators.get(format_name)
        if plugin is None:
            raise KeyError(f"no feed generator registered for {format_name!r}")
        result = plugin.generate(records, metadata=metadata)
        if not isinstance(result, dict):
            raise TypeError(f"feed plugin {plugin.name!r} returned a non-object")
        return result


def load_entry_points(manager: PluginManager, *, group: str = "omnisource.plugins") -> list[str]:
    """Load installed entry points from a known packaging group.

    Entry-point loading is opt-in and failures are returned to the caller as
    names rather than aborting the core build.  A production deployment should
    pin plugin distributions in its lockfile and set an allow-list in config.
    """
    loaded: list[str] = []
    entries = importlib.metadata.entry_points()
    selected = entries.select(group=group) if hasattr(entries, "select") else entries.get(group, [])
    for entry in selected:
        try:
            plugin = entry.load()
            if callable(plugin) and not hasattr(plugin, "name"):
                plugin = plugin()
            manager.register(plugin)
            loaded.append(entry.name)
        except Exception:  # plugin isolation: core remains available
            log.exception("failed to load plugin %s", entry.name)
    return loaded


def load_allowlisted_modules(manager: PluginManager, modules: list[str]) -> list[str]:
    """Load ``module:attribute`` references from an explicit allow-list."""
    loaded: list[str] = []
    for reference in modules:
        if not isinstance(reference, str) or reference.count(":") != 1:
            raise ValueError(f"invalid plugin reference: {reference!r}")
        module_name, attribute = reference.split(":", 1)
        if not module_name or not attribute or not module_name.startswith(("plugins.", "omnisource_")):
            raise ValueError(f"plugin reference is not allow-listed: {reference!r}")
        plugin = getattr(importlib.import_module(module_name), attribute)
        manager.register(plugin() if callable(plugin) and not hasattr(plugin, "name") else plugin)
        loaded.append(reference)
    return loaded


class JsonFeedPlugin:
    """Built-in pass-through normalizer for AltStore-family JSON feeds."""

    name = "json-feed"
    supported_types = ("json-feed", "altstore", "sidestore", "flarestore", "feather", "esign", "ksign", "livecontainer")

    def detect(self, payload: Any, url: str = "") -> bool:
        return isinstance(payload, dict) and isinstance(payload.get("apps"), list)

    def normalize(self, payload: Any, *, url: str = "") -> dict[str, Any]:
        if not self.detect(payload, url):
            raise ValueError("payload is not an AltStore-family JSON feed")
        value = dict(payload)
        value.setdefault("sourceURL", url)
        return value


class MetadataCompletenessValidator:
    """Built-in validator that is intentionally independent of network I/O."""

    name = "metadata-completeness"

    def validate(self, payload: Any, *, context: dict[str, Any] | None = None) -> list[str]:
        if not isinstance(payload, dict):
            return ["payload must be an object"]
        errors: list[str] = []
        for required_field in ("name", "identifier", "apps"):
            if not payload.get(required_field):
                errors.append(f"missing {required_field}")
        if not isinstance(payload.get("apps"), list):
            errors.append("apps must be an array")
        return errors


def build_default_plugin_manager() -> PluginManager:
    manager = PluginManager()
    manager.register(JsonFeedPlugin())
    manager.register(MetadataCompletenessValidator())
    return manager


__all__ = [
    "EnrichmentPlugin",
    "FeedGeneratorPlugin",
    "JsonFeedPlugin",
    "MetadataCompletenessValidator",
    "PluginManager",
    "SourceFormatPlugin",
    "ValidatorPlugin",
    "build_default_plugin_manager",
    "load_allowlisted_modules",
    "load_entry_points",
]
