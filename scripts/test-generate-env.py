"""Check that generated env files use defaults without sharing test storage."""

import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def generate(kind, answers=None):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/generate-env.sh", root / "scripts/generate-env.sh")
        subprocess.run(
            ["bash", str(root / "scripts/generate-env.sh"), kind],
            input="\n".join([kind, *(answers or [""] * 16), ""]) + "\n",
            text=True,
            capture_output=True,
            check=True,
        )
        return (root / ".env").read_text()


if __name__ == "__main__":
    prod = generate("prod")
    assert "DRASTIC_SERVER_IMAGE=" not in prod
    assert "DRASTIC_AGENT_IMAGE=" not in prod
    assert "MARIADB_DATABASE=" not in prod
    assert "DRASTIC_HOST_DB_PATH=" not in prod
    assert "DRASTIC_HOST_BACKEND_PORT=" not in prod
    assert "DRASTIC_JWT_COOKIE_SECURE=true" in prod

    test = generate("test")
    assert "DRASTIC_SERVER_IMAGE=" not in test
    assert "DRASTIC_AGENT_IMAGE=" not in test
    assert 'DRASTIC_ENV="test"' in test
    assert 'DRASTIC_TESTING="true"' in test
    assert 'DRASTIC_HOST_BACKEND_PORT="5051"' in test
    assert 'DRASTIC_HOST_DB_PATH="/opt/drastic-test/db"' in test
    assert 'DRASTIC_REST_SERVER_STORAGE_PATH="/opt/drastic-test/restic"' in test

    answers = [""] * 16
    answers[0] = "ghcr.io/devmato/drastic-backup-server:v0.2.0"
    answers[4:7] = ["backups", "backup_user", "hexabc"]
    answers[9] = "5052"
    custom = generate("prod", answers)
    assert 'DRASTIC_SERVER_IMAGE="ghcr.io/devmato/drastic-backup-server:v0.2.0"' in custom
    assert 'DRASTIC_AGENT_IMAGE="ghcr.io/devmato/drastic-backup-agent:v0.2.0"' in custom
    assert 'MARIADB_DATABASE="backups"' in custom
    assert 'MARIADB_USER="backup_user"' in custom
    assert "backup_user:hexabc@db:3306/backups" in custom
    assert 'DRASTIC_BIND_BACKEND_PORT="5052"' in custom
    assert 'DRASTIC_HOST_BACKEND_PORT="5052"' in custom
