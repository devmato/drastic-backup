import base64
import hashlib

from Crypto.PublicKey import RSA


def generate_ssh_keypair() -> tuple[str, str]:
    key = RSA.generate(4096)
    private_key = key.export_key(format="PEM").decode("ascii")
    public_key = key.publickey().export_key(format="OpenSSH").decode("ascii")
    return private_key, public_key


def ssh_public_key_algorithm(public_key: str) -> str | None:
    parts = str(public_key or "").strip().split()
    if not parts:
        return None
    return parts[0]


def ssh_public_key_fingerprint(public_key: str) -> str | None:
    parts = str(public_key or "").strip().split()
    if len(parts) < 2:
        return None
    digest = hashlib.sha256(base64.b64decode(parts[1].encode("ascii"))).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")
