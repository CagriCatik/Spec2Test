"""Interfaces between the application and its adapters.

Qt signals, HTTP response objects and library-specific types must not cross
these boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, Sequence, runtime_checkable

from ..domain.models import GenerationRun, SourceDocument, SourceSpan
from .deadline import Deadline

Role = Literal["system", "user", "assistant"]


# ------------------------------------------------------------------ model ---
@dataclass(frozen=True)
class ChatMessage:
    """A real chat role, not a concatenated single prompt string."""

    role: Role
    content: str


@dataclass(frozen=True)
class ModelDescriptor:
    """What the application records about the model it talked to."""

    model: str
    endpoint: str
    mode: str
    parameters: dict = field(default_factory=dict)
    supports_schema_enforcement: bool = False


@dataclass(frozen=True)
class ModelReply:
    """A model answer, stripped of all transport details."""

    text: str
    truncated: bool = False
    done_reason: str | None = None
    duration_s: float | None = None
    model: str | None = None


class ModelError(RuntimeError):
    """Base class for all model/transport failures surfaced to the application."""

    retryable = False


@runtime_checkable
class ModelPort(Protocol):
    """A chat-capable model. Implemented by the Ollama adapter and by fakes."""

    @property
    def descriptor(self) -> ModelDescriptor: ...

    def chat(
        self, messages: Sequence[ChatMessage], *, deadline: Deadline | None = None
    ) -> ModelReply: ...


# -------------------------------------------------------------- documents ---
@dataclass(frozen=True)
class TextSegment:
    """A page or section of the extracted text, with its character range."""

    index: int
    text: str
    char_start: int
    char_end: int
    page: int | None = None
    section: str | None = None
    is_empty: bool = False

    def span(self, document_id: str, quote: str | None = None) -> SourceSpan:
        return SourceSpan(
            document_id=document_id,
            char_start=self.char_start,
            char_end=self.char_end,
            quote=quote if quote is not None else self.text,
            page=self.page,
            section=self.section,
        )


@dataclass(frozen=True)
class ImportedDocument:
    document: SourceDocument
    segments: tuple[TextSegment, ...]
    warnings: tuple[str, ...] = ()

    @property
    def pages(self) -> list[str]:
        return [segment.text for segment in self.segments]


@runtime_checkable
class DocumentImportPort(Protocol):
    def supports(self, path: Path) -> bool: ...

    def load(self, path: Path) -> ImportedDocument: ...


# ------------------------------------------------------- storage / export ---
@runtime_checkable
class RunStorePort(Protocol):
    """Durable, traceable storage of a run (atomic replace in the adapter)."""

    def save_run(self, run: GenerationRun) -> Path: ...


@runtime_checkable
class ExportPort(Protocol):
    def export(self, run: GenerationRun, target: Path) -> Path: ...
