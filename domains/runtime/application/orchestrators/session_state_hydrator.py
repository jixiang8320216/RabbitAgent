import domains.runtime.application.orchestrators.session_state_contracts as state_contracts

class SessionStateHydrator:

    def __init__(self, app:state_contracts.SessionStateApplication) -> None:
        self.app = app