"""PROXY protocol v2 parsing helpers (generic, no game-specific code)."""

from __future__ import annotations

import ipaddress
import struct

PROXY_V2_SIGNATURE = b"\r\n\r\n\x00\r\nQUIT\n"


def _hexdump(data: bytes, limit: int = 64) -> str:
    data = data[:limit]
    return " ".join(f"{b:02x}" for b in data)


def parse_proxy_v2(data: bytes) -> dict | None:
    """Parse a PROXY protocol v2 header.

    Returns None when the datagram does not start with the v2 signature.
    The returned dict has:
      kind: "PROXY" | "LOCAL"
      complete: bool
      header_length: int
      source / destination: str | None
      source_port / destination_port: int | None
      family: int
      protocol_name: str
    """
    if len(data) < 16 or not data.startswith(PROXY_V2_SIGNATURE):
        return None

    version_command = data[12]
    family_protocol = data[13]
    address_length = (data[14] << 8) | data[15]
    version = version_command >> 4
    command = version_command & 0x0F
    family = family_protocol >> 4
    protocol = family_protocol & 0x0F
    total_length = 16 + address_length

    result = {
        "kind": "PROXY" if command == 1 else "LOCAL" if command == 0 else "UNKNOWN",
        "version": version,
        "command": command,
        "family": family,
        "protocol": protocol,
        "protocol_name": {
            1: "STREAM",
            2: "DGRAM",
        }.get(protocol, f"proto-{protocol}"),
        "header_length": total_length,
        "complete": len(data) >= total_length,
        "source": None,
        "source_port": None,
        "destination": None,
        "destination_port": None,
    }

    if version != 2 or not result["complete"]:
        return result

    address = data[16:16 + address_length]
    if command != 1:
        return result

    if family == 1 and address_length >= 12:  # AF_INET
        src_ip, dst_ip, src_port, dst_port = struct.unpack("!4s4sHH", address[:12])
        result["source"] = str(ipaddress.IPv4Address(src_ip))
        result["destination"] = str(ipaddress.IPv4Address(dst_ip))
        result["source_port"] = src_port
        result["destination_port"] = dst_port
    elif family == 2 and address_length >= 36:  # AF_INET6
        src_ip, dst_ip, src_port, dst_port = struct.unpack("!16s16sHH", address[:36])
        result["source"] = str(ipaddress.IPv6Address(src_ip))
        result["destination"] = str(ipaddress.IPv6Address(dst_ip))
        result["source_port"] = src_port
        result["destination_port"] = dst_port

    return result
