"""Application failures; transport adapters decide how to present them."""


class ServiceException(Exception):
    pass


class AgentServiceException(ServiceException):
    pass


class RestoreServiceException(ServiceException):
    pass


class ResourceNotFound(ServiceException):
    pass


class ResourceConflict(ServiceException):
    pass


class InvalidInput(ServiceException):
    pass


class AuthenticationFailed(ServiceException):
    pass


class AgentCommandFailed(ServiceException):
    def __init__(self, response):
        super().__init__(response.get("log", "Error"))
        self.response = response
