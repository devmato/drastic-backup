import base64
from hashlib import sha256
from typing import Any, Mapping

from Crypto.Cipher import AES

_ENVELOPE_VERSION = 1
_ALGORITHM = "AES-256-GCM"


class CryptoError(ValueError):
    pass


def _derive_key(key: str) -> bytes:
    normalized_key = str(key or "")
    if not normalized_key:
        raise CryptoError("encryption key cannot be empty")
    return sha256(normalized_key.encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: Any) -> bytes:
    try:
        return base64.b64decode(str(value or "").encode("ascii"), validate=True)
    except Exception as exc:
        raise CryptoError("encrypted value contains invalid base64 data") from exc


def decrypt(ciphertext=None, nonce=None, tag=None, key=None, **envelope):
    payload = dict(envelope)
    if ciphertext is not None:
        payload["ciphertext"] = ciphertext
    if nonce is not None:
        payload["nonce"] = nonce
    if tag is not None:
        payload["tag"] = tag

    if int(payload.get("v") or 0) != _ENVELOPE_VERSION:
        raise CryptoError("unsupported encrypted value version")
    if payload.get("alg") != _ALGORITHM:
        raise CryptoError("unsupported encrypted value algorithm")

    cipher = AES.new(_derive_key(key), AES.MODE_GCM, nonce=_b64decode(payload.get("nonce")))
    try:
        plaintext = cipher.decrypt_and_verify(
            _b64decode(payload.get("ciphertext")),
            _b64decode(payload.get("tag")),
        )
    except ValueError as exc:
        raise CryptoError("encrypted value could not be decrypted") from exc

    return plaintext.decode("utf-8")


def encrypt(plaintext, key) -> Mapping[str, str | int]:
    cipher = AES.new(_derive_key(key), AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(str(plaintext or "").encode("utf-8"))

    return {
        "v": _ENVELOPE_VERSION,
        "alg": _ALGORITHM,
        "nonce": _b64encode(cipher.nonce),
        "tag": _b64encode(tag),
        "ciphertext": _b64encode(ciphertext),
    }
