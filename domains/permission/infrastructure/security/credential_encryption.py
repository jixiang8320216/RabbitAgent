"""凭证加密服务。

L4-2.4「凭证最小化」与 L4-4.1「日志脱敏」的落点：API Key 这类凭证在落盘与
传递过程中不应以明文出现。

实现选择——标准库 ``hashlib`` + ``hmac``：
    不引入 cryptography 等第三方依赖。派生密钥走 PBKDF2-HMAC-SHA256，
    密文走 HMAC-SHA256 的 keystream 异或 + 完整性校验，等价于
    「认证加密」的最小可用形态，能挡住「密文被篡改」与「离线暴力破解
    弱口令」两类最常见攻击。

密钥来源：
    环境变量 ``RABBIT_CREDENTIAL_KEY``。**未配置时服务处于禁用态**，
    ``is_enabled()`` 返回 False，调用方据此拒绝把凭证交给本服务——
    这比默默用固定默认密钥加密更安全（固定密钥等于没加密）。

密文格式：``ENC:<base64(nonce)>:<base64(keystream)>:<base64(mac)>``
    带 ``ENCRYPTION_PREFIX`` 前缀，使「已加密」与「明文」在存储层可区分。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import typing

#: 环境变量名：凭证加密主密钥。
CREDENTIAL_KEY_ENV = "RABBIT_CREDENTIAL_KEY"

#: PBKDF2 迭代次数。取值兼顾安全强度与启动开销（只在加密时执行一次）。
_KDF_ITERATIONS = 200_000

#:派生密钥与keystream 的用途分隔，避免同一密钥被复用于两个目的。
_INFO_ENCRYPT = b"rabbit-credential-encrypt-v1"
_INFO_MAC = b"rabbit-credential-mac-v1"

_NONCE_BYTES = 16


class CredentialEncryptionError(RuntimeError):
    """凭证加解密失败（密钥未配置、密文损坏或校验不通过）。"""


class EncryptionService:
    """对称加密服务（单例，通过 :func:`get_encryption_service` 获取）。"""

    ENCRYPTION_PREFIX = "ENC:"

    def __init__(self, key: str | None = None) -> None:
        # 显式传入优先，其次读环境变量；都没有则进入禁用态
        self._key = key if key is not None else os.environ.get(CREDENTIAL_KEY_ENV, "")
        self._lock = threading.Lock()

    # ── 状态 ────────────────────────────────────────────────────
    @property
    def is_enabled(self) -> bool:
        """是否具备可用的加密密钥。

        调用方必须先判断这一点：密钥缺失时应拒绝把凭证交给本服务。
        """
        return bool(self._key)

    def _require_key(self) -> bytes:
        if not self._key:
            raise CredentialEncryptionError(
                f"凭证加密未启用：请设置环境变量 {CREDENTIAL_KEY_ENV}"
            )
        return self._key.encode("utf-8")

    def _derive(self, salt: bytes, info: bytes) -> bytes:
        # info 参与 salt 拼接，使加密与 MAC 用不同的派生密钥
        return hashlib.pbkdf2_hmac(
            "sha256", self._require_key(), salt + info, _KDF_ITERATIONS, dklen=32
        )

    # ── 加解密 ──────────────────────────────────────────────────
    def is_encrypted(self, value: typing.Any) -> bool:
        """判断字符串是否已是本服务产出的密文。"""
        return isinstance(value, str) and value.startswith(self.ENCRYPTION_PREFIX)

    def encrypt(self, plaintext: typing.Any) -> str:
        """加密明文并返回带前缀的密文。

        已加密的值原样返回（幂等），避免重复加密导致无法解密。
        """
        if plaintext is None:
            raise CredentialEncryptionError("待加密内容不能为 None")
        if self.is_encrypted(plaintext):
            return plaintext
        data = str(plaintext).encode("utf-8")
        with self._lock:
            salt = os.urandom(_NONCE_BYTES)
            keystream = self._derive(salt, _INFO_ENCRYPT)
            mac_key = self._derive(salt, _INFO_MAC)
            cipher = bytes(a ^ b for a, b in zip(data, keystream))
            mac = hmac.new(mac_key, cipher, hashlib.sha256).digest()

        return (
            f"{self.ENCRYPTION_PREFIX}"
            f"{base64.urlsafe_b64encode(salt).decode('ascii')}:"
            f"{base64.urlsafe_b64encode(cipher).decode('ascii')}:"
            f"{base64.urlsafe_b64encode(mac).decode('ascii')}"
        )

    def decrypt(self, ciphertext: typing.Any) -> str:
        """解密本服务产出的密文。

        完整性校验失败时抛 :class:`CredentialEncryptionError`——
        绝不在校验失败时返回明文或空串。
        """
        if not self.is_encrypted(ciphertext):
            raise CredentialEncryptionError("密文缺少 ENC: 前缀，拒绝解密")
        parts = ciphertext[len(self.ENCRYPTION_PREFIX):].split(":")
        if len(parts) != 3:
            raise CredentialEncryptionError("密文格式非法")
        try:
            salt = base64.urlsafe_b64decode(parts[0])
            cipher = base64.urlsafe_b64decode(parts[1])
            mac = base64.urlsafe_b64decode(parts[2])
        except Exception as exc:  # noqa: BLE001 — 统一转为领域异常
            raise CredentialEncryptionError(f"密文解码失败：{exc}") from exc

        with self._lock:
            expected = hmac.new(
                self._derive(salt, _INFO_MAC), cipher, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(expected, mac):
                raise CredentialEncryptionError("密文完整性校验失败，可能已被篡改")
            keystream = self._derive(salt, _INFO_ENCRYPT)
        return bytes(a ^ b for a, b in zip(cipher, keystream)).decode("utf-8")

    # ── 脱敏 ────────────────────────────────────────────────────
    #:保留的可见字符数（首尾），其余以 * 代替。
    def mask(self, secret: typing.Any, keep: int = 4) -> str:
        """脱敏展示：只保留首尾各 keep 个字符。

        用于日志与 Trace，确保明文密钥不会出现在观测数据里。
        """
        if secret is None:
            return ""
        text = str(secret)
        if not text:
            return ""
        if len(text) <= keep * 2:
            return "*" * len(text)
        return f"{text[:keep]}{'*' * (len(text) - keep * 2)}{text[-keep:]}"


#: 进程级单例状态。放在模块级而非类属性上，便于 ``global`` 声明生效
#: （类体内不存在 global 作用域，写在类里会导致 NameError）。
_encryption_service: typing.Optional[EncryptionService] = None
_encryption_service_lock = threading.Lock()


def get_encryption_service() -> EncryptionService:
    """取全局加密服务（双重检查锁单例）。"""
    global _encryption_service
    if _encryption_service is None:
        with _encryption_service_lock:
            if _encryption_service is None:
                _encryption_service = EncryptionService()
    return _encryption_service


__all__ = [
    "CREDENTIAL_KEY_ENV",
    "CredentialEncryptionError",
    "EncryptionService",
    "get_encryption_service",
]