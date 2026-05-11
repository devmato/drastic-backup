class ResticError(Exception):
    pass


class ResticFailedError(ResticError):
    pass


class ResticBinaryNotFoundError(ResticError):
    pass
