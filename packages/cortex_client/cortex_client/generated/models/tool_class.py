from enum import Enum


class ToolClass(str, Enum):
    APPLY = "apply"
    PROPOSE = "propose"
    READ = "read"

    def __str__(self) -> str:
        return str(self.value)
