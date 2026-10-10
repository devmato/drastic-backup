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


class TrueNASPathSchema(Schema):
    dataset = fields.String(required=True, validate=validate_dataset)
    path = fields.String(required=True)
    group = fields.String(required=True, validate=validate.OneOf(["dataset", "folder", "file"]))

    @validates_schema
    def validate_path(self, data, **kwargs):
        path = data["path"]
        if (not path or re.search(r"[\x00-\x1f]", path)
                or (path != "." and any(part in {"", ".", "..", ".zfs"} for part in path.split("/")))
                or (data["group"] == "dataset") != (path == ".")):
            raise ValidationError({"path": ["Select a path relative to the dataset root"]})


def path_is_within(entry, parent):
    if entry["dataset"] != parent["dataset"]:
        return parent["path"] == "." and entry["dataset"].startswith(parent["dataset"] + "/")
    return (parent["path"] == "." or entry["path"] == parent["path"]
            or (parent["group"] != "file" and entry["path"].startswith(parent["path"] + "/")))


class TrueNASBackupConfigSchema(Schema):
    paths = fields.List(fields.Nested(TrueNASPathSchema), required=True, validate=validate.Length(min=1))
    exclude_paths = fields.List(fields.Nested(TrueNASPathSchema), load_default=list)
    exclude_patterns = fields.List(fields.String(validate=validate.Length(min=1)), load_default=list)

    @validates_schema
    def validate_selection(self, data, **kwargs):
        for field in ("paths", "exclude_paths"):
            keys = [(entry["dataset"], entry["path"]) for entry in data[field]]
            if len(keys) != len(set(keys)):
                raise ValidationError({field: ["Duplicate paths are not allowed"]})
        excluded = {(entry["dataset"], entry["path"]) for entry in data["exclude_paths"]}
        if not any((entry["dataset"], entry["path"]) not in excluded for entry in data["paths"]):
            raise ValidationError({"paths": ["Select at least one path that is not excluded"]})


class ConnectionStatusSchema(Schema):
    configured = fields.Boolean(required=True)
    available = fields.Boolean(required=True)
    guest_files_error = fields.String(allow_none=True)
    native_backups_error = fields.String(allow_none=True)


class AgentConnectionsSchema(Schema):
    proxmox = fields.Nested(ConnectionStatusSchema)
    truenas = fields.Nested(ConnectionStatusSchema)
