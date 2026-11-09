"""Fetch one JPEG from an ESP32-CAM on the local network."""

from __future__ import annotations

import ipaddress
from http.client import HTTPConnection, HTTPException as HttpClientError

MAX_BYTES = 2 * 1024 * 1024


class CameraError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def normalize_ip(value: str) -> str:
    """Accept a local IPv4 address, with an optional port. Return host or host:port."""
    text = (value or "").strip()
    for prefix in ("http://", "https://"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.split("/", 1)[0].strip()
    if not text:
        raise CameraError(400, "Enter the ESP32 IP address.")
    host = text
    port = ""
    if ":" in text:
        host, port = text.rsplit(":", 1)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise CameraError(400, "Enter an IP address such as 192.168.254.20.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise CameraError(400, "Enter an IP address such as 192.168.254.20.") from exc
    if address.version != 4 or not (address.is_private or address.is_loopback or address.is_link_local):
        raise CameraError(400, "Use the ESP32 address on your local network.")
    return f"{host}:{port}" if port else host


def capture(address: str) -> bytes:
    """Read /capture from the camera. The address must already be a local IP."""
    host, _, port_text = address.partition(":")
    port = int(port_text) if port_text else 80
    connection = HTTPConnection(host, port, timeout=4)
    try:
        connection.request("GET", "/capture")
        response = connection.getresponse()
        if response.status != 200:
            raise CameraError(502, "The ESP32 did not send a camera image.")
        data = response.read(MAX_BYTES + 1)
    except CameraError:
        raise
    except (OSError, TimeoutError, HttpClientError) as exc:
        raise CameraError(
            502,
            "ESP32-CAM was not found. Check the IP address, Wi-Fi, and that the sketch is running.",
        ) from exc
    finally:
        connection.close()
    if len(data) < 100 or len(data) > MAX_BYTES or not data.startswith(b"\xff\xd8"):
        raise CameraError(502, "The ESP32 did not send a camera image.")
    return data
