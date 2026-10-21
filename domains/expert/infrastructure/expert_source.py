from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import json


import domains.expert.infrastructure.expert_repository as expert_repository
import domains.expert.infrastructure.expert_secret_store as secret_store_module
import domains.agent.domain.entities.expert_configuration as expert_models
import domains.mcp.domain.server_configuration as mcp_models
import domains.expert.infrastructure.portable_package as portable_package
import domains.skill.application.skill_package_parser as skill_package_parser

logger = logging.getLogger(__name__)
_IMPORT_STATE_FILENAME=".import-state.json"

class ExpertScope(StrEnum):
    BUILTIN = "builtin"
    SYSTEM = "system"
    USER = "user"


@dataclass(frozen=True)
class ExpertConfiguration:
    """单个专家配置：来自某层目录的 EXPERT.md。"""

    id: str
    name: str = ""
    description: str = ""
    scope: ExpertScope = ExpertScope.BUILTIN
    path: Path | None = None
    content: str = ""

def _package_resource_inverntory(
    directory: Path,
    *,
    configuration: expert_models.ExpertConfiguration,
    secret_store: secret_store_module.ExpertMcpSecretStore|None,
    secret_owner_id:str | None = None,
)->tuple[
    tuple[str,...],
    tuple[str,...],
    tuple[str,...],
    tuple[mcp_models.MCPServerConfig,...],
    expert_models.ExpertResourceLock|None,
    expert_models.ExpertReadiness,
]:

    resource_lock = portable_package.load_resource_lock(directory)
    skill_names:set[str]=set()
    for skill_path in sorted(directory/"skills").glob("*/SKILL.md"):
        try:
            document = skill_package_parser.SKillPackageParser.parse_file(skill_path)
        except skill_package_parser.SkillPackageParseError:
            continue
        if document.manifest.active is not False:
            skill_names.add(document.manifest.name)

    mcp_names:set[str]=set()
    tool_names:set[str]=set()
    mcp_configs:list[mcp_models.MCPServerConfig]=[]
    for config_path in sorted(directory/"mcp_config").glob("*.json"):
        try:
            data = json.load(config_path.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            continue
        if not isinstance(data,dict) or data.get("active",True)is False:
            continue
        server_name= str(data.get("name") or config_path.stem).strip()
        if not server_name:
            continue
        mcp_names.add(server_name)
        try:
            config=mcp_models.MCPServerConfig.from_dict(data)
            config.config_path=config_path
            if resource_lock is not None and secret_store is not None:
                config= portable_package.apply_mcp_secret_overrides(
                    config,
                    expert_id=secret_owner_id or configuration.expert_id,
                    resource_id = resource_lock.secret_requiredments,
                    secret_store= secret_store,
                )
            mcp_configs.append(config)
        except ValueError:
            logger.warning("skill invalid packagee MCP config")
        available_tools = data.get("available_tools")
        if not isinstance(available_tools,list):
            continue
        for item in available_tools:
            if not isinstance(item,dict):
                continue
            tool_name= str(item.get("name") or "").strip().replace(" ","_")
            if tool_name:
                tool_names.add(f"mcp_{server_name}__{tool_name}")
    readiness = expert_models.ExpertReadiness()
    if resource_lock is not None:
        diagnostics = list(
            portable_package.verify_locked_resources(
                directory,
                resource_lock,
                secret_store=secret_store,
            )
        )
        diagnostics.extend(_load_import_state_diagnostics(directory))
        readiness=expert_models.ExpertReadiness.from_diagnostics(diagnostics)
    else:
        readiness=expert_models.ExpertReadiness.from_diagnostics(
            _load_import_state_diagnostics(directory)
        )
    return(
        tuple(sorted(skill_names)),
        tuple(sorted(mcp_names)),
        tuple(sorted(tool_names)),
        tuple(sorted(mcp_configs),key=lambda item: item.name),
        resource_lock,
        readiness,
    )

def _load_import_state_diagnostics(
    directory:Path,
)-> tuple[expert_models.ExpertReadinessDiagnostic,...]:
    path=directory/_IMPORT_STATE_FILENAME
    if not path.is_file():
        return()
    try:
        document= json.loads(path.read_text(encoding="utf-8"))
        raw_diagnostics=document.get("diagnostics",[]) if isinstance(document,dict) else []
        return tuple(
            expert_models.ExpertReadinessDiagnostic.model_validate(item)
            for item in raw_diagnostics
        )
    except (OSError,ValueError):
        return (
            expert_models.ExpertReadinessDiagnostic(
                code="import_state_invalid",
                message="专家导入状态损坏，需重新导入",
                action="重新导入专家包",
            )
        )


def _resolved_record(
    *,
    configuration: expert_models.ExpertConfiguration,
    prompt: str,
    directory: Path,
    revision:str,
    source_category: str,
    secret_store: secret_store_module.ExpertMcpSecretStore|None = None,
    resource_root:Path |None = None,
    secret_owner_id:str |None = None,
)-> expert_models.ResolvedExpertConfiguration:
    (
        package_skills,
        package_mcps,
        package_tools,
        package_mcp_configs,
        resource_lock,
        readiness,
    )=_package_resource_inverntory(
        resource_root or directory,
        configuration =configuration,
        secret_store=secret_store,
        secret_owner_id=secret_owner_id,
    )
    return expert_models.ResolvedExpertConfiguration(
        configuration=configuration,
        prompt=prompt,
        directory=directory,
        resource_root=resource_root,
        revision=revision,
        source_category=source_category,
        package_skill_names= package_skills,
        package_mcp_server_names= package_mcps,
        package_tool_names= package_tools,
        package_mcp_configs= package_mcp_configs,
        resource_lock= resource_lock,
        readiness= readiness,
    )




def load_expert_root(
    root:Path,
    *,
    source_category: str,
)->tuple[expert_models.ResolvedExpertConfiguration, ...]:
    if not root.is_dir():
        return ()
    records: list[expert_models.ResolvedExpertConfiguration]=[]
    for directory in sorted(root.iterdir(),key=lambda x:x.name):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        config_path = directory /expert_repository.EXPERT_CONFIG_FILENAME
        prompt_path = directory/expert_repository.EXPERT_PROMPT_FILENAME
        if not config_path.is_file() or not prompt_path.is_file():
            continue
        try:
            configuration= expert_models.ExpertConfiguration.model_validate_json(
                config_path.read_text(encoding="utf-8"),
                strict=True,
            )
            prompt= prompt_path.read_text(encoding="utf-8")
            if configuration.expert_id != directory.name:
                raise ValueError("expert directory and document ID differ")
            records.append(
                _resolved_record(
                    configuration = configuration,
                    prompt= prompt,
                    directory=directory,
                    revision=directory.revision(prompt),
                    source_category=source_category,
                ))
        except ValueError as e:
            logger.error(e)
    return tuple(records)

def _load_locked_associated_records(
    root_record: expert_models.ResolvedExpertConfiguration,
        *,
    secret_store:secret_store_module.ExpertMcpSecretStore,
)->tuple[expert_models.ResolvedExpertConfiguration, ...]:
    lock=root_record.resource_lock
    if lock is None:
        return ()
    records: list[expert_models.ResolvedExpertConfiguration]=[]
    for entry in lock.experts:
        if entry.expert_id==lock.root_expert_id:
            continue
        directory = root_record.directory/Path(*entry.relative_path.split("/"))
        config_path= directory/expert_repository.EXPERT_CONFIG_FILENAME
        prompt_path = directory/expert_repository.EXPERT_PROMPT_FILENAME
        try:
            configuration= expert_models.ExpertConfiguration.model_validate_json(
                config_path.read_text(encoding="utf-8"),
                strict=True,
            )
            prompt= prompt_path.read_text(encoding="utf-8")
            records.append(
                _resolved_record(
                    configuration = configuration,
                    prompt=prompt,
                    directory=directory,
                    revision=directory.revision(prompt),
                    source_category="custom",
                    secret_store=secret_store,
                    resource_root=root_record.directory,
                    secret_owner_id=lock.root_expert_id,
                )
            )
        except (OSError,ValueError) as e:
            logger.error(e)

    return tuple(records)

def _block_duplicate_names(
    records: tuple[expert_models.ResolvedExpertConfiguration,...]
)->tuple[expert_models.ResolvedExpertConfiguration,...]:
    ids_by_name: dict[str,list[str]]={}
    for record in records:
        ids_by_name.setdefault(record.configuration.name, []).append(
            record.configuration.expert_id
        )
    duplicate_names={
        name:tuple(sorted(expert_ids))
        for name, expert_ids in ids_by_name.items()
        if len(expert_ids)>1
    }

    if not duplicate_names:
        return records
    blocked: list[expert_models.ResolvedExpertConfiguration]=[]
    for record in records:
        expert_ids = duplicate_names.get(record.configuration.name)
        if expert_ids is None:
            blocked.append(record)
            continue
        diagnostic = expert_models.ExpertReadinessDiagnostic(
            code="duplicate_expert_name",
            resource_id=record.configuration.name,
            message=(
                f"Expert display name '{record.configuration.name}' is shared by"
                f"stable IDs:{','.join(expert_ids)}"
            ),
            action="Rename one expert so every display name is unique",
        )
        blocked.append(record.model_copy(
            update={
                "readiness":expert_models.ExpertReadiness.from_diagnostics(
                    (*record.readiness.diagnostics, diagnostic)
                )
            }
        ))
    return tuple(blocked)

def _resolve_legacy_associated_refs(
    records: tuple[expert_models.ResolvedExpertConfiguration,...],
)-> tuple[expert_models.ResolvedExpertConfiguration,...]:
    resolved_records:list[expert_models.ResolvedExpertConfiguration]=[]
    for record in records:
        binding= record.configuration.resources.agents
        if(
            binding.mode is not expert_models.ExpertResourceBindingMode.MANUAL
            or binding.expert_refs
            or not binding.names
        ):
            resolved_records.append(record)
            continue
        references: list[expert_models.AssociatedExpertRef]=[]
        diagnostics: list[expert_models.ExpertReadinessDiagnostic]=[]
        for name in binding.names:
            mathes=[
                candidate
                for candidate in records
                if candidate.configuration.expert_id == name
                or candidate.configuration.name == name
            ]
            identities={
                candidate.configuration.expert_id for candidate in mathes
            }

            if len(identities) !=1:
                diagnostics.append(
                    expert_models.ExpertReadinessDiagnostic(
                        code="associated_expert_unresolved",
                        resource_id=name,
                        message=f"关联专家无法唯一解析:{name}",
                        action="按稳定ID 重新选择关联专家",
                    ))
                continue
            target=mathes[0]
            references.append(
                expert_models.AssociatedExpertRef(
                    expert_id=target.configuration.expert_id,
                    display_name=target.configuration.name,
                )
            )
        updated=record
        if not diagnostics:
            resources= record.configuration.resources.model_copy(
                update={
                    "agents":binding.model_copy(
                        update={"expert_refs":tuple(references)}
                    )
                }
            )
            updated=record.model_copy(
                update={
                    "configuration":record.configuration.model_copy(
                        update={"resources":resources}
                    )
                }
            )
        else:
            updated=record.model_copy(
                update={
                    "readiness":expert_models.ExpertReadiness.from_diagnostics(
                        (*record.readiness.diagnostics, *diagnostics)
                    )
                }
            )
        resolved_records.append(updated)
    return tuple(resolved_records)


class LayeredExpertConfigurationSource:
    """分层专家配置源：builtin → system → user，后者覆盖前者。"""

    def __init__(
        self,
        *,
        user_data_home: str | Path | None,
        builtin_root: str | Path | None,
        system_root: str | Path | None,
    ) -> None:
        self._repository = expert_repository.ExpertConfigurationRepository(user_data_home)
        self._secret_store = secret_store_module.ExpertMcpSecretStore(user_data_home)
        self._builtin_root =builtin_root
        self._system_root = system_root


    def list_experts(self) -> tuple[expert_models.ResolvedExpertConfiguration, ...]:
       effective: dict[str,expert_models.ResolvedExpertConfiguration] = {}
       for record in load_expert_root(self._builtin_root, source_category="builtin"):
            effective[record.configuration.expert_id]=record
       if self._system_root is not None:
            for record in load_expert_root(self._system_root,source_category="system"):
                effective[record.configuration.expert_id]=record
       for stored in self._repository.list_experts():
            root_record = _resolved_record(
                configuration = stored.configuration,
                prompt = stored.prompt,
                directory = stored.directory,
                revision = stored.revision,
                source_category = "custom",
                secret_store = self._secret_store,
            )
            associted = _load_locked_associated_records(
                root_record,
                secret_store = self._secret_store,
            )
            conflicts: list[expert_models.ExpertReadinessDiagnostic]=[]
            for record in associted:
                existing = effective.get(record.configuration.expert_id)
                if existing is not None and existing.revision !=record.revision:
                    conflicts.append(
                        expert_models.ExpertReadinessDiagnostic(
                            code="expert_id_conflict",
                            resource_id= record.configuration.expert_id,
                            message="包内关联专家与本地同ID 专家内容不同",
                            action="移除冲突专家或选择显示替换",
                        )
                    )
                    continue
                effective[record.configuration.expert_id]=record
            if conflicts:
               root_record = root_record.model_copy(
                    update={
                        "readiness":expert_models.ExpertReadiness.from_diagnostics(
                            (*root_record.readiness.diagnostics, *conflicts)
                        )
                    }
               )
            effective[stored.configuration.expert_id]= root_record
       records = tuple(effective[expert_id] for expert_id in sorted(effective))
       return _block_duplicate_names(_resolve_legacy_associated_refs(records))

    def discover_all(self) -> list[ExpertConfiguration]:
        """扫描三层目录下的 EXPERT.md，按 id 合并（user 覆盖 system 覆盖 builtin）。"""
        merged: dict[str, ExpertConfiguration] = {}
        for path, scope in self._candidate_files():
            expert = self._parse_expert(path, scope)
            if expert is not None:
                merged[expert.id] = expert  # 后者覆盖前者
        return sorted(merged.values(), key=lambda expert: expert.id)

    def get(self, expert_id: str) -> ExpertConfiguration | None:
        target = expert_id.strip()
        for expert in self.discover_all():
            if expert.id == target:
                return expert
        return None

    def _candidate_files(self) -> list[tuple[Path, ExpertScope]]:
        """builtin → system → user 的优先级顺序，递归返回存在的 EXPERT.md。"""
        roots: list[tuple[Path, ExpertScope]] = []
        if self.builtin_root is not None:
            roots.append((self.builtin_root, ExpertScope.BUILTIN))
        if self.system_root is not None:
            roots.append((self.system_root, ExpertScope.SYSTEM))
        if self.user_data_home is not None:
            # user_data_home 是用户主目录，其下 experts/ 为用户级专家根
            roots.append((self.user_data_home / "experts", ExpertScope.USER))

        result: list[tuple[Path, ExpertScope]] = []
        for root, scope in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob(self._EXPERT_FILE_NAME)):
                if path.is_file():
                    result.append((path, scope))
        return result

    @classmethod
    def _parse_expert(
        cls, path: Path, scope: ExpertScope
    ) -> ExpertConfiguration | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None

        frontmatter, body = cls._split_frontmatter(raw)
        expert_id = (frontmatter.get("id") or frontmatter.get("name") or path.parent.name).strip()
        if not expert_id:
            return None
        name = (frontmatter.get("name") or expert_id).strip()
        description = (frontmatter.get("description") or "").strip()
        return ExpertConfiguration(
            id=expert_id,
            name=name,
            description=description,
            scope=scope,
            path=path,
            content=body.strip(),
        )

    @classmethod
    def _split_frontmatter(cls, text: str) -> tuple[dict[str, str], str]:
        match = cls._FRONTMATTER_RE.match(text)
        if match is None:
            return {}, text
        values: dict[str, str] = {}
        for line in match.group("body").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key.strip()] = value.strip().strip("\"'")
        return values, match.group("rest")
