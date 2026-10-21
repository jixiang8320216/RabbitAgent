import json
import pathlib
import typing
import logging
import domains.persistence.domain.store_ports as ports
import domains.agent.domain.value_objects.runtime_context as process_runtime_context
from domains.agent.domain.value_objects.runtime_context import get_runtime_context_or_none
import domains.session.infrastructure.session_file_catalog as session_file_catalog

logger = logging.getLogger(__name__)
_load_app_state_from_path= session_file_catalog.load_app_state_from_path


def _get_legacy_sessions_dirs() ->typing.List[pathlib.Path]:
    legacy_dirs:list[pathlib.Path]=[]
    seen: set[pathlib.Path]=set()

    def add_candidate(path: pathlib.Path|None)->None:
        if path is None:
            return
        try:
            candidate = pathlib.Path(path).expanduser()
            if not candidate.exists() or not candidate.is_dir():
                return
            resolved =candidate.resolve()
            runtime_ctx = get_runtime_context_or_none()
            canonical_dirs=[]
            if runtime_ctx is not None:
                canonical_dirs.extend(
                    p.resolve()
                    for p in (runtime_ctx.sessions_dir, runtime_ctx.apps_dir)
                    if p is not None and pathlib.Path(p).exists()
                )
            if resolved in canonical_dirs or resolved in seen:
                return
            legacy_dirs.append(candidate)
            seen.add(resolved)
        except Exception:
          return

    runtime_ctx = get_runtime_context_or_none()
    if runtime_ctx is not None:
        add_candidate(runtime_ctx.user_data_home/"sessions")
    else:
        add_candidate(pathlib.Path.cwd()/"sessions")
        for parent in pathlib.Path.cwd().resolve().parents:
            if (parent / ".git").exists():
                add_candidate(parent / "sessions")
                break
    return legacy_dirs


def _session_search_dirs(
    sessions_dir: pathlib.Path,
    runtime_ctx: process_runtime_context.RuntimeContext|None,
)->list[pathlib.Path]:
    search_dirs = [sessions_dir]
    if runtime_ctx is None:
        return search_dirs
    canonical_dirs = {path.resolve() for path in (runtime_ctx.sessions_dir,runtime_ctx.apps_dir) if path is not None}
    if sessions_dir.resolve() not in canonical_dirs:
        return search_dirs
    for legacy_dir in _get_legacy_sessions_dirs():
        if legacy_dir not in search_dirs:
            search_dirs.append(legacy_dir)
    return search_dirs

def _app_state_path(session_dir: pathlib.Path, session_id:str) -> pathlib.Path:
    return pathlib.Path(session_dir) /f"app_{session_id}"/"managerment.json"

class SessionPersistence:
   """统一的Session 持久化管理"""
   def __init__(
        self,
        sessions_dir:pathlib.Path,
        *,
        session_state_store: ports.SessionStateStore|None=None,
        session_backup_store: ports.SessionBackupStore|None=None,
        allow_legacy_writes: bool=False,
   ) -> None:
       self.sessions_dir = pathlib.Path(sessions_dir)
       self.sessions_dir.mkdir(parents=True, exist_ok=True)
       self._created_timestamps: typing.Dict[str,str]= {}
       self._state_store=session_state_store
       self._backup_store = session_backup_store
       self._allow_legacy_writes= allow_legacy_writes


   @staticmethod
   def normalize_path(value: str) -> str:
       """归一化路径，统一分隔符并消解冗余段。

       会话状态里记录的工作目录可能来自不同平台（Windows 的 ``\\``、
       POSIX 的 ``/``），也可能是相对路径。展示与比较前先归一，
       避免同一个目录因写法不同而被当成两个。

       **说明**：该方法在迁移中丢失定义，
       ``SessionPersistence.list_sessions`` 构造 ``SessionFileCatalogQuery``
       时会依赖它，这里按该调用点补齐。
       """
       if not value:
            return value
       text = str(value).strip()
       if not text:
            return text
       # 统一为 POSIX 风格分隔符，消除重复分隔符与尾部分隔符
       unified = text.replace("\\", "/")
       while "//" in unified:
            unified = unified.replace("//", "/")
       if len(unified) > 1:
            unified = unified.rstrip("/")
       return unified


   @staticmethod
   def get_project_home(session_id:str, session_dir: typing.Optional[pathlib.Path] = None) -> typing.Optional[str]:
       runtime_ctx= process_runtime_context.get_runtime_context()
       if session_dir is None:
           session_dir=runtime_ctx.sessions_dir
       if session_dir is None:
            return None

       search_dirs = _session_search_dirs(session_dir,runtime_ctx)

       #按顺序搜索，APP V3 managerment plane 优先于legacy flat file
       for search_dir in search_dirs:
           app_path =_app_state_path(search_dir, session_id)
           if app_path.exists():
               try:
                   app_state = _load_app_state_from_path(app_path)
                   project_home = app_state.execution_context.get("woking_directory")
                   if isinstance(project_home, str) and project_home:
                       return project_home
               except Exception as e:
                    logger.warning(e)

           session_file = search_dir/f"{session_id}.json"
           if session_file.exists():
               try:
                   with open(session_file, "r") as f:
                       session_data = json.load(f)
                   project_home = session_data.get("execution_context",{}).get("woking_directory")
                   if project_home:
                       return project_home
               except Exception as e:
                    logger.warning(e)
                    continue
       return None


   @staticmethod
   def list_sessions(
        sessions_dir: typing.Optional[pathlib.Path] = None, #session文件存储目录
        limit: int =50,
        offset: int = 0, #跳过的会话数量，用于分页
        include_old_format: bool = True, #是否包含旧格式（_execution.json）的兼容逻辑（默认True）
        filter_tags: typing.Mapping[str,str|None]|None = None, #按tags过滤，格式{"key":"value"}
   )->list[session_file_catalog.JsonObject]:
      """列出所有Sessions（统一的公共方法）
      整合删除session list 功能的实现
      --使用CLI的正则陪陪过滤文件：
      --整合web api的旧格式兼容逻辑（检查_execution.json）
      --使用SessionState读取新格式session
      --标准化返回格式
      --支持limit和offset参数实现分页

      Returns:
          Session 信息列表，每个元素包括：
        - id: Session ID
        - created_at: 创建时间
        - last_updated：最后更新时间
        - modified：修改时间（ISO格式）
        -working_directory: 工作目录
        - topic： 会话主题
        - tags: 会话标签
        -size： 文件大小(字节)
        -current_plan:当前计划信息（如果有）
        -historrical_plans_conunt: 历史计划数量
        -first_message: 第一条用户消息（预览）
        -has_session_file: 是否有session文件
        -has_execution_file: 是否有exectution文件
      """
      runtime_ctx = get_runtime_context_or_none()
      if sessions_dir is None:
           runtime_ctx =process_runtime_context.get_runtime_context()
           sessions_dir=runtime_ctx.sessions_dir
           if sessions_dir is None:
               raise RuntimeError("RuntimeContext.sessions_dir is required to list sessions")
      query = session_file_catalog.SessionFileCatalogQuery(
           sessions_dir=sessions_dir,
           search_dirs= _session_search_dirs(sessions_dir,runtime_ctx),
           normalize_path=SessionPersistence.normalize_path,
      )
      return query.execute(
           limit=limit,
           offset=offset,
           include_old_format=include_old_format,
           filter_tags=filter_tags,
       )

