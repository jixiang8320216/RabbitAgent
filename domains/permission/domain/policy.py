"""内置授权规则表。

``default_rules()`` 是 guradrails 的兜底策略：YAML 规则源（``yaml_rule_provider``）
没提供规则、或某类操作没有任何规则覆盖时，用它保证「高危操作至少会被拦一下」。

匹配目标（``pattern`` 匹配什么）按操作类型而定：

- ``shell_command`` / ``schedule_task`` / ``git_push``：完整的命令串。
- ``network_request``：``"METHOD URL"``（如 ``POST https://api.example.com``）。
- ``mcp``：MCP 服务名或接入地址。
- ``mcp_tool``：工具名（如 ``filesystem.write_file``）。
- ``code_modification``：文件路径。

规则**按顺序匹配，命中第一条即止**。因此每个操作类型内部都把「最具体、最危险」
的放在前面，兜底的 ``.*`` 放在最后——否则兜底规则会把前面的精细规则全部吃掉。

关于 ``DENY``：默认动作是 ``CONFIRM``（问一句），只有两类操作直接拒绝——
文件系统级破坏（mkfs/format/dd/关机）和删除根目录/家目录。它们既不可逆，
又几乎不存在"用户其实想要"的合理场景，给它们弹确认只是提供误点同意的机会。
其余高危操作（含普通 ``rm -rf <子目录>``）一律走确认，避免挡住正常的清理工作。
"""

from __future__ import annotations

import domains.permission.domain.contracts as contracts

__all__ = ["default_rules"]

_Rule = contracts.AuthorizationRule
_Operation = contracts.OperationType


def default_rules() -> tuple[contracts.AuthorizationRule, ...]:
    """返回内置授权规则（按操作类型分组，组内由具体到兜底排序）。"""
    return (
        # --- code_modification：改代码 ------------------------------------
        # _Rule(
        #     operation_type=_Operation.CODE_MODIFICATION.value,
        #     # 凭据/密钥类文件：泄露后影响远超仓库本身，单独提级
        #     # 注意 \.env 要按「路径段」匹配，否则 .environment / env.py 会被误伤
        #     pattern=r"(?i)((^|[\\/])\.env(\.|$|[\\/])|credential|secret|\.pem$|\.key$|id_rsa|\.ssh[\\/]|\.aws[\\/]|(^|[\\/])\.npmrc$)",
        #     scope=contracts.Scope.ONCE,
        #     risk_level=contracts.RiskLevel.HIGH,
        #     description="Modify credential or secret-bearing file",
        # ),
        _Rule(
            operation_type=_Operation.CODE_MODIFICATION.value,
            pattern=r".*",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="Modify code file",
        ),
        # --- shell_command：执行 shell -------------------------------------
        # # 1) 删除根/家目录：不可逆且无合理场景，直接拒绝
        # _Rule(
        #     operation_type=_Operation.SHELL_COMMAND.value,
        #     # 只认「路径就是根/家目录本身」，rm -rf /tmp 不该落到这里
        #     pattern=r"(?i)\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*|--force|--recursive)\s+(\*|/\s*$|/\*|~(/|\s|$)|\$HOME)",
        #     scope=contracts.Scope.NEVER,
        #     risk_level=contracts.RiskLevel.CRITICAL,
        #     action=contracts.RuleAction.DENY,
        #     description="Recursive delete targeting root or home directory",
        # ),
        # # 2) 文件系统级破坏：同样直接拒绝
        # _Rule(
        #     operation_type=_Operation.SHELL_COMMAND.value,
        #     # 各分支自带词边界：dd 的 if= 后面紧跟路径（= 与 / 之间没有词边界），
        #     # 统一在外层加 \b 会让这一支永远匹配不上
        #     pattern=r"(?i)(\bmkfs(\.[a-z0-9]+)?|\bformat\s+[a-z]:|\bdd\s+if=|\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b|\binit\s+0)",
        #     scope=contracts.Scope.NEVER,
        #     risk_level=contracts.RiskLevel.CRITICAL,
        #     action=contracts.RuleAction.DENY,
        #     description="Filesystem-level or host power operation",
        # ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^(\S*/)?(rm|del)\s+[rR]",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.CRITICAL,
            description="Recursove delete commands",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^DROP\s+(TABLE|DATABASE|SCHEMA)",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.CRITICAL,
            description="SQL DROP commands",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"mkfs|dd\s+if=.*=/dev",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.CRITICAL,
            description="DIsk formatting command",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^(\S*/)?(rm|del)\s+",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.CRITICAL,
            description=" delete command",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^(curl|wget)\s+",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="Network download command",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^(pip|npm|yarn|cargo)\s+install",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="Package installation",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^(ls|dir|pwd|echo|cat|head|tail|grep|find|which|whereis)(\s|$)",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.LOW,
            auto_approve=True,
            description="safe read-only commands",
        ),
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r"^(git\s(status|log|diff|show|branch|remote))(\s|$)",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.LOW,
            auto_approve=True,
            description="safe git read commands",
        ),
        # 10) 兜底：任何 shell 命令至少被问一次
        _Rule(
            operation_type=_Operation.SHELL_COMMAND.value,
            pattern=r".*",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="other shell command",
        ),
        # --- schedule_task：计划任务 ---------------------------------------
        # 计划任务是「持久副作用」：即使对话结束也会继续执行，所以整类比
        # 一次性命令更严格。
        # _Rule(
        #     operation_type=_Operation.SCHEDULE_TASK.value,
        #     # 注册到系统级调度器。只认「写入型」子命令：crontab -l（列出）、
        #     # systemctl status（查看）这类只读调用不该被当成注册任务。
        #     pattern=r"(?i)(\bcrontab\s+(?!-l\b)|\bsystemctl\s+(enable|start|restart|stop|daemon-reload)\b|\bschtasks\s+/create\b|\blaunchctl\s+(load|submit|start)\b|\bsystemd-run\b|\bat\s+\d)",
        #     scope=contracts.Scope.ONCE,
        #     risk_level=contracts.RiskLevel.CRITICAL,
        #     description="Register a system-level scheduled task",
        # ),
        _Rule(
            operation_type=_Operation.SCHEDULE_TASK.value,
            pattern=r".*",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.HIGH,
            description="Schedule a recurring or delayed task",
        ),
        # # --- mcp：接入 MCP 服务 --------------------------------------------
        # # 接入 MCP 等于把外部能力注入 agent，远端服务更是等同于执行第三方代码。
        # _Rule(
        #     operation_type=_Operation.MCP.value,
        #     # 非本地地址：远端 MCP 服务
        #     pattern=r"(?i)^(https?|sse|ws|wss)://(?!localhost\b|127\.0\.0\.1|\[::1\])",
        #     scope=contracts.Scope.ONCE,
        #     risk_level=contracts.RiskLevel.CRITICAL,
        #     description="Connect to a remote MCP server",
        # ),
        # _Rule(
        #     operation_type=_Operation.MCP.value,
        #     pattern=r".*",
        #     scope=contracts.Scope.ONCE,
        #     risk_level=contracts.RiskLevel.HIGH,
        #     description="Connect an MCP server (injects external tools)",
        # ),
        # --- mcp_tool：调用 MCP 工具 ---------------------------------------
        # _Rule(
        #     operation_type=_Operation.MCP_TOOL.value,
        #     # 名字里带破坏/外发语义的工具
        #     pattern=r"(?i)(delete|remove|drop|truncate|purge|destroy|kill|terminate|exec|shell|run_command|write_file|edit_file|push|publish|send|deploy|reset)",
        #     scope=contracts.Scope.ONCE,
        #     risk_level=contracts.RiskLevel.HIGH,
        #     description="Invoke an MCP tool with destructive or outbound effect",
        # ),
        _Rule(
            operation_type=_Operation.MCP_TOOL.value,
            pattern=r"^mcp://[^/]+/.+$",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="Invoke an MCP tool",
        ),
        # --- network_request：对外网络请求 ----------------------------------
        # _Rule(
        #     operation_type=_Operation.NETWORK_REQUEST.value,
        #     # 会改变远端状态的写方法，且目标是外部地址（数据外发风险）
        #     pattern=r"(?i)^(POST|PUT|PATCH|DELETE)\s+https?://(?!localhost\b|127\.0\.0\.1|\[::1\])",
        #     scope=contracts.Scope.ONCE,
        #     risk_level=contracts.RiskLevel.HIGH,
        #     description="Outbound request that mutates remote state",
        # ),
        _Rule(
            operation_type=_Operation.NETWORK_REQUEST.value,
            pattern=r".*",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="Outbound network request",
        ),
        _Rule(
            operation_type=_Operation.NETWORK_REQUEST.value,
            pattern=r"(github\.com|huawei\.com|pypi\.org)",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.LOW,
            auto_approve=True,
            description="Trusted network request",
        ),
        # --- git_push：推送到远端 -------------------------------------------
        _Rule(
            operation_type=_Operation.GIT_PUSH.value,
            pattern=r"--force.*\s+(origin/)?(main|master|develop|release)",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.CRITICAL,
            description="Force push or delete a remote branch",
        ),
        _Rule(
            operation_type=_Operation.GIT_PUSH.value,
            pattern=r"(origin/)?(main/master)",
            # pattern=r"(?i)(--force(?!-with-lease)|-f\b|--delete|--mirror)",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.HIGH,
            description=" push to main or master branch",
        ),
        # 推送是不可撤销的对外可见动作（他人可见、CI 会跑），整类 HIGH 起步。
        _Rule(
            operation_type=_Operation.GIT_PUSH.value,
            # 强推 / 删远端分支：会覆盖他人提交
            pattern=r"--force",
            # pattern=r"(?i)(--force(?!-with-lease)|-f\b|--delete|--mirror)",
            scope=contracts.Scope.ONCE,
            risk_level=contracts.RiskLevel.HIGH,
            description="Force push or delete a remote branch",
        ),
        _Rule(
            operation_type=_Operation.GIT_PUSH.value,
            pattern=r".*",
            scope=contracts.Scope.SESSION,
            risk_level=contracts.RiskLevel.MEDIUM,
            description="Push commits to a remote repository",
        ),
    )
