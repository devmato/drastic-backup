class ResticError(Exception):
    pass


class ResticFailedError(ResticError):
    def __init__(self, *args, snapshot_id=None):
        super().__init__(*args)
        self.snapshot_id = snapshot_id


class ResticBinaryNotFoundError(ResticError):
    pass


class ResticCancelledError(ResticError):
    pass


class ResticTimeoutError(ResticError):
    pass
