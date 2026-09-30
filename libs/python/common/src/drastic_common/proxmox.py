import re
from urllib.parse import urlsplit

from marshmallow import Schema, ValidationError, fields, post_load, validate


def validate_proxmox_api_url(value):
    try:
        url = urlsplit(value)
        port = url.port
        valid = (
            url.scheme == "https"
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and not re.search(r"[\x00-\x20\x7f]|\s", value)
            and (port is None or port > 0)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValidationError("Use an HTTPS API URL without credentials, query or fragment")


def validate_proxmox_token_secret(value):
    if not re.fullmatch(r"[\x21-\x7e]{1,1024}", value):
        raise ValidationError("Token secret must contain 1–1024 printable characters without spaces")


class ProxmoxSettingsSchema(Schema):
    api_url = fields.String(required=True, validate=validate_proxmox_api_url)
    token_id = fields.String(
        required=True,
        validate=[
            validate.Length(max=255),
            validate.Regexp(r"^(?=[\x21-\x7e]+\Z)[^=@!]+@[^=@!]+![^=@!]+\Z"),
        ],
    )
    node = fields.String(
        load_default="",
        validate=[validate.Length(max=255), validate.Regexp(r"^(?:[A-Za-z0-9][A-Za-z0-9_.-]*)?\Z")],
    )
    verify_tls = fields.Boolean(required=True)

    @post_load
    def normalize(self, data, **kwargs):
        data["api_url"] = data["api_url"].rstrip("/")
        return data
