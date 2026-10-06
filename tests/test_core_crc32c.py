import json
import random

import pytest

from lob_sim.record.envelope import _crc32c, canonical_json, payload_checksum


def bitwise_reference(data: bytes) -> int:
    """Independent bit-stream division; no lookup table or production helper."""
    remainder = 0xFFFFFFFF
    for byte in data:
        for bit in range(8):
            feedback = (remainder & 1) ^ ((byte >> bit) & 1)
            remainder >>= 1
            if feedback:
                remainder ^= 0x82F63B78
    return remainder ^ 0xFFFFFFFF


@pytest.mark.parametrize("length", [0, 1, 2, 7, 8, 9, 31, 32, 255, 256, 257, 1024, 4096])
@pytest.mark.parametrize("seed", [0, 7, 42])
def test_table_crc_agrees_with_independent_bitstream_oracle(length, seed):
    rng = random.Random(seed)
    data = bytes(rng.randrange(256) for _ in range(length))
    assert _crc32c(data) == bitwise_reference(data)


def test_all_byte_values_and_zero_length_checksum():
    for byte in range(256):
        assert _crc32c(bytes([byte])) == bitwise_reference(bytes([byte]))
    assert _crc32c(b"") == 0
    assert _crc32c(b"123456789") == 0xE3069283
    assert _crc32c(bytes(range(256)) * 32) == bitwise_reference(bytes(range(256)) * 32)


def test_canonical_unicode_payload_retains_exact_wire_checksum():
    payload = {"nested": [None, True, False, 0, 1.25, "é", "東京"], "price": "0.10", "size": "0.001"}
    expected_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert canonical_json(payload) == expected_bytes
    assert payload_checksum(payload) == f"crc32c:{bitwise_reference(expected_bytes):08x}"
