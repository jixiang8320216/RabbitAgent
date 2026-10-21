import typing
import domains.persistence.application.persistence_plane as persistence_plane_module


class SessionStateApplication(typing.Protocol):

    persistence_plane: persistence_plane_module.PersistencePlane|None