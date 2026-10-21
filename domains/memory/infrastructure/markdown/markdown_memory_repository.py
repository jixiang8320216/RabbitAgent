from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import collections.abc as abc
from domains.memory.domain.memory_entry import MemoryScope, MemoryStatus, MemoryType
import domains.memory.domain.memory_entry as memory_entry


class MarkdownRepositoryError(RuntimeError):
	"""Raised when a legacy Markdown memory entry cannot be parsed."""


@dataclass(frozen=True)
class MarkdownFrontmatter:
	source_type: str | None = None


@dataclass(frozen=True)
class MarkdownMemoryEntry:
	type: MemoryType
	status: MemoryStatus
	content: str
	frontmatter: MarkdownFrontmatter


@dataclass(frozen=True)
class MarkdownMemoryIndex:
	path: Path


class MarkdownRepository:
	"""Read legacy Markdown memory entries from a scope directory."""

	def __init__(self, scope: MemoryScope, root: Path | str) -> None:
		self._scope = MemoryScope(scope)
		self._root = Path(root).resolve(strict=False)
		self._root.mkdir(parents=True, exist_ok=True)
		self._resolved_root = self._root
		self._lock_path = self._root / ".memory.lock"
		self._index = MarkdownMemoryIndex(self._root / "index.sqlite")

	def iter_files(self) -> abc.Iterator[Path]:
		for memory_type in sorted(memory_entry.MemoryType,key=lambda item: item.value):
			type_directory = self._root / memory_type.value
			if type_directory.exists():
				yield  from sorted(type_directory.glob("*.md"))

	def iter_file(self) -> abc.Iterator[Path]:
		"""Backward-compatible singular alias for iter_files()."""
		yield from self.iter_files()

	def read_file(self, path: Path | str) -> MarkdownMemoryEntry:
		file_path = Path(path)
		if not file_path.is_absolute():
			file_path = self.root / file_path
		try:
			text = file_path.read_text(encoding="utf-8")
		except (OSError, UnicodeError) as exc:
			raise MarkdownRepositoryError(f"Unable to read memory file: {file_path}") from exc

		frontmatter, content = self._parse_frontmatter(text)
		return MarkdownMemoryEntry(
			type=self._parse_type(frontmatter.get("type"), file_path),
			status=self._parse_status(frontmatter.get("status")),
			content=content.strip(),
			frontmatter=MarkdownFrontmatter(
				source_type=frontmatter.get("source_type") or frontmatter.get("sourceType")
			),
		)

	@staticmethod
	def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
		if not text.startswith("---"):
			return {}, text
		lines = text.splitlines()
		if len(lines) < 3 or lines[0].strip() != "---":
			return {}, text
		try:
			end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
		except StopIteration as exc:
			raise MarkdownRepositoryError("Unterminated Markdown frontmatter") from exc

		values: dict[str, str] = {}
		for line in lines[1:end]:
			if line.strip() and ":" in line:
				key, value = line.split(":", 1)
				values[key.strip()] = value.strip().strip("\"'")
		return values, "\n".join(lines[end + 1:])

	@staticmethod
	def _parse_type(value: str | None, path: Path) -> MemoryType:
		candidate = (value or path.stem).strip().lower().replace("-", "_")
		try:
			return MemoryType(candidate)
		except ValueError:
			return MemoryType.PATTERN

	@staticmethod
	def _parse_status(value: str | None) -> MemoryStatus:
		candidate = (value or MemoryStatus.ACTIVE.value).strip().lower()
		try:
			return MemoryStatus(candidate)
		except ValueError:
			return MemoryStatus.ACTIVE

	@property
	def scope(self) -> MemoryScope:
		return self._scope

	@property
	def root(self) -> Path:
		return self._root

	@property
	def resolved_root(self) -> Path:
		return self._resolved_root

	@property
	def lock_path(self) -> Path:
		return self._lock_path

	@property
	def index(self) -> MarkdownMemoryIndex:
		return self._index

	def close(self) -> None:
		"""Release repository resources; this implementation has no open handles."""
