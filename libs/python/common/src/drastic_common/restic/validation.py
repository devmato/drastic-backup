"""Validate Restic check selections before starting repository maintenance."""

import re


def validate_read_data_subset(value):
    """Return a normalized subset accepted by Restic 0.19, or reject it before repository I/O."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        fraction = re.fullmatch(r"([0-9]+)/([0-9]+)", value)
        if fraction and 1 <= int(fraction[1]) <= int(fraction[2]) <= 256:
            return value
        if re.fullmatch(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?%", value):
            if 0 < float(value[:-1]) <= 100:
                return value
        size = re.fullmatch(r"\+?([0-9]+)([bBkKmMgGtT])", value)
        if size and 0 < int(size[1]) * 1024 ** "bkmgt".index(size[2].lower()) <= 2**63 - 1:
            return value
    raise ValueError("Read data subset must be n/t (1 <= n <= t <= 256), a percentage above 0 and at most 100%, or a positive size such as 1G")
