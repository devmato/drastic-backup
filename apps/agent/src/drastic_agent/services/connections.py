"""Manage host integrations without exposing the agent's transport or executor."""

import json
import shutil

from marshmallow import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from drastic_agent.agent.enums import AgentReportState
from drastic_agent.agent.report import AgentReport
from drastic_agent.proxmox import (
    ProxmoxApiClient,
    ProxmoxError,
    QemuVolumeGuestDriver,
    ensure_proxmox_available,
)
from drastic_agent.truenas import TRUENAS_LOCK, TrueNASClient, TrueNASError
from drastic_common.proxmox import ProxmoxSettingsSchema, validate_proxmox_token_secret
from drastic_common.secret_envelope import (
    SecretEnvelopeError,
    decrypt_with_private_key,
    encrypt_for_public_key,
)
from drastic_common.truenas import TrueNASSettingsSchema, validate_api_key


class ConnectionSettings:
    """A per-command view of current identity and persistent integration settings."""

    def __init__(self, database, settings, snapshots, *, private_key, public_key, identifier, platform):
        self.db = database
        self.settings = settings
        self.snapshots = snapshots
        self.private_key = private_key
        self.public_key = public_key
        self.identifier = identifier
        self.platform = platform

    def proxmox_client(self):
        saved = self.settings.find_one(name="proxmox")
        if saved is None:
            return ProxmoxApiClient()
        try:
            settings = json.loads(saved["settings"])
            if settings.get("disabled"):
                api = ProxmoxApiClient()
                api.token_id = api.token_secret = ""
                api.source = "agent"
                return api
            envelope = settings.pop("encrypted_value")
            if not isinstance(envelope, dict):
                raise ValueError("Invalid encrypted token")
            settings = ProxmoxSettingsSchema().load(settings)
            settings["token_secret"] = decrypt_with_private_key(envelope, self.private_key)
            validate_proxmox_token_secret(settings["token_secret"])
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise ProxmoxError("Saved Proxmox settings could not be loaded") from exc
        return ProxmoxApiClient(settings)

    def _proxmox_update_client(self, settings, envelope):
        settings = ProxmoxSettingsSchema().load(settings)
        if envelope is not None:
            if not isinstance(envelope, dict):
                raise ProxmoxError("Invalid encrypted Proxmox token")
            try:
                secret = decrypt_with_private_key(envelope, self.private_key)
            except (TypeError, ValueError) as exc:
                raise ProxmoxError("Proxmox token could not be decrypted") from exc
        else:
            current = self.proxmox_client()
            if settings["api_url"] != current.base_url or settings["token_id"] != current.token_id:
                raise ProxmoxError("Enter a token secret when changing the API URL or token ID")
            secret = current.token_secret
        if not secret:
            raise ProxmoxError("Enter a Proxmox token secret")
        validate_proxmox_token_secret(secret)
        return ProxmoxApiClient({**settings, "token_secret": secret})

    def get_proxmox(self):
        report = AgentReport.command_report()
        try:
            report.data = self.proxmox_client().public_settings
        except ProxmoxError as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def update_proxmox(self, settings, envelope=None):
        report = AgentReport.command_report()
        try:
            with self.db:
                api = self._proxmox_update_client(settings, envelope)
                saved = ProxmoxSettingsSchema().dump(api.public_settings)
                saved["encrypted_value"] = encrypt_for_public_key(api.token_secret, self.public_key)
                self.settings.upsert({"name": "proxmox", "settings": json.dumps(saved)}, ["name"])
            report.data = api.public_settings
            report.data["connections"] = self.status()
        except (ProxmoxError, SecretEnvelopeError, ValidationError) as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        except SQLAlchemyError:
            report.log_message("Could not save Proxmox settings on the agent", final_state=AgentReportState.failed)
        return report.finish()

    def test_proxmox(self, settings, envelope=None):
        report = AgentReport.command_report()
        api = None
        try:
            api = self._proxmox_update_client(settings, envelope)
            ensure_proxmox_available()
            guests = QemuVolumeGuestDriver().list_supported_guests(api)
            report.data = {"node": api.get_node(), "guest_count": len(guests)}
        except (ProxmoxError, SecretEnvelopeError, ValidationError) as exc:
            message = str(exc)
            if api and api.token_secret:
                message = message.replace(api.token_secret, "<redacted>")
            report.log_message(message, final_state=AgentReportState.failed)
        return report.finish()

    def proxmox_guests(self):
        report = AgentReport.command_report(data={"guests": []})
        try:
            report.data["guests"] = QemuVolumeGuestDriver().list_supported_guests(api=self.proxmox_client())
        except ProxmoxError as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        return report.finish()

    def status(self):
        result = {}
        for kind, factory in (("proxmox", self.proxmox_client), ("truenas", self.truenas_client)):
            try:
                configured = factory().public_settings["configured"]
            except (ProxmoxError, TrueNASError):
                configured = False
            available = all(shutil.which(tool) for tool in ("qm", "perl", "blockdev", "lvs")) if kind == "proxmox" else self.platform == "linux"
            result[kind] = {"configured": configured, "available": available}
            if kind == "proxmox" and available:
                from drastic_agent.jobs.proxmox_native import dependency_error
                from drastic_agent.services.proxmox_restore import guest_tools_error

                result[kind]["guest_files_error"] = guest_tools_error()
                result[kind]["native_backups_error"] = dependency_error()
        return result

    def truenas_client(self):
        saved = self.settings.find_one(name="truenas")
        if saved is None:
            return TrueNASClient({"api_url": "", "username": "", "verify_tls": True, "host_root": "/mnt/host"})
        try:
            settings = json.loads(saved["settings"])
            envelope = settings.pop("encrypted_value")
            settings = TrueNASSettingsSchema().load(settings)
            key = decrypt_with_private_key(envelope, self.private_key)
            validate_api_key(key)
            return TrueNASClient(settings, key)
        except (KeyError, TypeError, ValueError, ValidationError, SecretEnvelopeError) as exc:
            raise TrueNASError("Saved TrueNAS settings could not be loaded") from exc

    def truenas(self, action="get", settings=None, encrypted_value=None):
        report = AgentReport.command_report()
        locked = False
        try:
            if action not in {"get", "test", "save", "datasets", "cleanup"}:
                raise TrueNASError("Unsupported TrueNAS settings action")
            if action in {"save", "cleanup"}:
                locked = TRUENAS_LOCK.acquire(blocking=False)
                if not locked:
                    raise TrueNASError("Finish the current TrueNAS backup before changing the connection or cleaning up")
            api = self.truenas_client()
            if action in {"test", "save"}:
                settings = TrueNASSettingsSchema().load(settings)
                if action == "save" and self.snapshots.count() and settings["api_url"] != api.settings["api_url"]:
                    raise TrueNASError("Clean up pending snapshots before changing the NAS address")
                if encrypted_value is not None:
                    key = decrypt_with_private_key(encrypted_value, self.private_key)
                else:
                    if any(settings[field] != api.settings[field] for field in ("api_url", "username")):
                        raise TrueNASError("Enter an API key when changing the NAS address or user")
                    key = api.api_key
                validate_api_key(key)
                api = TrueNASClient(settings, key)
            if action == "test":
                report.data = api.test()
            elif action == "datasets":
                report.data = {"datasets": api.datasets()}
            else:
                if action == "cleanup":
                    from drastic_agent.jobs.truenas_backup import cleanup_snapshots

                    cleanup_snapshots(api, self.identifier, report)
                if action == "save":
                    saved = {**settings, "encrypted_value": encrypt_for_public_key(api.api_key, self.public_key)}
                    self.settings.upsert({"name": "truenas", "settings": json.dumps(saved)}, ["name"])
                report.data = {**api.public_settings, "connections": self.status(), "pending_snapshots": self.snapshots.count()}
        except (TrueNASError, SecretEnvelopeError, ValidationError, ValueError, TypeError) as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        except SQLAlchemyError:
            report.log_message("Could not save TrueNAS settings", final_state=AgentReportState.failed)
        finally:
            if locked:
                TRUENAS_LOCK.release()
        return report.finish()

    def delete(self, kind):
        report = AgentReport.command_report()
        locked = False
        try:
            if kind == "truenas":
                locked = TRUENAS_LOCK.acquire(blocking=False)
                if not locked or self.snapshots.count():
                    raise TrueNASError("Finish TrueNAS backups and clean up pending snapshots before removing the connection")
                self.settings.delete(name="truenas")
            elif kind == "proxmox":
                # Keep a marker so inherited environment credentials stay disabled too.
                self.settings.upsert({"name": "proxmox", "settings": json.dumps({"disabled": True})}, ["name"])
            else:
                raise TrueNASError("Unknown connection")
            report.data = {"connections": self.status()}
        except (TrueNASError, SQLAlchemyError) as exc:
            report.log_message(str(exc), final_state=AgentReportState.failed)
        finally:
            if locked:
                TRUENAS_LOCK.release()
        return report.finish()
