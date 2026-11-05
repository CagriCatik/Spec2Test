"""Legacy compatibility layer.

Everything here is a thin wrapper around ``src/spec2test``. New code should use
the package directly: ``spec2test.application.generate.generate_drafts`` for
generation, ``spec2test.adapters.export`` for CSV and Excel, and
``spec2test.ui`` for the interface.

The implementation now lives in ``src/spec2test``:
    application.chunking   - block building without page loss
    application.legacy     - the deprecated flat-shape bridge for the GUI
    application.contracts  - the strict, versioned model contract
    adapters.ollama        - HTTP transport for cloud and local mode

This module keeps the names the GUI, the workers and the existing tests use.
New code should call ``spec2test.application.generate.generate_drafts``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Sequence

try:  # pragma: no cover - import guard for optional progress dependency
    from tqdm.auto import tqdm
except Exception:  # pragma: no cover - keep working without tqdm installed
    tqdm = None  # type: ignore[assignment]

import requests  # re-exported: tests simulate transport errors through it

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from spec2test.adapters.documents.pdf_import import extract_pages
from spec2test.adapters.ollama.client import (  # noqa: E402
    MODE_CLOUD,
    MODE_LOCAL,
    OllamaChatClient,
    OllamaError,
    OllamaSettings,
)
from spec2test.application.chunking import build_blocks  # noqa: E402
from spec2test.application.contracts import (  # noqa: E402
    ParseOutcome,
    parse_model_response,
)
from spec2test.application.contracts import strip_code_fences as _strip_code_fences
from spec2test.application.legacy import (  # noqa: E402
    LEGACY_KEYS,
    LEGACY_REQUIRED,
    LEGACY_PROMPT_VERSION,
    LEGACY_SCHEMA_VERSION,
    generate_legacy_cases,
    parse_legacy_cases,
)
from spec2test.adapters.export import save_csv as _save_csv  # noqa: E402
from spec2test.adapters.export import save_xlsx as _save_xlsx  # noqa: E402
from spec2test.adapters.export import summarise_rows as _summarise_rows  # noqa: E402
from spec2test.application.ports import ChatMessage  # noqa: E402
from spec2test.domain.models import RunStatus  # noqa: E402

from spec2test.config import (  # noqa: E402
    DEFAULT_GPU_LAYERS,
    DEFAULT_MAIN_GPU,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL_NAME,
    DEFAULT_NUM_GPU,
    DEFAULT_NUM_THREAD,
    DEFAULT_OLLAMA_URL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_K,
    DEFAULT_TOP_P,
    DEFAULT_USE_GPU,
)

# ------------------------------ prompt role ---------------------------------
# The legacy path needs the legacy prompt: it must ask for the flat JSON array
# that `parse_legacy_cases` understands. The files in prompts/ are project
# guidance for the current pipeline and no longer prescribe a format.
PROMPT_FILE = (
    Path(__file__).parent / "prompts" / "legacy" / "Prompt_Expert_Test_Architect.txt"
)
PROMPT_ROLE = PROMPT_FILE.read_text(encoding="utf-8").strip()

# ------------------------------ constants -----------------------------------
DEFAULTS = dict(
    ollama_url=DEFAULT_OLLAMA_URL,
    model=DEFAULT_MODEL_NAME,
    temperature=DEFAULT_TEMPERATURE,
    top_p=DEFAULT_TOP_P,
    top_k=DEFAULT_TOP_K,
    max_tokens=DEFAULT_MAX_TOKENS,
    connect_timeout=10.0,
    read_timeout=300.0,
    use_gpu=DEFAULT_USE_GPU,
    num_gpu=DEFAULT_NUM_GPU,
    main_gpu=DEFAULT_MAIN_GPU,
    gpu_layers=DEFAULT_GPU_LAYERS,
    num_thread=DEFAULT_NUM_THREAD,
    mode=MODE_LOCAL,
)

ALLOWED_KEYS = list(LEGACY_KEYS)
REQUIRED = set(LEGACY_REQUIRED)

__all__ = [
    "AgentLocal",
    "generate_test_cases",
    "save_csv",
    "save_xlsx",
    "DEFAULTS",
    "parse_cases",
    "parse_legacy_cases",
    "parse_model_response",
    "strip_code_fences",
    "chunk_document",
    "build_blocks",
    "KeywordRetriever",
    "load_prompt_file",
    "PromptCache",
    "summarise_cases",
    "RunStatus",
]


# ------------------------------ agent ---------------------------------------
class AgentLocal:
    """Thin wrapper over the Ollama chat adapter.

    Behaviour change versus the baseline: there is no CLI fallback. A transport
    error is raised so the caller can show it instead of silently starting a
    local ``ollama run``.
    """

    def __init__(self, settings: OllamaSettings | None = None, **kwargs):
        """``settings`` is the shared :class:`OllamaSettings`.

        GUI, worker and CLI all build it through
        ``spec2test.bootstrap.ollama_settings_from_config``, so mode, host and
        timeouts cannot drift apart. The keyword arguments remain for callers
        that predate that object.
        """
        cfg = {**DEFAULTS, **{k: v for k, v in kwargs.items() if v is not None}}
        if settings is not None:
            cfg["model"] = settings.model
            cfg["mode"] = settings.mode
            cfg["ollama_url"] = settings.resolved_host
            cfg["connect_timeout"] = settings.connect_timeout_s
            cfg["read_timeout"] = settings.read_timeout_s
        model = kwargs.get("model") or kwargs.get("model_name")
        if model:
            cfg["model"] = model
        url = kwargs.get("url") or kwargs.get("ollama_url")
        if url:
            cfg["ollama_url"] = url

        self.model = cfg["model"]
        self.url = cfg["ollama_url"].rstrip("/")
        self.mode = cfg["mode"]
        self.use_gpu = bool(cfg["use_gpu"])

        num_gpu = cfg["num_gpu"]
        if num_gpu is None:
            num_gpu = -1 if self.use_gpu else 0

        # Kept for the GUI/tests; the adapter receives these only in local mode.
        self.opt = {
            "temperature": cfg["temperature"],
            "top_p": cfg["top_p"],
            "top_k": cfg["top_k"],
            "max_output_tokens": cfg["max_tokens"],
            "num_gpu": num_gpu,
        }
        if cfg["main_gpu"] is not None:
            self.opt["main_gpu"] = cfg["main_gpu"]
        if cfg["gpu_layers"] is not None:
            self.opt["gpu_layers"] = cfg["gpu_layers"]
        if cfg["num_thread"] is not None:
            self.opt["num_thread"] = cfg["num_thread"]

        self.system_prompt = kwargs.get("system_prompt") or PROMPT_ROLE
        self.settings = settings or OllamaSettings(
            model=self.model,
            mode=self.mode,
            host=self.url,
            connect_timeout_s=float(cfg["connect_timeout"]),
            read_timeout_s=float(cfg["read_timeout"]),
            temperature=cfg["temperature"],
            top_p=cfg["top_p"],
            top_k=cfg["top_k"],
            max_tokens=cfg["max_tokens"],
            num_gpu=None if self.mode == MODE_CLOUD else num_gpu,
            main_gpu=cfg["main_gpu"],
            gpu_layers=cfg["gpu_layers"],
            num_thread=cfg["num_thread"],
        )
        self.client = OllamaChatClient(self.settings, log=logging.info)

    # --- utilities ---------------------------------------------------------
    def ensure_server(self, timeout: float = 2.0) -> bool:
        """True when the configured endpoint answers and accepts the credentials."""
        try:
            self.client.check_connection()
            return True
        except OllamaError as exc:
            logging.warning("endpoint check failed: %s", exc)
            return False

    def build_prompt(self, user_content: str, system_prompt: str | None = None) -> str:
        """Kept for compatibility; the adapter sends real chat roles."""
        system = system_prompt or self.system_prompt
        return f"[SYSTEM]\n{system}\n\n[USER]\n{user_content}".strip()

    def messages(
        self, user_content: str, system_prompt: str | None = None
    ) -> list[ChatMessage]:
        return [
            ChatMessage("system", system_prompt or self.system_prompt),
            ChatMessage("user", user_content),
        ]

    def generate(self, content: str, *, system_prompt: str | None = None) -> str:
        reply = self.client.chat(self.messages(content, system_prompt))
        if reply.truncated:
            logging.warning(
                "the model stopped early (done_reason=%s); the answer is incomplete",
                reply.done_reason,
            )
        return reply.text.strip()


# ------------------------------ helpers -------------------------------------
def strip_code_fences(text: str) -> str:
    return _strip_code_fences(text)


def parse_cases(raw: str) -> List[Dict]:
    """Legacy parser. Unusable elements are rejected and logged, never turned
    into empty placeholder cases. Use :func:`parse_legacy_cases` for details."""
    result = parse_legacy_cases(raw)
    for issue in result.issues:
        logging.warning("parse: %s", issue)
    return result.cases


def pdf_to_text_pages(pdf_path: str) -> List[str]:
    return extract_pages(Path(pdf_path))


def _missing_fields(case: dict) -> list[str]:
    return [
        key
        for key in REQUIRED
        if not (
            any(str(v).strip() for v in case[key])
            if isinstance(case.get(key), list)
            else str(case.get(key) or "").strip()
        )
    ]


def load_prompt_file(path: Path | str | None) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        logging.warning("Could not read prompt file %s: %s", path, exc)
        return None


def chunk_document(pages: Sequence[str], combine: int, *, overlap: int = 0) -> list[str]:
    """Combine pages into text blocks. Every page reaches at least one block."""
    return [block.text for block in build_blocks(pages, combine, overlap=overlap)]


class KeywordRetriever:
    """Very small TF-IDF retriever to support RAG without external services."""

    def __init__(self, texts: Sequence[str]):
        if not texts:
            raise ValueError("Cannot build retriever with no texts")
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
        except Exception as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "scikit-learn is required for retrieval augmentation"
            ) from exc

        self.texts = list(texts)
        self.vectorizer = TfidfVectorizer(stop_words="english")
        self.matrix = self.vectorizer.fit_transform(self.texts)

    def top_k(self, query: str, k: int, *, exclude_idx: int | None = None) -> list[str]:
        if not query.strip():
            return []
        vec = self.vectorizer.transform([query])
        scores = (self.matrix @ vec.T).toarray().ravel()
        indexed = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)
        out: list[str] = []
        for idx, _score in indexed:
            if exclude_idx is not None and idx == exclude_idx:
                continue
            out.append(self.texts[idx])
            if len(out) >= k:
                break
        return out


def _log(msg: str, logger: Callable[[str], None] | None) -> None:
    if logger:
        logger(msg)
    else:
        logging.info(msg)


# ------------------------------ cache ---------------------------------------
CACHE_FORMAT = "2"


class PromptCache:
    """Persist validated LLM responses.

    Entries are keyed by endpoint, model, options, prompt text and the prompt/
    schema version. Entries written by an older format are ignored: committed
    cache data is not reference truth, and a cache hit is no approval record.
    """

    def __init__(
        self,
        directory: Path | str,
        *,
        max_entries: int = 256,
        max_age_hours: float | None = None,
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "responses.json"
        self.max_entries = max_entries
        self.max_age_seconds = (
            None if max_age_hours is None else max(0, max_age_hours) * 3600
        )
        self._entries: dict[str, dict] = {}
        self.skipped_legacy = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._entries = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self._entries = {}
            return
        entries = raw.get("entries", raw) if isinstance(raw, dict) else {}
        kept: dict[str, dict] = {}
        for key, entry in entries.items():
            if isinstance(entry, dict) and entry.get("format") == CACHE_FORMAT:
                kept[key] = entry
            else:
                self.skipped_legacy += 1
        if self.skipped_legacy:
            logging.info(
                "ignored %d cache entry/entries without the current format marker",
                self.skipped_legacy,
            )
        self._entries = kept

    def _normalise_cases(self, cases: Sequence[Dict]) -> list[Dict]:
        return json.loads(json.dumps(list(cases)))

    def _prune(self) -> None:
        if self.max_entries <= 0:
            self._entries.clear()
            return
        if len(self._entries) <= self.max_entries:
            return
        ordered = sorted(
            self._entries.items(), key=lambda item: item[1].get("ts", 0), reverse=True
        )
        self._entries = dict(ordered[: self.max_entries])

    def _expired(self, entry: dict) -> bool:
        if not entry:
            return True
        if self.max_age_seconds is None:
            return False
        ts = entry.get("ts")
        if ts is None:
            return True
        return (time.time() - float(ts)) > self.max_age_seconds

    def get(self, key: str) -> list[Dict] | None:
        entry = self._entries.get(key)
        if not entry or self._expired(entry):
            if entry:
                self._entries.pop(key, None)
            return None
        return self._normalise_cases(entry.get("cases", []))

    def set(self, key: str, cases: Sequence[Dict]) -> None:
        self._entries[key] = {
            "format": CACHE_FORMAT,
            "cases": self._normalise_cases(cases),
            "ts": time.time(),
        }
        self._prune()

    def flush(self) -> None:
        if not self._entries:
            if self.path.exists():
                try:
                    self.path.unlink()
                except OSError:
                    pass
            return
        payload = {"format": CACHE_FORMAT, "entries": self._entries}
        try:
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            logging.warning("Failed to persist cache at %s", self.path)


def _cache_key(prompt: str, agent, *, system_prompt: str | None) -> str:
    payload = {
        "prompt": prompt,
        "system": system_prompt or getattr(agent, "system_prompt", ""),
        "model": getattr(agent, "model", "unknown"),
        "endpoint": getattr(getattr(agent, "settings", None), "endpoint", None)
        or getattr(agent, "url", "unknown"),
        "options": getattr(agent, "opt", {}),
        "prompt_version": LEGACY_PROMPT_VERSION,
        "schema_version": LEGACY_SCHEMA_VERSION,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ------------------------------ core ----------------------------------------
def generate_test_cases(
    pdf_path: str,
    agent,
    n_cases: int = 6,
    combine: int = 3,
    *,
    prompt_role: str | None = None,
    use_rag: bool = False,
    rag_top_k: int = 2,
    chunk_overlap: int = 0,
    progress_cb: Callable[[int], None] | None = None,
    show_progress: bool = False,
    logger: Callable[[str], None] | None = None,
    cache: PromptCache | None = None,
    should_cancel: Callable[[], bool] | None = None,
    return_run: bool = False,
):
    """Legacy entry point used by the GUI worker.

    Delegates to :func:`spec2test.application.legacy.generate_legacy_cases`.
    With ``return_run=True`` the :class:`GenerationRun` is returned as well, so
    a caller can show ``completed`` / ``partial`` / ``failed`` / ``cancelled``
    instead of assuming success.
    """
    pages = pdf_to_text_pages(pdf_path)
    if not pages:
        raise RuntimeError(f"No text extracted from {pdf_path!s}")

    blocks = build_blocks(pages, combine, overlap=chunk_overlap)
    _log(
        f"Loaded {len(pages)} page(s); processing {len(blocks)} block(s) with "
        f"combine={combine} and overlap={chunk_overlap}",
        logger,
    )

    retriever: KeywordRetriever | None = None
    if use_rag and len(blocks) > 1:
        try:
            retriever = KeywordRetriever([block.text for block in blocks])
            _log("RAG retriever initialised (TF-IDF)", logger)
        except Exception as exc:
            _log(f"⚠️  Failed to initialise retriever: {exc}", logger)
            retriever = None

    bar = None
    if show_progress and tqdm is not None and blocks:
        bar = tqdm(total=len(blocks), desc="Blocks", unit="block")

    def build_prompt(block, total: int, text: str) -> str:
        prompt = (
            f"Generate at most {n_cases} independent test cases following the JSON "
            "schema, including the 'linked_requirements' field.\n-----\n"
            f"Requirements excerpt ({block.index}/{total}, {block.label()}):\n"
            f"{text}\n-----"
        )
        if retriever:
            related = retriever.top_k(text, rag_top_k, exclude_idx=block.index - 1)
            if related:
                prompt += "\nRelated context (auto-selected):\n" + "\n-----\n".join(related)
        return prompt

    cache_hits = 0

    def cache_get(prompt: str) -> list[Dict] | None:
        nonlocal cache_hits
        if not cache:
            return None
        hit = cache.get(_cache_key(prompt, agent, system_prompt=prompt_role))
        if hit is not None:
            cache_hits += 1
        return hit

    def cache_set(prompt: str, cases: Sequence[Dict]) -> None:
        if cache and cases:
            cache.set(_cache_key(prompt, agent, system_prompt=prompt_role), list(cases))

    def progress(percent: int) -> None:
        if bar is not None:
            bar.update(1)
        if progress_cb:
            progress_cb(percent)

    if progress_cb:
        progress_cb(0)

    try:
        cases, run = generate_legacy_cases(
            pages,
            agent,
            document_id=Path(pdf_path).name,
            combine=combine,
            overlap=chunk_overlap,
            n_cases=n_cases,
            prompt_role=prompt_role,
            build_prompt=build_prompt,
            model_name=getattr(agent, "model", "unknown"),
            endpoint=getattr(agent, "url", "unknown"),
            log=lambda msg: _log(msg, logger),
            progress=progress,
            should_cancel=should_cancel,
            cache_get=cache_get if cache else None,
            cache_set=cache_set if cache else None,
        )
    finally:
        if bar is not None:
            bar.close()
        if cache:
            cache.flush()

    if progress_cb:
        # 100% only means the processing ended; run.status says whether it worked.
        progress_cb(100)

    _log(
        f"Run {run.run_id} finished with status '{run.status.value}': "
        f"{len(cases)} test case(s) from {len(blocks)} block(s)"
        + (f", {cache_hits} cache hit(s)" if cache_hits else ""),
        logger,
    )
    if return_run:
        return cases, run
    return cases


def summarise_cases(cases: Sequence[Dict]) -> Dict[str, object]:
    """Simple analytics about generated cases. See adapters.export."""
    return _summarise_rows(cases)


# ------------------------------ writers -------------------------------------
# The exporters moved to spec2test.adapters.export; these names stay for
# existing scripts.
def save_csv(cases: List[Dict], path: str) -> None:
    _save_csv(cases, path)


def save_xlsx(cases: List[Dict], path: str) -> None:
    _save_xlsx(cases, path)
