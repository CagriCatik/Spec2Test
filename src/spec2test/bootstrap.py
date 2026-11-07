"""Load configuration and wire concrete adapters."""
from __future__ import annotations

import os
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
CONFIG_FILENAME = "config.yml"


ENV_FILENAME = ".env"


def load_env_file(path: Path | str | None = None, *, override: bool = False) -> list[str]:
    """Read ``KEY=VALUE`` lines from a ``.env`` file into the environment.

    Returns the names of the variables that were set - never their values, so a
    caller cannot log a secret by accident. An already exported variable wins
    unless ``override`` is set, and the file is never written to.

    A tiny parser instead of a dependency: it handles comments, blank lines,
    ``export`` prefixes and quoted values, which is all a key file needs.
    """
    candidates = (
        [Path(path)]
        if path is not None
        else [Path.cwd() / ENV_FILENAME, PROJECT_ROOT / ENV_FILENAME]
    )
    env_path = next((c for c in candidates if c.exists()), None)
    if env_path is None:
        return []

    applied: list[str] = []
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in os.environ and not override:
            continue
        os.environ[name] = value
        applied.append(name)
    return applied


def default_config_path() -> Path | None:
    """Find a configuration file.

    The working directory wins, so an installed ``spec2test`` command uses the
    configuration of the project it is run in. The repository checkout is only
    the fallback for running straight from the source tree.
    """
    for candidate in (Path.cwd() / CONFIG_FILENAME, DEFAULT_CONFIG_PATH):
        if candidate.exists():
            return candidate
    return None


@dataclass
class AppSettings:
    mode: str = MODE_CLOUD
    ollama: OllamaSettings | None = None
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    documents: list[Path] = field(default_factory=list)
    output_dir: Path = field(default_factory=lambda: Path.cwd() / "runs")


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Read config.yml if present. A missing file is not an error."""
    import yaml

    config_path = Path(path) if path else default_config_path()
    if config_path is None or not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def ollama_settings_from_config(
    config: dict[str, Any], *, mode: str | None = None, model: str | None = None
) -> OllamaSettings:
    model_cfg = config.get("model") or {}
    cloud_cfg = config.get("cloud") or {}
    # no configured mode means the local daemon: it needs no credentials
    resolved_mode = mode or cloud_cfg.get("mode") or MODE_LOCAL
    if resolved_mode == MODE_FAKE:
        resolved_mode = MODE_CLOUD  # only used to build a descriptor shape
    # an explicit host always wins; otherwise the mode decides the default
    host = cloud_cfg.get("host") or (
        None if resolved_mode == MODE_CLOUD else model_cfg.get("url")
    )
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
    """The single conversion used by the GUI, the Qt worker and the CLI.

    ``cases_per_prompt`` of the legacy configuration is accepted as a fallback,
    but an explicit ``max_cases_per_unit`` always wins - otherwise an old
    widget value would silently override the new setting.
    """
    gen = config.get("generation") or {}
    cloud = config.get("cloud") or {}
    max_cases = gen.get("max_cases_per_unit")
    if max_cases is None:
        max_cases = gen.get("cases_per_prompt", 5)
    job_timeout = gen.get("job_timeout_s", cloud.get("job_timeout_s", 900.0))
    return GenerationSettings(
        use_reference_context=bool(gen.get("use_reference_context", True)),
        use_retrieval=bool(gen.get("use_retrieval", False)),
        max_search_items=max(0, int(gen.get("max_search_items", 3))),
        retrieval=str(gen.get("retrieval", "lexical")).strip().lower(),
        max_task_items=max(0, int(gen.get("max_task_items", 2))),
        embedding_model=str(gen.get("embedding_model", "nomic-embed-text")),
        context_char_budget=max(0, int(gen.get("context_char_budget", 6_000))),
        max_context_items=max(0, int(gen.get("max_context_items", 5))),
        max_cases_per_unit=max(1, int(max_cases)),
        max_repair_attempts=max(0, int(gen.get("max_repair_attempts", 2))),
        group_size=max(1, int(gen.get("group_size", 1))),
        input_char_budget=max(1, int(gen.get("input_char_budget", 12_000))),
        job_timeout_s=job_timeout,
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
        # offline stand-in; any settings are ignored on purpose
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

def build_knowledge(index_path, settings, *, log=None):
    """The retriever a run should use, from the settings.

    ``lexical``  the SQLite/FTS5 index alone.
    ``hybrid``   the same index fused with embeddings of the same passages.
                 When the embedding model cannot be reached the run falls
                 back to lexical and says so: a missing model must not stop a
                 generation, but it must not pass unnoticed either.
    """
    from .adapters.knowledge import SqliteKnowledgeIndex

    lexical = SqliteKnowledgeIndex(index_path)
    if settings.retrieval != "hybrid":
        return lexical

    from .adapters.knowledge import (
        EmbeddingUnavailable,
        HybridKnowledgeIndex,
        OllamaEmbedder,
        VectorKnowledgeIndex,
    )

    cache = Path(index_path).with_suffix(".vectors.json")
    vector = VectorKnowledgeIndex(
        lexical, OllamaEmbedder(model=settings.embedding_model), cache_path=cache
    )
    try:
        embedded = vector.build()
    except EmbeddingUnavailable as exc:
        if log:
            log(
                f"warning: hybrid retrieval was requested but is unavailable "
                f"({exc}). Falling back to lexical search."
            )
        return lexical
    if log:
        log(
            f"hybrid retrieval: {settings.embedding_model}, "
            f"{embedded} passage(s) embedded in this run"
        )
    return HybridKnowledgeIndex(lexical, vector)

