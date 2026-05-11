import base64
from typing import Any, Mapping

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import PBKDF2
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes

_ENVELOPE_VERSION = 1
_PASSWORD_ALGORITHM = "AES-256-GCM"
_PASSWORD_KDF = "PBKDF2-SHA256"
_PASSWORD_ITERATIONS = 600_000
_RSA_ALGORITHM = "RSA-OAEP-SHA256"


class SecretEnvelopeError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: Any) -> bytes:
    try:
        return base64.b64decode(str(value or "").encode("ascii"), validate=True)
    except Exception as exc:
        raise SecretEnvelopeError("secret envelope contains invalid base64 data") from exc


def _derive_password_key(password: str, salt: bytes, iterations: int) -> bytes:
    normalized_password = str(password or "")
    if not normalized_password:
        raise SecretEnvelopeError("password cannot be empty")
    return PBKDF2(
        normalized_password.encode("utf-8"),
        salt,
        dkLen=32,
        count=iterations,
        hmac_hash_module=SHA256,
    )


def encrypt_with_password(plaintext: str, password: str) -> Mapping[str, str | int]:
    salt = get_random_bytes(16)
    key = _derive_password_key(password, salt, _PASSWORD_ITERATIONS)
    cipher = AES.new(key, AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(str(plaintext or "").encode("utf-8"))

    return {
        "v": _ENVELOPE_VERSION,
        "alg": _PASSWORD_ALGORITHM,
        "kdf": _PASSWORD_KDF,
        "iterations": _PASSWORD_ITERATIONS,
        "salt": _b64encode(salt),
        "nonce": _b64encode(cipher.nonce),
        "tag": _b64encode(tag),
        "ciphertext": _b64encode(ciphertext),
    }


def decrypt_with_password(envelope: Mapping[str, Any], password: str) -> str:
    if int(envelope.get("v") or 0) != _ENVELOPE_VERSION:
        raise SecretEnvelopeError("unsupported password envelope version")
    if envelope.get("alg") != _PASSWORD_ALGORITHM:
        raise SecretEnvelopeError("unsupported password envelope algorithm")
    if envelope.get("kdf") != _PASSWORD_KDF:
        raise SecretEnvelopeError("unsupported password envelope kdf")

    iterations = int(envelope.get("iterations") or 0)
    if iterations <= 0:
        raise SecretEnvelopeError("password envelope contains invalid kdf parameters")

    key = _derive_password_key(password, _b64decode(envelope.get("salt")), iterations)
    cipher = AES.new(key, AES.MODE_GCM, nonce=_b64decode(envelope.get("nonce")))
    try:
        plaintext = cipher.decrypt_and_verify(
            _b64decode(envelope.get("ciphertext")),
            _b64decode(envelope.get("tag")),
        )
    except ValueError as exc:
        raise SecretEnvelopeError("password envelope could not be decrypted") from exc

    return plaintext.decode("utf-8")


def generate_agent_keypair() -> tuple[str, str]:
    key = RSA.generate(2048)
    private_key = key.export_key(format="PEM").decode("ascii")
    public_key = key.publickey().export_key(format="PEM").decode("ascii")
    return private_key, public_key


def encrypt_for_public_key(plaintext: str, public_key: str) -> Mapping[str, str | int]:
    try:
        key = RSA.import_key(public_key)
    except (ValueError, IndexError, TypeError) as exc:
        raise SecretEnvelopeError("invalid public key") from exc

    cipher = PKCS1_OAEP.new(key, hashAlgo=SHA256)
    try:
        ciphertext = cipher.encrypt(str(plaintext or "").encode("utf-8"))
    except ValueError as exc:
        raise SecretEnvelopeError("secret is too large for public key envelope") from exc

    return {
        "v": _ENVELOPE_VERSION,
        "alg": _RSA_ALGORITHM,
        "ciphertext": _b64encode(ciphertext),
    }


def decrypt_with_private_key(envelope: Mapping[str, Any], private_key: str) -> str:
    if int(envelope.get("v") or 0) != _ENVELOPE_VERSION:
        raise SecretEnvelopeError("unsupported public key envelope version")
    if envelope.get("alg") != _RSA_ALGORITHM:
        raise SecretEnvelopeError("unsupported public key envelope algorithm")

    try:
        key = RSA.import_key(private_key)
    except (ValueError, IndexError, TypeError) as exc:
        raise SecretEnvelopeError("invalid private key") from exc

    cipher = PKCS1_OAEP.new(key, hashAlgo=SHA256)
    try:
        plaintext = cipher.decrypt(_b64decode(envelope.get("ciphertext")))
    except ValueError as exc:
        raise SecretEnvelopeError("public key envelope could not be decrypted") from exc

    return plaintext.decode("utf-8")
