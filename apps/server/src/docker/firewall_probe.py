"""Startup check that runs with network cannot reach internal addresses.

A probe container on the sandbox bridge connects to the bridge gateway, the metadata address
and the internal services of the stack. A dropped packet times out; a completed connection or a
refusal proves a path, so the worker refuses to start.
"""

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlsplit

from src.config import Settings
from src.docker.client import SANDBOX_NETWORK, Config, DockerHost, Role

_PROBE_SECONDS = 2.0
_PROBE = """
import json, socket, sys
reachable = []
for host, port in json.loads(sys.argv[1]):
    try:
        socket.create_connection((host, port), timeout=float(sys.argv[2])).close()
    except TimeoutError:
        continue
    except OSError as error:
        if "timed out" in str(error) or getattr(error, "errno", None) in (101, 113):
            continue
    reachable.append(f"{host}:{port}")
print(json.dumps(reachable))
"""


class FirewallOpenError(RuntimeError):
    """A run with network can reach an internal address."""


async def _resolve(host: str) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({str(info[4][0]) for info in infos if info[0] is socket.AF_INET})


async def targets(settings: Settings, gateway: str) -> list[tuple[str, int]]:
    """Return the addresses a run must not reach.

    A loopback address of a service is the sandbox itself inside the probe, so the service is
    probed through the bridge gateway instead.
    """
    temporal_host, _, temporal_port = settings.temporal.address.rpartition(":")
    services = [
        (settings.database.host, settings.database.port),
        (temporal_host, int(temporal_port)),
    ]
    if settings.s3.endpoint_url is not None:
        endpoint = urlsplit(settings.s3.endpoint_url)
        services.append((endpoint.hostname or "", endpoint.port or 443))
    found = [(gateway, 1), (gateway, settings.worker.internal_port), ("169.254.169.254", 80)]
    for host, port in services:
        found.extend(
            (gateway if ipaddress.ip_address(address).is_loopback else address, port)
            for address in await _resolve(host)
        )
    return list(dict.fromkeys(found))


async def check_firewall(docker: DockerHost, settings: Settings, gateway: str) -> None:
    """Run the probe on the sandbox bridge.

    Raises
    ------
    FirewallOpenError
        If any internal address is reachable.
    """
    probe = await targets(settings, gateway)
    config: Config = {
        "Image": settings.execute.image,
        "Cmd": ["python3", "-c", _PROBE, json.dumps(probe), str(_PROBE_SECONDS)],
        "Labels": docker.labels(Role.PROBE),
        "HostConfig": {
            "NetworkMode": SANDBOX_NETWORK,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": 128 * 1024**2,
            "PidsLimit": 64,
        },
    }
    name = f"xv-probe-{docker.worker_id}"
    await docker.remove_container(name)
    container_id = await docker.start_container(name, config)
    try:
        result = await docker.wait_output(container_id, within=_PROBE_SECONDS * len(probe) + 30)
    finally:
        await docker.remove_container(container_id)
    if not result.strip():
        raise FirewallOpenError("the firewall probe produced no result")
    reachable: list[str] = json.loads(result)
    if reachable:
        raise FirewallOpenError(
            f"runs on {SANDBOX_NETWORK} reach internal addresses {reachable}; "
            "run firewall-init (apps/server/images/firewall) before the worker"
        )
