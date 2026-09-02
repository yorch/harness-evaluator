"""Gateway network helpers for Docker bridge detection.

The gateway proxy must be reachable from inside Docker containers. Containers
reach the host via ``host.docker.internal`` (mapped with
``--add-host host.docker.internal:host-gateway``), which resolves to the host's
Docker bridge gateway IP — typically ``172.17.0.1`` on the default ``docker0``
bridge. A gateway bound to ``127.0.0.1`` is not reachable at that IP, which is
the root cause of the ``Connection refused`` failures users see on Linux.

These helpers detect the bridge gateway IP so the gateway can bind to it by
default, keeping the proxy off the host's external NICs while remaining
reachable from containers — without requiring users to pass ``--host 0.0.0.0``
(and without the security implications of binding all interfaces).

On Docker Desktop (macOS/Windows) the bridge lives inside a VM and the
detected IP is not bindable on the host. ``resolve_gateway_host`` probes
bindability and falls back to ``127.0.0.1`` in that case, so the gateway
still starts (users on Docker Desktop should pass ``--host 0.0.0.0``
explicitly for container reachability).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import subprocess

logger = logging.getLogger(__name__)

# Fallback bridge gateway IP for the default docker0 bridge.
# Used when `docker network inspect` is unavailable or fails.
_DEFAULT_BRIDGE_GATEWAY = "172.17.0.1"

# Sentinel value for the CLI --host option that triggers automatic resolution.
AUTO_HOST = "auto"

# Loopback fallback when the bridge IP is not bindable (e.g. Docker Desktop).
_LOOPBACK_FALLBACK = "127.0.0.1"


def _is_bindable(ip: str, port: int = 0) -> bool:
    """Check whether *ip* can be bound on this host.

    On Docker Desktop (macOS/Windows) the bridge IP lives inside the Linux VM
    and cannot be bound from the host process. This probe detects that case so
    we can fall back to loopback instead of crashing with EADDRNOTAVAIL.
    """
    try:
        infos = socket.getaddrinfo(ip, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.bind(sockaddr)
            sock.close()
            return True
        except OSError:
            sock.close()
    return False


def _validate_ip(raw: str) -> str | None:
    """Validate that *raw* is a safe, non-wildcard IP address.

    Rejects ``0.0.0.0`` (wildcard), non-IP strings, and anything that
    ``docker network inspect`` might return that is not a concrete address.
    This prevents a compromised or misconfigured Docker setup from tricking
    the gateway into binding all interfaces.
    """
    ip = raw.strip()
    if not ip:
        return None
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return None
    # Reject wildcard/unspecified addresses — the whole point of this module
    # is to avoid binding 0.0.0.0. Also reject multicast.
    if parsed.is_unspecified or parsed.is_multicast:
        return None
    return str(parsed)


def resolve_gateway_host() -> str:
    """Detect the Docker default bridge gateway IP.

    This is the IP that ``--add-host host.docker.internal:host-gateway``
    resolves to inside containers. Binding the gateway to this IP makes it
    reachable from containers without exposing it on all interfaces.

    Tries ``docker network inspect bridge`` first, validates the output is a
    real IP (not ``0.0.0.0`` or junk), probes whether it can be bound on this
    host, then falls back to the well-known default ``172.17.0.1``. If that
    also can't be bound (e.g. Docker Desktop where the bridge is in a VM),
    falls back to ``127.0.0.1`` so the gateway at least starts.

    Returns the resolved IP string. Never raises — on any failure, returns
    a fallback so the gateway can still attempt to bind.
    """
    detected = _detect_bridge_ip()
    if detected is not None and _is_bindable(detected):
        return detected

    if detected is not None:
        logger.debug(
            "Bridge IP %s is not bindable on this host; "
            "likely Docker Desktop or no docker0 interface. "
            "Falling back to %s.",
            detected,
            _LOOPBACK_FALLBACK,
        )
    else:
        logger.debug(
            "Could not detect Docker bridge IP; falling back to %s.",
            _LOOPBACK_FALLBACK,
        )
    return _LOOPBACK_FALLBACK


def _detect_bridge_ip() -> str | None:
    """Run ``docker network inspect bridge`` and return the validated gateway IP.

    Returns ``None`` if docker is unavailable, the inspect fails, or the
    output is not a valid non-wildcard IP address.
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "network",
                "inspect",
                "bridge",
                "--format",
                "{{(index .IPAM.Config 0).Gateway}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return _validate_ip(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug(
            "docker network inspect failed (%s); falling back.", exc
        )
    return None
