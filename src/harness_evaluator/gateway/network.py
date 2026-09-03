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

On Docker Desktop (macOS/Windows) the bridge lives inside a Linux VM and the
detected IP is not bindable on the host. In that case, ``resolve_gateway_host``
falls back to ``0.0.0.0`` (with a warning) because Docker Desktop's networking
model requires it — containers reach the host via a VM-internal address that
only works if the host process binds all interfaces. This is a weaker security
posture than the Linux bridge-IP binding, but Docker Desktop typically runs on
personal machines (not shared servers), and macOS provides a host firewall.
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

# Loopback fallback for standalone use without Docker.
_LOOPBACK_FALLBACK = "127.0.0.1"

# All-interfaces bind for Docker Desktop (bridge IP not bindable on host).
_ALL_INTERFACES = "0.0.0.0"


def _is_bindable(ip: str, port: int = 0) -> bool:
    """Check whether *ip* can be bound on this host.

    On Docker Desktop (macOS/Windows) the bridge IP lives inside the Linux VM
    and cannot be bound from the host process. This probe detects that case so
    we can fall back instead of crashing with EADDRNOTAVAIL.
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


def _is_docker_desktop() -> bool:
    """Detect whether Docker is running via Docker Desktop.

    Docker Desktop (macOS/Windows) runs Docker inside a Linux VM. The bridge
    network exists inside that VM, so ``docker network inspect bridge``
    returns an IP (e.g. 172.17.0.1) that is not bindable on the host. We need
    to detect this to fall back to ``0.0.0.0`` instead of the bridge IP.

    Uses ``docker info --format '{{.OperatingSystem}}'`` which returns
    "Docker Desktop" on macOS/Windows and the host OS name on Linux.
    """
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.OperatingSystem}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return "Docker Desktop" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return False


def resolve_gateway_host() -> str:
    """Detect the best bind address for the gateway proxy.

    On **Linux** with a native Docker daemon: detects the Docker bridge gateway
    IP via ``docker network inspect bridge`` and binds to it. This is the same
    IP ``host-gateway`` resolves to, so containers reach the gateway without it
    being exposed on the host's external NICs.

    On **Docker Desktop** (macOS/Windows): the bridge IP lives inside the Linux
    VM and can't be bound on the host. Falls back to ``0.0.0.0`` (all
    interfaces) because Docker Desktop's networking model requires it —
    containers reach the host via a VM-internal address that only works with an
    all-interfaces bind. A warning is logged about the exposure.

    When **Docker is not installed**: falls back to ``127.0.0.1`` so the
    gateway at least starts for standalone/development use.

    Returns the resolved host string. Never raises — on any failure, returns
    a fallback so the gateway can still attempt to bind.
    """
    detected = _detect_bridge_ip()
    if detected is not None and _is_bindable(detected):
        return detected

    # Bridge IP not bindable — likely Docker Desktop or no docker0 interface.
    if _is_docker_desktop():
        logger.warning(
            "Docker Desktop detected: bridge IP %s is inside the Linux VM "
            "and not bindable on the host. Binding 0.0.0.0 (all interfaces) "
            "so containers can reach the gateway via host.docker.internal. "
            "Consider enabling the macOS host firewall or using a firewall "
            "rule to restrict access to port 8877.",
            detected or _DEFAULT_BRIDGE_GATEWAY,
        )
        return _ALL_INTERFACES

    if detected is not None:
        logger.debug(
            "Bridge IP %s is not bindable on this host and Docker Desktop "
            "was not detected. Falling back to %s.",
            detected,
            _LOOPBACK_FALLBACK,
        )
    else:
        logger.debug(
            "Could not detect Docker bridge IP; falling back to %s.",
            _LOOPBACK_FALLBACK,
        )
    return _LOOPBACK_FALLBACK


def format_host_for_url(host: str) -> str:
    """Format a host for use in a URL, bracketing IPv6 literals.

    ``http://::1:8877`` is ambiguous (the colons in the IPv6 address collide
    with the port separator). RFC 3986 requires IPv6 literals in URLs to be
    wrapped in brackets: ``http://[::1]:8877``. IPv4 and hostnames are
    returned unchanged.
    """
    try:
        parsed = ipaddress.ip_address(host)
        if parsed.version == 6:
            return f"[{host}]"
    except ValueError:
        pass
    return host


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
