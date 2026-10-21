from __future__ import annotations

import re
import unicodedata
from enum import StrEnum


class MemoryType(StrEnum):
	ARCHITECTURE = "architecture"
	PITFALL = "pitfall"
	CONVENTION = "convention"
	PREFERENCE = "preference"
	DECISION = "decision"
	ERROR_SOLUTION = "error_solution"
	PATTERN = "pattern"


class MemoryScope(StrEnum):
	USER = "user"
	PROJECT = "project"


class MemoryStatus(StrEnum):
	ACTIVE = "active"
	ARCHIVED = "archived"
	DELETED = "deleted"


class MemoryConfidence(StrEnum):
	FACT = "fact"
	INFERENCE = "inference"
	PREFERENCE = "preference"
	UNKNOWN = "unknown"


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_for_hash(value: str) -> str:
	"""Normalize memory text before duplicate detection."""
	normalized = unicodedata.normalize("NFKC", str(value))
	return _WHITESPACE_RE.sub(" ", normalized).strip().casefold()


__all__ = [
	"MemoryConfidence",
	"MemoryScope",
	"MemoryStatus",
	"MemoryType",
	"normalize_for_hash",
]
