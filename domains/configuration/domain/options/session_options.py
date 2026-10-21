from domains.configuration.domain.model.config_data import RuntimeConfigModel


class RuntimeSessionConfig(RuntimeConfigModel):
    resume: bool=False
    session_id: str=""