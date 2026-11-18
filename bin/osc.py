"""Minimal OSC 1.0 codec (encode + decode), stdlib only.

Only what an Ardour client needs: messages with string/int/float arguments.
Deliberately small so the whole transport is auditable.
"""
from __future__ import annotations

import struct

__all__ = ["encode_message", "decode_message", "OscError"]


class OscError(ValueError):
    pass


def _pad(data: bytes) -> bytes:
    return data + b"\0" * ((4 - len(data) % 4) % 4)


def _parse_bloblike(data: bytes, offset: int) -> tuple[bytes, bytes, int]:
    end = data.index(b"\0", offset)
    return data[offset:end], data[:end], end + 1


def encode_message(address: str, *args) -> bytes:
    """Encode one OSC message.

    Supported argument types: str, bool, int, float.
    """
    if not address.startswith("/"):
        raise OscError(f"OSC address must start with '/': {address!r}")

    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, bool):
            tags += "i"
            payload += struct.pack(">i", 1 if arg else 0)
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags += "f"
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags += "s"
            payload += _pad(arg.encode("utf-8") + b"\0")
        else:
            raise OscError(f"unsupported OSC argument type: {type(arg).__name__}")

    return _pad(address.encode("utf-8") + b"\0") + _pad(tags.encode("ascii") + b"\0") + payload


def decode_message(data: bytes) -> tuple[str, list]:
    """Decode a single OSC message into (address, args)."""
    if not data:
        raise OscError("empty datagram")

    def read_padded(buf: bytes, off: int) -> tuple[str, int]:
        end = buf.index(b"\0", off)
        text = buf[off:end].decode("utf-8", "replace")
        return text, off + ((len(text) // 4) + 1) * 4

    address, offset = read_padded(data, 0)
    if offset >= len(data):
        return address, []
    tags, offset = read_padded(data, offset)
    if not tags.startswith(","):
        raise OscError(f"missing OSC type tag string: {tags!r}")

    args: list = []
    for tag in tags[1:]:
        if tag == "i":
            args.append(struct.unpack(">i", data[offset:offset + 4])[0])
            offset += 4
        elif tag == "f":
            args.append(struct.unpack(">f", data[offset:offset + 4])[0])
            offset += 4
        elif tag == "s":
            value, offset = read_padded(data, offset)
            args.append(value)
        elif tag == "d":
            args.append(struct.unpack(">d", data[offset:offset + 8])[0])
            offset += 8
        elif tag in "TF":
            raise OscError("OSC 1.1 T/F tags not supported")
        else:
            raise OscError(f"unsupported OSC type tag {tag!r}")
    return address, args
