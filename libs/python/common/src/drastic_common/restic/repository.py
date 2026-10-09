"""Restic endpoint, credentials and backup-only hostname metadata."""


class ResticRepository:
    location = None
    password = None
    restic_id = None
    env = None

    def __init__(
        self,
        location,
        password,
        restic_id=None,
        env=None,
        ssh_private_key=None,
        ssh_known_hosts_path=None,
        backup_host=None,
    ):
        self.location = location
        self.password = password
        self.restic_id = restic_id
        self.env = env or {}
        self.ssh_private_key = ssh_private_key
        self.ssh_known_hosts_path = ssh_known_hosts_path
        self.backup_host = backup_host
