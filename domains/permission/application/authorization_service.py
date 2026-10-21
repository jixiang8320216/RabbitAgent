
import domains.permission.domain.ports as ports
import domains.permission.domain.policy as policy_module
import domains.permission.application.approval_workflow as approval_workflow



class AuthorizationService:


    def __init__(
        self,
        approval_repository: ports.ApprovalRepository,
        rule_provider: ports.AuthorizationRuleProvider,
        context_provider: ports.AuthorizationContextpRrovider,
        confirmation_gateway: ports.ConfirmationGateway,
        plugin_policy: ports.AuthorizationPolicyPlugib,
        hook_gateway: ports.AuthorizationHookGateway|None=None,
    )->None:
        self._approval_repository = approval_repository
        self._rule_provider = rule_provider
        self._context_provider = context_provider
        self._default_rules = policy_module.default_rules()
        self._approval_workflow = approval_workflow.ApprovalWorkflow(
            approval_repository=approval_repository,
            confirmation_gateway=confirmation_gateway,
            plugin_policy=plugin_policy,
            hook_gateway=hook_gateway,
        )