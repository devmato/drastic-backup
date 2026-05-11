class ResticRepository:
    location = None
    password = None
    restic_id = None
    env = None

    def __init__(self, location, password, restic_id=None, env=None):
        self.location = location
        self.password = password
        self.restic_id = restic_id
        self.env = env or {}
