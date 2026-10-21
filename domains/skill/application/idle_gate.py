
from domains.skill.domain.models import BackgroundReviewConfig


class IdleGate:
    def __init__(self, config: BackgroundReviewConfig):
        self.config = config
