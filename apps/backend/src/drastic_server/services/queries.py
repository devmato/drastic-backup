"""Shared lookup semantics without Flask's HTTP-specific first_or_404."""

from drastic_server.services.exceptions import ResourceNotFound


def require_result(query):
    result = query.first()
    if result is None:
        raise ResourceNotFound("Resource not found")
    return result
