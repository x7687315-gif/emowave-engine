# -*- coding: utf-8 -*-
"""加密导出回归测试（阶段 6：伪加密 XOR → 真 AES-256-GCM）

这些测试锁住的是**安全属性**，不是内部实现：
  · 新导出必须是 EMW2（AES-GCM）
  · 正确密码能往返
  · 错误密码必须失败（GCM 认证标签）
  · 篡改一个字节必须被发现（完整性）
  · EMW1 旧格式仍能只读打开，但必须发告警
"""
import base64
import hashlib
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

cryptography = pytest.importorskip("cryptography")

from privacy_export import DataExporter  # noqa: E402


PLAIN = '{"user_id_safe": "test_x", "note": "心情记录 中文"}'


@pytest.fixture(scope="module")
def exporter(tmp_path_factory):
    return DataExporter(user_id="t", output_dir=str(tmp_path_factory.mktemp("exp")))


def test_encrypt_uses_aes_gcm_prefix(exporter):
    ct = exporter._simple_encrypt(PLAIN, "pw")
    assert ct.startswith("EMW2:"), "新导出必须是 AES-GCM 的 EMW2 格式"
    # 密文长度 ≈ base64(16 salt + 12 nonce + len(明文) + 16 认证标签)
    raw = base64.b64decode(ct[5:])
    assert len(raw) >= 16 + 12 + 16


def test_encrypt_roundtrip(exporter):
    ct = exporter._simple_encrypt(PLAIN, "correct horse")
    assert exporter._simple_decrypt(ct, "correct horse") == PLAIN


def test_wrong_password_is_rejected(exporter):
    ct = exporter._simple_encrypt(PLAIN, "right")
    with pytest.raises(ValueError):
        exporter._simple_decrypt(ct, "wrong")


def test_tampering_is_detected(exporter):
    """GCM 的核心价值：改一个字节也解不开（旧 XOR 会静默解出乱码）。"""
    ct = exporter._simple_encrypt(PLAIN, "pw")
    raw = bytearray(base64.b64decode(ct[5:]))
    raw[-20] ^= 0x01
    tampered = "EMW2:" + base64.b64encode(bytes(raw)).decode()
    with pytest.raises(ValueError):
        exporter._simple_decrypt(tampered, "pw")


def test_emw1_legacy_readable_with_warning(exporter):
    """历史 EMW1（XOR）仍能打开，但必须告警提醒重新导出。"""
    import secrets
    salt = secrets.token_bytes(16)
    key = hashlib.sha256(b"pw" + salt).digest()
    body = bytes(b ^ key[i % 32] for i, b in enumerate(PLAIN.encode("utf-8")))
    emw1 = "EMW1:" + base64.b64encode(salt + body).decode()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert exporter._simple_decrypt(emw1, "pw") == PLAIN
        assert any(issubclass(x.category, UserWarning) for x in w), \
            "EMW1 必须发 UserWarning 提醒用户该格式不安全"


def test_unknown_prefix_rejected(exporter):
    with pytest.raises(ValueError):
        exporter._simple_decrypt("EMW9:garbage", "pw")