"""Model list and known capabilities of the selected operating mode.

Cloud API names and local cloud aliases are deliberately NOT treated as
equivalent: ``gpt-oss:120b`` (API) and ``gpt-oss:120b-cloud`` (local alias) are
different strings and are never rewritten for the user.
"""
from __future__ import annotations

from dataclasses import dataclass

from .client import MODE_CLOUD, MODE_LOCAL, OllamaChatClient


@dataclass(frozen=True)
class ModelCapabilities:
    """What the application may assume about a model in a given mode."""

    name: str
    mode: str
    supports_schema_enforcement: bool
    supports_gpu_options: bool
    notes: tuple[str, ...] = ()


_CLOUD_NOTE = (
    "Ollama Cloud does not support schema-enforced structured outputs; the "
    "'format' parameter must not be assumed. Local schema validation is "
    "mandatory."
)
_ALIAS_NOTE = (
    "The name ends in ':cloud' or '-cloud', which is the local CLI/app alias. "
    "The cloud API expects the name reported by /api/tags. The two are not "
    "mapped automatically."
)


def is_local_cloud_alias(name: str) -> bool:
    """Both ``gemma4:cloud`` and ``gpt-oss:120b-cloud`` are local aliases."""
    lowered = name.strip().lower()
    return lowered.endswith(":cloud") or lowered.endswith("-cloud")


def capabilities_for(name: str, mode: str) -> ModelCapabilities:
    notes: list[str] = []
    if mode == MODE_CLOUD:
        notes.append(_CLOUD_NOTE)
        if is_local_cloud_alias(name):
            notes.append(_ALIAS_NOTE)
    return ModelCapabilities(
        name=name,
        mode=mode,
        supports_schema_enforcement=False,
        supports_gpu_options=(mode == MODE_LOCAL),
        notes=tuple(notes),
    )


def list_models(client: OllamaChatClient) -> list[str]:
    """Model names from /api/tags of the configured host."""
    return sorted(client.list_model_names())


def check_model_choice(name: str, available: list[str], mode: str) -> list[str]:
    """Warnings about a chosen model. A manual name is always allowed."""
    warnings: list[str] = []
    if available and name not in available:
        warnings.append(
            f"model {name!r} is not in the list reported by the configured host "
            f"({len(available)} available). Manual names are allowed, but the "
            "request may fail with 404."
        )
        if mode == MODE_CLOUD and is_local_cloud_alias(name):
            warnings.append(_ALIAS_NOTE)
    return warnings
