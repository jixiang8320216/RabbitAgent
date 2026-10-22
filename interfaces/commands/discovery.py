import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict,Optional
import logging
from domains.configuration.domain.protocol.project_layout_impl import project_layout
from domains.configuration import get_user_config_dir

logger = logging.getLogger(__name__)

MAX_COMMAND_FILE_SIZE=1024*100

@dataclass
class MarkdownCommandInfo:

    name:str
    full_name:str
    description:str
    prompt:str
    source:str
    file_path:Path

class CommandDiscovery:
    PRIORITY_SYSTEM = 1
    PRIORITY_USER = 2
    PRIORITY_PROJECT_RABBIT = 3
    PRIORITY_PROJECT_CLAUDE = 2.5
    PRIORITY_PROJECT_CURSOR = 2.3

    def __init__(self, project_home:str=".") -> None:
        self.project_home = Path(project_home).resolve()

    def _get_system_commands_dir(self) -> Path:
        from domains.configuration import get_config_dir
        return get_config_dir()/"commands"

    def _get_user_commands_dir(self) -> Path:
        return get_user_config_dir() / "commands"

    def _get_rabbit_commands_dir(self) -> Path:
        layout= project_layout(self.project_home)
        if layout is None:
            return None
        return layout.commands_dir


    def _get_claude_commands_dir(self) -> Path:
        return self.project_home / ".claude" / "commands"

    def _get_cursor_commands_dir(self) -> Path:
        return self.project_home / ".cursor" / "commands"

    @staticmethod
    def _path_to_command_name(relative_path: Path) -> str:
        """相对路径转命令名：去掉 .md 后缀，路径段用 ':' 连接（如 git/commit.md -> git:commit）。"""
        parts = [p for p in relative_path.with_suffix("").parts if p and p != "."]
        return ":".join(parts)


    def discover_all(self) -> Dict[str, MarkdownCommandInfo]:
        commands: Dict[str, tuple[MarkdownCommandInfo, int]] = {}
        sources = [
            (self._get_system_commands_dir(), self.PRIORITY_SYSTEM, "system"),
            (self._get_user_commands_dir(), self.PRIORITY_USER, "user"),
        ]
        project_sources = [
            (self._get_rabbit_commands_dir(), self.PRIORITY_PROJECT_RABBIT, "project-rabbit"),
            (self._get_claude_commands_dir(), self.PRIORITY_PROJECT_CLAUDE, "project-claude"),
            (self._get_cursor_commands_dir(), self.PRIORITY_PROJECT_CURSOR, "project-cursor"),
        ]
        sources.extend(project_sources)

        for cmd_dir, priority, source_name in sources:
            if cmd_dir and cmd_dir.exists():
                discovered = self._scan_directory(cmd_dir, source_name)
                for cmd_name, cmd_info in discovered.items():
                    if cmd_name not in commands or priority > commands[cmd_name][1]:
                        commands[cmd_name] = (cmd_info, priority)
        result = {name: info for name, (info, _) in commands.items()}
        logger.info(f"[discovery] {len(result)} commands total")
        return result

    def _scan_directory(self,directory:Path,source_name:str)->Dict[str, MarkdownCommandInfo]:
        commands={}

        for md_file in directory.rglob("*.md"):
            try:
                if md_file.stat().st_size >MAX_COMMAND_FILE_SIZE:
                    continue
                cmd_info=self._parse_command_file(md_file,source_name,directory)
                if cmd_info:
                    commands[cmd_info.name]=cmd_info
            except Exception as e:
                logger.error(f"[discovery] {e}")
        return commands

    def _parse_command_file(self, file_path:Path,source_name:str,base_dir:Path=None)-> Optional[MarkdownCommandInfo]:

        content = file_path.read_text(encoding="utf-8")
        description=""
        prompt_content=content

        fromatter_math = re.match(r"^---\s*\n(.*?)\n===\s*\n(.*)$", content, re.DOTALL)
        if fromatter_math:
            fromatter_text,body_text = fromatter_math.groups()
            prompt_content = body_text
            desc_match = re.search(r"^Description:\s*(.*)", fromatter_text)
            if desc_match:
                description = desc_match.group(1).strip().strip("\n")
                description =description.rstrip("\"'").strip()

        if base_dir:
            relative_path = file_path.relative_to(base_dir)
        else:
            relative_path = file_path.relative_to(file_path.parent.parent)
        name=self._path_to_command_name(relative_path)
        if not description:
            description = f"/{name}"
        return MarkdownCommandInfo(
            name=name,
            full_name=f"/{name}",
            description=description,
            prompt =prompt_content,
            source =source_name,
            file_path=file_path,
        )



