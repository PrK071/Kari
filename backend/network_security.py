from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeRemoteURLError(ValueError):
    """Raised when an outbound URL could reach a non-public network."""


def validate_public_http_url(
    url: str,
    *,
    allowed_ports: set[int] | None = None,
) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeRemoteURLError("URL remota deve usar HTTP ou HTTPS.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeRemoteURLError("URL remota nao pode conter credenciais.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise UnsafeRemoteURLError("Destino de rede privado bloqueado.")

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeRemoteURLError("Porta remota invalida.") from exc
    if allowed_ports is not None and port not in allowed_ports:
        raise UnsafeRemoteURLError("Porta remota nao permitida.")

    try:
        addresses = {
            row[4][0]
            for row in socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as exc:
        raise UnsafeRemoteURLError("Destino remoto nao pode ser resolvido.") from exc
    if not addresses:
        raise UnsafeRemoteURLError("Destino remoto nao pode ser resolvido.")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeRemoteURLError("Destino remoto retornou endereco invalido.") from exc
        if not ip.is_global:
            raise UnsafeRemoteURLError("Destino de rede privado bloqueado.")
    return value
