import unittest

from flask import Flask, current_app

from drastic_server.schemas.repository import RepositoryManageSchema, RepositoryResponseSchema
from drastic_server.utils.urls import build_managed_restic_location


class RepositoryManageSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = RepositoryManageSchema()

    def test_custom_repository_accepts_manage_payload(self):
        data = self.schema.load(
            {
                "name": "Repo",
                "kind": "custom",
                "location": "sftp:user@example.test:/backups/repo",
                "password": "secret",
                "recovery_key": "user-recovery-key",
            }
        )

        self.assertEqual(data["kind"], "custom")
        self.assertEqual(data["location"], "sftp:user@example.test:/backups/repo")

    def test_native_repository_accepts_name_only(self):
        data = self.schema.load(
            {
                "name": "Repo",
                "kind": "native",
                "recovery_key": "user-recovery-key",
            }
        )

        self.assertEqual(data["name"], "Repo")
        self.assertEqual(data["kind"], "native")
        self.assertIsNone(data["password"])

    def test_manage_schema_allows_ignored_edit_fields(self):
        data = self.schema.load(
            {
                "name": "Repo",
                "kind": "native",
                "location": "rest:http://elsewhere/repo",
                "repository_path": "native/manual",
                "recovery_key": "user-recovery-key",
            }
        )

        self.assertEqual(data["location"], "rest:http://elsewhere/repo")
        self.assertEqual(data["repository_path"], "native/manual")

    def test_native_repository_accepts_client_password(self):
        data = self.schema.load(
            {
                "name": "Repo",
                "kind": "native",
                "password": "secret",
                "recovery_key": "user-recovery-key",
            }
        )

        self.assertEqual(data["password"], "secret")

    def test_custom_repository_accepts_generated_password(self):
        data = self.schema.load(
            {
                "name": "Repo",
                "kind": "custom",
                "location": "sftp:user@example.test:/backups/repo",
                "recovery_key": "user-recovery-key",
            }
        )

        self.assertIsNone(data["password"])


class _FakeNativeRepository:
    id = 1
    name = "Repo"
    kind = "native"
    location = "native/test-repo"
    restic_id = None
    stats = None
    created = None
    encrypted_recovery_key = {"v": 1}
    environment = {}

    @property
    def repository_path(self):
        return self.location

    @property
    def public_location(self):
        return build_managed_restic_location(current_app.config, self.location)


class RepositoryResponseSchemaTests(unittest.TestCase):
    def test_native_repository_response_builds_public_location_from_path(self):
        app = Flask(__name__)
        app.config["PUBLIC_URL"] = "http://backend:5050"

        with app.app_context():
            data = RepositoryResponseSchema().dump(_FakeNativeRepository())

        self.assertEqual(data["location"], "rest:http://backend:5050/restic/native/test-repo")
        self.assertEqual(data["repository_path"], "native/test-repo")


if __name__ == "__main__":
    unittest.main()
