"""Load configuration and wire concrete adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .adapters.documents import DEFAULT_IMPORTERS
from .adapters.fake.model import RuleBasedFakeModel
from .adapters.ollama.client import (
    MODE_CLOUD,
    MODE_LOCAL,
    OllamaChatClient,
    OllamaSettings,
)
from .application.generate import GenerationSettings
from .application.ports import DocumentImportPort, ModelPort

MODE_FAKE = "fake"
ALL_MODES = (MODE_CLOUD, MODE_LOCAL, MODE_FAKE)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yml"


@dataclass
class AppSettings:
    mode: str = MODE_CLOUD
    ollama: OllamaSettings | None = None
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    documents: list[Path] = field(default_factory=list)
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "runs")


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Read config.yml if present. A missing file is not an error."""
    import yaml

    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def ollama_settings_from_config(
    config: dict[str, Any], *, mode: str | None = None, model: str | None = None
) -> OllamaSettings:
    model_cfg = config.get("model") or {}
    cloud_cfg = config.get("cloud") or {}
    resolved_mode = mode or cloud_cfg.get("mode") or MODE_CLOUD
    if resolved_mode == MODE_FAKE:
        resolved_mode = MODE_CLOUD  # only used to build a descriptor shape
    host = cloud_cfg.get("host") if resolved_mode == MODE_CLOUD else model_cfg.get("url")
    return OllamaSettings(
        model=model or model_cfg.get("name") or "gpt-oss:120b",
        mode=resolved_mode,
        host=host,
        connect_timeout_s=float(cloud_cfg.get("connect_timeout_s", 10.0)),
        read_timeout_s=float(cloud_cfg.get("read_timeout_s", 300.0)),
        max_retries=int(cloud_cfg.get("max_retries", 2)),
        temperature=float(model_cfg.get("temperature", 0.2)),
        top_p=float(model_cfg.get("top_p", 0.9)),
        top_k=int(model_cfg.get("top_k", 40)),
        max_tokens=model_cfg.get("max_tokens") or None,
        num_gpu=model_cfg.get("num_gpu"),
        main_gpu=model_cfg.get("main_gpu"),
        gpu_layers=model_cfg.get("gpu_layers"),
        num_thread=model_cfg.get("num_thread"),
    )


def generation_settings_from_config(config: dict[str, Any]) -> GenerationSettings:
    gen = config.get("generation") or {}
    return GenerationSettings(
        max_cases_per_unit=int(gen.get("max_cases_per_unit", 5)),
        max_repair_attempts=int(gen.get("max_repair_attempts", 2)),
        group_size=int(gen.get("group_size", 1)),
        input_char_budget=int(gen.get("input_char_budget", 12_000)),
        job_timeout_s=gen.get("job_timeout_s", 900.0),
    )


def build_model(
    mode: str,
    settings: OllamaSettings | None = None,
    *,
    api_key: str | None = None,
    api_key_provider: Callable[[], str | None] | None = None,
    log: Callable[[str], None] | None = None,
) -> ModelPort:
    """Build the model adapter for an explicit mode.

    There is no implicit fallback: a cloud failure never silently becomes a
    local run, and never starts the local Ollama CLI.
    """
    if mode == MODE_FAKE:
        return RuleBasedFakeModel()
    if mode not in (MODE_CLOUD, MODE_LOCAL):
        raise ValueError(f"mode must be one of {ALL_MODES}, got {mode!r}")
    if settings is None:
        raise ValueError(f"mode {mode!r} needs OllamaSettings")
    if settings.mode != mode:
        raise ValueError(
            f"settings mode {settings.mode!r} does not match requested mode {mode!r}"
        )
    return OllamaChatClient(
        settings, api_key=api_key, api_key_provider=api_key_provider, log=log
    )


def build_importers() -> Sequence[DocumentImportPort]:
    return DEFAULT_IMPORTERS
