class ResticError(Exception):
    pass


class ResticFailedError(ResticError):
    def __init__(self, *args, snapshot_id=None, return_code=None):
        super().__init__(*args)
        self.snapshot_id = snapshot_id
        self.return_code = return_code


class ResticBinaryNotFoundError(ResticError):
    pass


class ResticCancelledError(ResticError):
    pass


class ResticTimeoutError(ResticError):
    pass


def is_repository_locked(error):
    """Lock contention means the command could not run, not that repository data failed verification."""
    return isinstance(error, ResticFailedError) and error.return_code == 11
