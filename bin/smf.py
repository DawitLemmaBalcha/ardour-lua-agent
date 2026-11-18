"""Minimal Standard MIDI File reader used to predict Ardour's import result.

Ardour creates one track per SMF track that produces MIDI **model** events, and
silently drops the rest. Verified on ardour9 9.8.0 by importing fixtures:

    track content          Ardour track?
    ---------------------  -------------
    notes                  yes
    control change         yes
    pitch bend             yes
    channel pressure       yes
    program change only    no
    sysex only             no
    tempo/meta only        no  (conductor track)
    empty                  no

The rule is therefore: a track counts when it contains at least one event whose
status is 0x8n..0xBn or 0xDn..0xEn. Program change (0xCn), sysex (0xF0/0xF7)
and meta (0xFF) do not create an Ardour track on their own.
"""
from __future__ import annotations

__all__ = ["model_track_count", "is_smf"]

_COUNTED = set(range(0x80, 0xC0)) | set(range(0xD0, 0xF0))


class SmfError(ValueError):
    pass


def is_smf(data: bytes) -> bool:
    return len(data) >= 14 and data[:4] == b"MThd"


def _read_vlq(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    while True:
        if i >= len(data):
            raise SmfError("truncated variable-length quantity")
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def _track_has_model_events(body: bytes) -> bool:
    i, status = 0, None
    while i < len(body):
        _, i = _read_vlq(body, i)  # delta time
        if i >= len(body):
            break
        byte = body[i]

        if byte == 0xFF:  # meta
            i += 2  # 0xFF, type
            length, i = _read_vlq(body, i)
            i += length
            status = None
            continue
        if byte in (0xF0, 0xF7):  # sysex
            i += 1
            length, i = _read_vlq(body, i)
            i += length
            status = None
            continue
        if byte & 0x80:  # new status byte
            status = byte
            i += 1
        # else: running status, keep previous

        if status is None:
            raise SmfError("data byte with no running status")
        high = status & 0xF0
        if high in _COUNTED:
            return True
        # program change and channel pressure have one data byte
        i += 1 if high in (0xC0, 0xD0) else 2
    return False


def model_track_count(path: str) -> int | None:
    """Number of SMF tracks Ardour will turn into tracks, or None if not SMF."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not is_smf(data):
        return None
    header_len = int.from_bytes(data[4:8], "big")
    ntrk = int.from_bytes(data[10:12], "big")
    i = 8 + header_len
    counted = 0
    for _ in range(ntrk):
        if data[i:i + 4] != b"MTrk":
            raise SmfError(f"{path}: expected MTrk at offset {i}")
        length = int.from_bytes(data[i + 4:i + 8], "big")
        body = data[i + 8:i + 8 + length]
        i += 8 + length
        try:
            if _track_has_model_events(body):
                counted += 1
        except SmfError:
            raise
    return counted
