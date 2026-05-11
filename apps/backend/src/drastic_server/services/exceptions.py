class ServiceException(Exception):
    pass


class AgentServiceException(ServiceException):
    pass


class RestoreServiceException(ServiceException):
    pass
