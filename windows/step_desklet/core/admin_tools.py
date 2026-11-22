"""Fixed Windows administration tools offered through the consent dialog."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class AdminTool:
    label: str
    executable: str
    console: str | None = None


ADMIN_TOOLS = MappingProxyType({
    "device-manager": AdminTool("Device Manager", "mmc.exe", "devmgmt.msc"),
    "disk-management": AdminTool("Disk Management", "mmc.exe", "diskmgmt.msc"),
    "event-viewer": AdminTool("Event Viewer", "mmc.exe", "eventvwr.msc"),
    "services": AdminTool("Services", "mmc.exe", "services.msc"),
    "task-manager": AdminTool("Task Manager", "Taskmgr.exe"),
})


@dataclass(frozen=True)
class AdminToolRequest:
    tool_id: str
