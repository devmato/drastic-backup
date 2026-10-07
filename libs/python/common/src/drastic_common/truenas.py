import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from marshmallow import Schema, ValidationError, fields, post_load, validate, validates_schema

from drastic_common.proxmox import validate_proxmox_api_url


def validate_api_key(value):
    if not re.fullmatch(r"[\x21-\x7e]{1,4096}", value):
        raise ValidationError("Enter a valid API key (no whitespace)")


def validate_dataset(value):
    parts = value.split("/")
    if (len(value) > 255 or any(not re.fullmatch(r"[A-Za-z0-9_.:-]+", part) for part in parts)
            or any(part in {".", "..", ".system", "ix-apps", "ix-applications", "boot-pool"} for part in parts)):
        raise ValidationError("Select a user filesystem dataset")


class TrueNASSettingsSchema(Schema):
    api_url = fields.String(required=True, validate=validate_proxmox_api_url)
    username = fields.String(required=True, validate=[validate.Length(min=1, max=255), validate.Regexp(r"^[^\s\x00-\x1f]+\Z")])
    verify_tls = fields.Boolean(load_default=True)
    host_root = fields.String(load_default="/mnt/host")

    @post_load
    def normalize(self, data, **kwargs):
        data["api_url"] = data["api_url"].rstrip("/")
        if urlsplit(data["api_url"]).path:
            raise ValidationError({"api_url": ["Enter the NAS HTTPS address without an API path"]})
        root = data["host_root"]
        if not root.startswith("/") or ".." in PurePosixPath(root).parts or re.search(r"[\x00-\x1f]", root):
            raise ValidationError({"host_root": ["Enter an absolute host-root mount path"]})
        data["host_root"] = str(PurePosixPath(root))
        return data


class TrueNASBackupConfigSchema(Schema):
    datasets = fields.List(fields.String(validate=validate_dataset), required=True, validate=validate.Length(min=1))
    include_children = fields.Boolean(load_default=False)
    exclude_datasets = fields.List(fields.String(validate=validate_dataset), load_default=list)
    exclude_patterns = fields.List(fields.String(validate=validate.Length(min=1)), load_default=list)

    @validates_schema
    def unique_datasets(self, data, **kwargs):
        for field in ("datasets", "exclude_datasets"):
            if len(data[field]) != len(set(data[field])):
                raise ValidationError({field: ["Duplicate datasets are not allowed"]})
        if not any(not any(name == excluded or name.startswith(f"{excluded}/")
                           for excluded in data["exclude_datasets"]) for name in data["datasets"]):
            raise ValidationError({"datasets": ["Select at least one dataset that is not excluded"]})

    @post_load
    def omit_empty_exclusions(self, data, **kwargs):
        # Older agents reject unknown fields, even when the list is empty.
        if not data["exclude_datasets"]:
            data.pop("exclude_datasets")
        return data


class ConnectionStatusSchema(Schema):
    configured = fields.Boolean(required=True)
    available = fields.Boolean(required=True)
    guest_files_error = fields.String(allow_none=True)
    native_backups_error = fields.String(allow_none=True)


class AgentConnectionsSchema(Schema):
    proxmox = fields.Nested(ConnectionStatusSchema)
    truenas = fields.Nested(ConnectionStatusSchema)
