import json
import unittest

import bcrypt
from flask import Flask

from drastic_common.secret_envelope import (
    decrypt_with_password,
    decrypt_with_private_key,
    encrypt_for_public_key,
    encrypt_with_password,
    generate_agent_keypair,
)
from drastic_server import models as _models  # noqa: F401
from drastic_server.app import _derive_secret, _derive_settings_encryption_key
from drastic_server.cli import _seed_bootstrap_admin_if_configured
from drastic_server.extensions import db
from drastic_server.models.notification import NotificationConfig
from drastic_server.models.repository import Repository
from drastic_server.models.user import User
from drastic_server.services.repository import (
    RepositorySecretError,
    ensure_user_recovery_key,
    reveal_repository_recovery_key,
    store_recovery_key,
)
from drastic_server.utils.crypto import CryptoError, decrypt, encrypt


class CryptoEnvelopeTests(unittest.TestCase):
    def test_encrypt_returns_json_serializable_envelope(self):
        envelope = encrypt("secret", key="test-key")

        json.dumps(envelope)
        self.assertEqual(envelope["v"], 1)
        self.assertEqual(envelope["alg"], "AES-256-GCM")

    def test_decrypt_roundtrip(self):
        envelope = encrypt("secret", key="test-key")

        self.assertEqual(decrypt(**envelope, key="test-key"), "secret")

    def test_decrypt_rejects_wrong_key(self):
        envelope = encrypt("secret", key="test-key")

        with self.assertRaises(CryptoError):
            decrypt(**envelope, key="wrong-key")


class SecretEnvelopeTests(unittest.TestCase):
    def test_password_envelope_roundtrip(self):
        envelope = encrypt_with_password("repo-secret", "account-password")

        json.dumps(envelope)
        self.assertEqual(decrypt_with_password(envelope, "account-password"), "repo-secret")

    def test_agent_public_key_envelope_roundtrip(self):
        private_key, public_key = generate_agent_keypair()
        envelope = encrypt_for_public_key("repo-secret", public_key)

        json.dumps(envelope)
        self.assertEqual(decrypt_with_private_key(envelope, private_key), "repo-secret")


class EncryptedModelFieldTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["ENCRYPTION_KEY"] = "field-key"

    def test_repository_environment_is_json_envelope(self):
        with self.app.app_context():
            repository = Repository(user_id=1, name="Repo", kind="custom", location="rest:http://repo")
            repository.environment = {"AWS_ACCESS_KEY_ID": "key"}

            json.dumps(repository._environment)
            self.assertEqual(repository.environment, {"AWS_ACCESS_KEY_ID": "key"})

    def test_notification_url_is_json_envelope(self):
        with self.app.app_context():
            config = NotificationConfig(user_id=1)
            config.url = "mailto://user@example.test"

            json.dumps(config._url)
            self.assertEqual(config.url, "mailto://user@example.test")


class EncryptionKeyConfigTests(unittest.TestCase):
    def test_settings_encryption_key_is_derived_from_master_secret(self):
        app = Flask(__name__)
        app.config["APP_MASTER_SECRET"] = "master-secret"

        _derive_settings_encryption_key(app)

        self.assertEqual(
            app.config["ENCRYPTION_KEY"],
            _derive_secret("master-secret", b"drastic:settings-encryption"),
        )

    def test_explicit_encryption_key_is_ignored(self):
        app = Flask(__name__)
        app.config["APP_MASTER_SECRET"] = "master-secret"
        app.config["ENCRYPTION_KEY"] = "field-key"

        _derive_settings_encryption_key(app)

        self.assertNotEqual(app.config["ENCRYPTION_KEY"], "field-key")


class RepositorySecretRevealTests(unittest.TestCase):
    def test_reveal_repository_recovery_key_requires_account_password(self):
        user = User(name="admin")
        user.set_password("account-password", recovery_key="user-recovery-key")
        repository = Repository(user_id=1, name="Repo", kind="custom", location="rest:http://repo")
        store_recovery_key(repository, "repo-password", "user-recovery-key")

        self.assertEqual(
            reveal_repository_recovery_key(user, repository, "account-password"),
            "repo-password",
        )

        with self.assertRaisesRegex(RepositorySecretError, "Wrong account password"):
            reveal_repository_recovery_key(user, repository, "wrong-password")

    def test_ensure_user_recovery_key_roundtrip(self):
        user = User(name="admin")
        user.set_password("account-password", recovery_key="user-recovery-key")

        self.assertEqual(ensure_user_recovery_key(user, "account-password"), "user-recovery-key")


class BootstrapSeedTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BOOTSTRAP_ADMIN_USERNAME="admin",
            BOOTSTRAP_ADMIN_PASSWORD="new-password",
        )
        db.init_app(self.app)
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_bootstrap_creates_missing_admin(self):
        with self.app.app_context():
            user = _seed_bootstrap_admin_if_configured()

            self.assertIsNotNone(user)
            self.assertEqual(user.name, "admin")
            self.assertTrue(bcrypt.checkpw(b"new-password", user.password.encode("utf-8")))

    def test_bootstrap_does_not_update_existing_admin_by_default(self):
        with self.app.app_context():
            user = User(name="admin")
            user.set_password("old-password")
            db.session.add(user)
            db.session.commit()

            _seed_bootstrap_admin_if_configured()
            db.session.refresh(user)

            self.assertTrue(bcrypt.checkpw(b"old-password", user.password.encode("utf-8")))

    def test_bootstrap_updates_existing_admin_when_enabled(self):
        with self.app.app_context():
            user = User(name="admin")
            user.set_password("old-password")
            db.session.add(user)
            db.session.commit()

            self.app.config["BOOTSTRAP_ADMIN_UPDATE"] = True
            _seed_bootstrap_admin_if_configured()
            db.session.refresh(user)

            self.assertTrue(bcrypt.checkpw(b"new-password", user.password.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
