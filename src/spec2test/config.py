"""Configuration dataclasses shared by the UI, the workers and the CLI.

Pure data: no I/O, no Qt. ``bootstrap`` turns these into adapter settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# Default constants shared across the application
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL_NAME = "deepseek-r1:14b"
DEFAULT_TEMPERATURE = 0.4
DEFAULT_TOP_P = 0.9
DEFAULT_TOP_K = 64
DEFAULT_MAX_TOKENS = 8192
DEFAULT_USE_GPU = True
DEFAULT_NUM_GPU: Optional[int] = None
DEFAULT_MAIN_GPU: Optional[int] = None
DEFAULT_GPU_LAYERS: Optional[int] = None
DEFAULT_NUM_THREAD: Optional[int] = None

# Explicit operating modes; there is no implicit switching between them.
MODE_CLOUD = "ollama_cloud"
MODE_LOCAL = "ollama_local"
# Without a configuration file the local daemon is the default: it needs no
# credentials. ollama_cloud must be chosen deliberately.
DEFAULT_MODE = MODE_LOCAL
DEFAULT_CLOUD_HOST = "https://ollama.com"
DEFAULT_CONNECT_TIMEOUT_S = 10.0
DEFAULT_READ_TIMEOUT_S = 300.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_JOB_TIMEOUT_S = 900.0

DEFAULT_CACHE_ENABLED = False
DEFAULT_CACHE_MAX_ENTRIES = 256
DEFAULT_CACHE_MAX_AGE_HOURS: Optional[int] = 24
DEFAULT_CACHE_DIRECTORY = Path.home() / ".tsg-cache"

DEFAULT_CASES_PER_PROMPT = 6
DEFAULT_MAX_CASES_PER_UNIT = 5
DEFAULT_MAX_REPAIR_ATTEMPTS = 2
DEFAULT_GROUP_SIZE = 1
DEFAULT_INPUT_CHAR_BUDGET = 12_000
DEFAULT_COMBINE_PAGES = 3
DEFAULT_BATCH_SIZE = 1
DEFAULT_PARALLEL_PROMPTS = 1


@dataclass
class ModelConfig:
    """Settings controlling how the local LLM is queried."""

    name: str = DEFAULT_MODEL_NAME
    url: str = DEFAULT_OLLAMA_URL
    temperature: float = DEFAULT_TEMPERATURE
    top_p: float = DEFAULT_TOP_P
    top_k: int = DEFAULT_TOP_K
    max_tokens: int = DEFAULT_MAX_TOKENS
    http_timeout: Optional[float] = None
    use_gpu: bool = DEFAULT_USE_GPU
    num_gpu: Optional[int] = DEFAULT_NUM_GPU
    main_gpu: Optional[int] = DEFAULT_MAIN_GPU
    gpu_layers: Optional[int] = DEFAULT_GPU_LAYERS
    num_thread: Optional[int] = DEFAULT_NUM_THREAD

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "ModelConfig":
        if not data:
            data = {}
        # Accept historical keys ("model_name", "ollama_url") for compatibility
        mapping = {
            "name": data.get("name") or data.get("model_name"),
            "url": data.get("url") or data.get("ollama_url"),
            "temperature": data.get("temperature"),
            "top_p": data.get("top_p"),
            "top_k": data.get("top_k"),
            "max_tokens": data.get("max_tokens"),
            "http_timeout": data.get("http_timeout"),
            "use_gpu": data.get("use_gpu"),
            "num_gpu": data.get("num_gpu"),
            "main_gpu": data.get("main_gpu"),
            "gpu_layers": data.get("gpu_layers"),
            "num_thread": data.get("num_thread"),
        }
        return cls(**{k: v for k, v in mapping.items() if v is not None})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "max_tokens": self.max_tokens,
            "http_timeout": self.http_timeout,
            "use_gpu": self.use_gpu,
            "num_gpu": self.num_gpu,
            "main_gpu": self.main_gpu,
            "gpu_layers": self.gpu_layers,
            "num_thread": self.num_thread,
        }


@dataclass
class CloudConfig:
    """Endpoint and transport settings shared by the GUI, the worker and the CLI.

    The API key is never part of this object: it is read from $OLLAMA_API_KEY.
    """

    mode: str = DEFAULT_MODE
    host: Optional[str] = None
    connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S
    read_timeout_s: float = DEFAULT_READ_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    job_timeout_s: Optional[float] = DEFAULT_JOB_TIMEOUT_S

    @property
    def is_cloud(self) -> bool:
        return self.mode == MODE_CLOUD

    def resolved_host(self, local_url: str = DEFAULT_OLLAMA_URL) -> str:
        """The host actually used for this mode."""
        if self.host:
            return self.host.rstrip("/")
        return DEFAULT_CLOUD_HOST if self.is_cloud else local_url.rstrip("/")

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "CloudConfig":
        if not data:
            data = {}
        mapping = {
            "mode": data.get("mode"),
            "host": data.get("host"),
            "connect_timeout_s": data.get("connect_timeout_s"),
            "read_timeout_s": data.get("read_timeout_s"),
            "max_retries": data.get("max_retries"),
            "job_timeout_s": data.get("job_timeout_s"),
        }
        return cls(**{k: v for k, v in mapping.items() if v is not None})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "host": self.host,
            "connect_timeout_s": self.connect_timeout_s,
            "read_timeout_s": self.read_timeout_s,
            "max_retries": self.max_retries,
            "job_timeout_s": self.job_timeout_s,
        }


@dataclass
class GenerationConfig:
    """Limits of the current pipeline, plus the legacy chunking values.

    The first four fields drive ``application.generate``. The remaining ones
    belong to the legacy flat path and are kept so existing configurations and
    scripts keep working.
    """

    max_cases_per_unit: int = DEFAULT_MAX_CASES_PER_UNIT
    max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS
    group_size: int = DEFAULT_GROUP_SIZE
    input_char_budget: int = DEFAULT_INPUT_CHAR_BUDGET
    # legacy flat path only
    cases_per_prompt: int = DEFAULT_CASES_PER_PROMPT
    combine_pages: int = DEFAULT_COMBINE_PAGES
    batch_size: int = DEFAULT_BATCH_SIZE
    parallel_prompts: int = DEFAULT_PARALLEL_PROMPTS

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "GenerationConfig":
        if not data:
            data = {}
        mapping = {
            "max_cases_per_unit": data.get("max_cases_per_unit"),
            "max_repair_attempts": data.get("max_repair_attempts"),
            "group_size": data.get("group_size"),
            "input_char_budget": data.get("input_char_budget"),
            "cases_per_prompt": data.get("cases_per_prompt"),
            "combine_pages": data.get("combine_pages"),
            "batch_size": data.get("batch_size"),
            "parallel_prompts": data.get("parallel_prompts"),
        }
        return cls(**{k: v for k, v in mapping.items() if v is not None})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_cases_per_unit": self.max_cases_per_unit,
            "max_repair_attempts": self.max_repair_attempts,
            "group_size": self.group_size,
            "input_char_budget": self.input_char_budget,
            "cases_per_prompt": self.cases_per_prompt,
            "combine_pages": self.combine_pages,
            "batch_size": self.batch_size,
            "parallel_prompts": self.parallel_prompts,
        }


@dataclass
class RagConfig:
    """Options for lightweight retrieval augmented generation."""

    enabled: bool = False
    top_k: int = 2
    chunk_overlap: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "RagConfig":
        if not data:
            data = {}
        mapping = {
            "enabled": data.get("enabled") if "enabled" in data else data.get("use_rag"),
            "top_k": data.get("top_k"),
            "chunk_overlap": data.get("chunk_overlap"),
        }
        return cls(**{k: v for k, v in mapping.items() if v is not None})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "top_k": self.top_k,
            "chunk_overlap": self.chunk_overlap,
        }


@dataclass
class PathConfig:
    """Holds user-selected file paths."""

    pdf_path: Optional[Path] = None
    prompt_path: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "PathConfig":
        if not data:
            data = {}
        pdf = data.get("pdf_path")
        prompt = data.get("prompt_path")
        return cls(
            pdf_path=Path(pdf) if pdf else None,
            prompt_path=Path(prompt) if prompt else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "prompt_path": str(self.prompt_path) if self.prompt_path else None,
        }


@dataclass
class CacheConfig:
    """Disk caching settings for LLM responses."""

    enabled: bool = DEFAULT_CACHE_ENABLED
    directory: Optional[Path] = DEFAULT_CACHE_DIRECTORY
    max_entries: int = DEFAULT_CACHE_MAX_ENTRIES
    max_age_hours: Optional[int] = DEFAULT_CACHE_MAX_AGE_HOURS

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "CacheConfig":
        if not data:
            data = {}
        directory = data.get("directory") or data.get("path")
        max_age = data.get("max_age_hours")
        if max_age == 0:
            max_age = None
        # Accept historical key names when present
        mapping = {
            "enabled": data.get("enabled"),
            "directory": Path(directory).expanduser() if directory else None,
            "max_entries": data.get("max_entries"),
            "max_age_hours": max_age,
        }
        return cls(**{k: v for k, v in mapping.items() if v is not None})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "directory": str(self.directory) if self.directory else None,
            "max_entries": self.max_entries,
            "max_age_hours": self.max_age_hours,
        }


@dataclass
class AppConfig:
    """Full configuration snapshot used by the worker pipeline."""

    model: ModelConfig = field(default_factory=ModelConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    paths: PathConfig = field(default_factory=PathConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "AppConfig":
        """Build from nested dict or older flat layout."""
        if not data:
            data = {}

        if any(key in data for key in ("model", "generation", "rag", "paths", "cloud")):
            return cls(
                model=ModelConfig.from_dict(data.get("model")),
                cloud=CloudConfig.from_dict(data.get("cloud")),
                generation=GenerationConfig.from_dict(data.get("generation")),
                rag=RagConfig.from_dict(data.get("rag")),
                cache=CacheConfig.from_dict(data.get("cache")),
                paths=PathConfig.from_dict(data.get("paths")),
            )

        # Fallback: historical flat config.yml structure
        return cls(
            model=ModelConfig.from_dict(data),
            cloud=CloudConfig.from_dict(data),
            generation=GenerationConfig.from_dict(data),
            rag=RagConfig.from_dict(data),
            cache=CacheConfig.from_dict(data),
            paths=PathConfig.from_dict(data),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "cloud": self.cloud.to_dict(),
            "generation": self.generation.to_dict(),
            "rag": self.rag.to_dict(),
            "cache": self.cache.to_dict(),
            "paths": self.paths.to_dict(),
        }


__all__ = [
    "MODE_CLOUD",
    "MODE_LOCAL",
    "DEFAULT_MODE",
    "DEFAULT_CLOUD_HOST",
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DEFAULT_READ_TIMEOUT_S",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_JOB_TIMEOUT_S",
    "DEFAULT_MODEL_NAME",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TOP_P",
    "DEFAULT_TOP_K",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_USE_GPU",
    "DEFAULT_NUM_GPU",
    "DEFAULT_MAIN_GPU",
    "DEFAULT_GPU_LAYERS",
    "DEFAULT_NUM_THREAD",
    "DEFAULT_CACHE_ENABLED",
    "DEFAULT_CACHE_MAX_ENTRIES",
    "DEFAULT_CACHE_MAX_AGE_HOURS",
    "DEFAULT_CACHE_DIRECTORY",
    "DEFAULT_CASES_PER_PROMPT",
    "DEFAULT_MAX_CASES_PER_UNIT",
    "DEFAULT_MAX_REPAIR_ATTEMPTS",
    "DEFAULT_GROUP_SIZE",
    "DEFAULT_INPUT_CHAR_BUDGET",
    "DEFAULT_COMBINE_PAGES",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_PARALLEL_PROMPTS",
    "AppConfig",
    "CacheConfig",
    "CloudConfig",
    "GenerationConfig",
    "ModelConfig",
    "PathConfig",
    "RagConfig",
]
