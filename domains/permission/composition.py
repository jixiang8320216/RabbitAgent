import domains.permission.application.authorization_service as authorization_service
import domains.permission.infrastructure.file_approval_repository as file_approval_repository
import domains.permission.infrastructure.yaml_rule_provider as yaml_rule_provider
import domains.permission.infrastructure.hook_gateway as hook_gateway
import domains.permission.infrastructure.confirmation_adapter as confirmation_adapter
import domains.permission.infrastructure.context_provider as context_provider
import domains.permission.infrastructure.plugin_policy_adapter as plugin_policy_adapter

_authorization_service = None

def create_authorization_service()->authorization_service.AuthorizationService:
    return authorization_service.AuthorizationService(
        approval_repository=file_approval_repository.FileApprovalRepository(),
        rule_provider=yaml_rule_provider.YamlAuthorizationRuleProvider(),
        context_provider= context_provider.RuntimeAuthenticationContextProvider(),
        confirmation_gateway= confirmation_adapter.ConfirmationRegistryGateway(),
        plugin_policy=plugin_policy_adapter.PluginAuthorizationPolicy(),
        hook_gateway=hook_gateway.RuntimeAuthorizationHookGateway(),
    )

def get_authorization_service()->authorization_service.AuthorizationService:
    global _authorization_service
    if _authorization_service is None:
        _authorization_service= create_authorization_service()
    return _authorization_service
