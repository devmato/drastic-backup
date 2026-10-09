"""Notification configuration and explicit test delivery."""

from apprise import Apprise

from drastic_server.extensions import db
from drastic_server.models.notification import NotificationConfig
from drastic_server.services.queries import require_result


def list_configs(user_id):
    return NotificationConfig.query.filter_by(user_id=user_id).all()


def get_config(user_id, config_id):
    return require_result(NotificationConfig.query.filter_by(id=config_id, user_id=user_id))


def create_config(user_id, data):
    config = NotificationConfig(user_id=user_id, url=data["url"],
                                operation_types=data["operation_types"], operation_states=data["operation_states"])
    db.session.add(config)
    db.session.commit()


def update_config(user_id, config_id, data):
    config = get_config(user_id, config_id)
    for key in ("url", "operation_types", "operation_states"):
        if key in data:
            setattr(config, key, data[key])
    db.session.commit()


def delete_config(user_id, config_id):
    db.session.delete(get_config(user_id, config_id))
    db.session.commit()


def test_delivery(url):
    apprise = Apprise()
    apprise.add(url)
    return apprise.notify(title="Test notification", body="This is a test notification from the drastic server")
