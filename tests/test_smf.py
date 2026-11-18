"""Offline tests for the import-completion predictor.

These cover the only part of the import wait that can be tested without a live
Ardour: how many tracks Ardour will create for a set of input files.

    python3 -m unittest discover -s tests -v

The fixture table mirrors what was measured against ardour9 9.8.0 by importing
each file and counting the resulting routes; see README "Async imports".
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))

from smf import model_track_count  # noqa: E402


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _track(events: list[bytes]) -> bytes:
    body = b"".join(events)
    return b"MTrk" + struct.pack(">I", len(body)) + body


def _smf(tracks: list[bytes], fmt: int = 1) -> bytes:
    return (b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), 480) + b"".join(tracks))


def notes(pitches=(60,)) -> bytes:
    events = []
    for i, p in enumerate(pitches):
        events.append(_vlq(0 if i == 0 else 240) + bytes([0x90, p, 90]))
        events.append(_vlq(120) + bytes([0x80, p, 0]))
    events.append(_vlq(0) + b"\xff\x2f\x00")
    return _track(events)


def meta_only() -> bytes:
    return _track([b"\x00\xff\x51\x03" + (500000).to_bytes(3, "big"), b"\x00\xff\x2f\x00"])


def empty() -> bytes:
    return _track([b"\x00\xff\x2f\x00"])


def cc_only() -> bytes:
    return _track([_vlq(0) + bytes([0xB0, 1, 64]), _vlq(240) + bytes([0xB0, 1, 32]),
                   _vlq(0) + b"\xff\x2f\x00"])


def bend_only() -> bytes:
    return _track([_vlq(0) + bytes([0xE0, 0x00, 0x50]), _vlq(240) + bytes([0xE0, 0x00, 0x40]),
                   _vlq(0) + b"\xff\x2f\x00"])


def pc_only() -> bytes:
    return _track([_vlq(0) + bytes([0xC0, 5]), _vlq(0) + b"\xff\x2f\x00"])


def sysex_only() -> bytes:
    payload = bytes([0x7E, 0x7F, 0x09, 0x01])
    return _track([_vlq(0) + bytes([0xF0, len(payload)]) + payload + b"\xF7",
                   _vlq(0) + b"\xff\x2f\x00"])


def channel_pressure_only() -> bytes:
    return _track([_vlq(0) + bytes([0xD0, 40]), _vlq(240) + bytes([0xD0, 10]),
                   _vlq(0) + b"\xff\x2f\x00"])


class ModelTrackCount(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.dir.cleanup()

    def write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def count(self, name: str, data: bytes) -> int | None:
        return model_track_count(self.write(name, data))

    # The expected values are what Ardour actually created in live tests.
    def test_two_note_tracks(self) -> None:
        self.assertEqual(self.count("a.mid", _smf([notes([60, 62]), notes([64, 65])])), 2)

    def test_conductor_track_ignored(self) -> None:
        self.assertEqual(self.count("b.mid", _smf([meta_only(), notes([69, 71])])), 1)

    def test_empty_track_ignored(self) -> None:
        self.assertEqual(self.count("c.mid", _smf([meta_only(), notes([72]), empty()])), 1)

    def test_cc_only_track_counts(self) -> None:
        self.assertEqual(self.count("d.mid", _smf([notes([76]), cc_only()])), 2)

    def test_bend_only_track_counts(self) -> None:
        self.assertEqual(self.count("e.mid", _smf([notes([62]), bend_only()])), 2)

    def test_channel_pressure_track_counts(self) -> None:
        self.assertEqual(self.count("f.mid", _smf([notes([62]), channel_pressure_only()])), 2)

    def test_program_change_only_track_ignored(self) -> None:
        self.assertEqual(self.count("g.mid", _smf([notes([60]), pc_only()])), 1)

    def test_sysex_only_track_ignored(self) -> None:
        self.assertEqual(self.count("h.mid", _smf([notes([60]), sysex_only()])), 1)

    def test_meta_only_file(self) -> None:
        self.assertEqual(self.count("i.mid", _smf([meta_only()])), 0)

    def test_format0_single_track(self) -> None:
        self.assertEqual(self.count("j.mid", _smf([notes([60, 62, 64])], fmt=0)), 1)

    def test_audio_file_is_not_smf(self) -> None:
        path = self.write("k.wav", b"RIFF....WAVEfmt ")
        self.assertIsNone(model_track_count(path))

    def test_missing_file(self) -> None:
        self.assertIsNone(model_track_count(os.path.join(self.dir.name, "nope.mid")))


class ExpectedTracks(unittest.TestCase):
    def test_mixed_inputs(self) -> None:
        import ardour_agent

        with tempfile.TemporaryDirectory() as d:
            mid = os.path.join(d, "multi.mid")
            with open(mid, "wb") as fh:
                fh.write(_smf([notes([60]), notes([62]), meta_only()]))
            audio = os.path.join(d, "take.wav")
            with open(audio, "wb") as fh:
                fh.write(b"RIFF....WAVE")
            expected, origin = ardour_agent._expected_tracks({"paths": [mid, audio]})
        self.assertEqual(expected, 3)  # 2 MIDI tracks + 1 audio
        self.assertEqual(origin, "derived from inputs")

    def test_unknown_input_is_not_guessed(self) -> None:
        import ardour_agent

        with tempfile.TemporaryDirectory() as d:
            odd = os.path.join(d, "thing.xyz")
            with open(odd, "wb") as fh:
                fh.write(b"not a known format")
            expected, _ = ardour_agent._expected_tracks({"paths": [odd]})
        self.assertIsNone(expected)

    def test_merge_is_not_guessed(self) -> None:
        import ardour_agent

        expected, origin = ardour_agent._expected_tracks(
            {"paths": ["/tmp/x.mid"], "merge": True})
        self.assertIsNone(expected)
        self.assertIn("merge", origin)


if __name__ == "__main__":
    unittest.main()
