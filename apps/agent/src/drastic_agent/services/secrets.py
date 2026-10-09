"""Unseal repository credentials into memory and persist only agent-key envelopes."""

import json

from drastic_agent.agent.exceptions import AgentExeption
from drastic_common.secret_envelope import (
    SecretEnvelopeError,
    decrypt_with_private_key,
    encrypt_for_public_key,
)


def json_mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def agent_password(repository, private_key, cache):
    repository_id = repository.get("id")
    if repository_id in cache:
        return cache[repository_id]
    envelope = json_mapping(repository.get("encrypted_restic_access_key"))
    if not envelope:
        return None
    if not private_key:
        raise AgentExeption(f"Repository {repository_id} cannot be unlocked without agent key")
    try:
        password = decrypt_with_private_key(envelope, private_key)
    except SecretEnvelopeError as exc:
        raise AgentExeption(f"Repository {repository_id} agent key envelope is invalid") from exc
    cache[repository_id] = password
    return password


def recovery_password(repository, private_key, secret_values, request):
    repository_id = repository.get("id")
    secret_id = repository.get("password_secret_id")
    if secret_id in secret_values:
        return secret_values[secret_id]["value"]
    envelope = json_mapping(repository.get("encrypted_recovery_key"))
    if not envelope:
        response = request("repository_recovery_envelope", repository_id=repository_id)
        if response.get("success"):
            envelope = json_mapping((response.get("result") or {}).get("encrypted_recovery_key"))
    if not envelope:
        raise AgentExeption(f"Repository {repository_id} has no agent key and no recovery envelope")
    if not private_key:
        raise AgentExeption(f"Repository {repository_id} cannot be provisioned without agent key")
    try:
        return decrypt_with_private_key(envelope, private_key)
    except SecretEnvelopeError as exc:
        raise AgentExeption(f"Repository {repository_id} provisioning envelope is invalid") from exc


def store_agent_key(repository_id, password, *, public_key, repositories, json_type, request):
    if not public_key:
        raise AgentExeption(f"Repository {repository_id} agent key cannot be sealed without public key")
    try:
        envelope = encrypt_for_public_key(password, public_key)
    except SecretEnvelopeError as exc:
        raise AgentExeption(f"Repository {repository_id} agent key could not be sealed") from exc
    # Keep an offline-unlockable copy even if the server acknowledgement is lost.
    repositories.update({"id": repository_id, "encrypted_restic_access_key": envelope}, ["id"],
                        types={"encrypted_restic_access_key": json_type})
    response = request("store_repository_agent_key", repository_id=repository_id, encrypted_agent_key=envelope)
    if not response.get("success"):
        raise AgentExeption(f"Repository {repository_id} agent key could not be stored on backend")


def store_secret_value(envelope, private_key, cache):
    secret_id = envelope.get("user_secret_id")
    encrypted_value = json_mapping(envelope.get("encrypted_value"))
    if not secret_id or not encrypted_value:
        return
    if not private_key:
        raise AgentExeption(f"Secret {secret_id} cannot be unlocked without agent key")
    try:
        value = decrypt_with_private_key(encrypted_value, private_key)
    except SecretEnvelopeError as exc:
        raise AgentExeption(f"Secret {secret_id} envelope is invalid") from exc
    cache[secret_id] = {"type": envelope.get("type"), "value": value, "public_data": envelope.get("public_data") or {}}
