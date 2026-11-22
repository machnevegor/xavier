"""Names and containers of a workspace: two volumes and the holder that serves them."""

from uuid import UUID

from src.config import ExecuteSettings
from src.docker.client import Config

WORKSPACE_MOUNT = "/workspace"
HOME_MOUNT = "/home/user"
MARKER = "/var/xavier/.checkpoint"
"""Touched in the holder when a checkpoint starts; newer files mean changes."""
USER = "1000:1000"


def volume_names(chat_id: UUID, epoch: int) -> tuple[str, str]:
    """Return the ``workspace`` and ``home`` volume names of one activation."""
    return f"xv-ws-{chat_id}-{epoch}", f"xv-home-{chat_id}-{epoch}"


def holder_name(chat_id: UUID) -> str:
    """Return the holder container name."""
    return f"xv-holder-{chat_id}"


def mounts(workspace_volume: str, home_volume: str | None = None) -> list[Config]:
    """Return the volume mounts of a container that uses the workspace."""
    found: list[Config] = [
        {"Type": "volume", "Source": workspace_volume, "Target": WORKSPACE_MOUNT}
    ]
    if home_volume is not None:
        found.append({"Type": "volume", "Source": home_volume, "Target": HOME_MOUNT})
    return found


def holder_config(
    execute: ExecuteSettings, labels: dict[str, str], workspace_volume: str, home_volume: str
) -> Config:
    """Return the holder: no network, both volumes, fixed service commands only."""
    return {
        "Image": execute.image,
        "Cmd": ["sleep", "infinity"],
        "User": USER,
        "Labels": dict(labels),
        "HostConfig": {
            "NetworkMode": "none",
            "Mounts": mounts(workspace_volume, home_volume),
            "Tmpfs": {
                "/tmp": "rw,nosuid,nodev,size=64m,mode=1777",  # noqa: S108 - a tmpfs mount
                "/var/xavier": "rw,nosuid,nodev,size=1m,uid=1000,gid=1000",
            },
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 64,
            "Memory": 512 * 1024**2,
            "Runtime": execute.runtime,
        },
    }
