from enum import Enum

class Badge(str, Enum):
    ABSTAIN = "abstain"
    BLOCKED = "blocked"
    CERTIFIED = "certified"
    GOVERNED_METRIC = "governed_metric"
    QUERY_SKILL = "query_skill"
    SESSION = "session"

    def __str__(self) -> str:
        return str(self.value)
