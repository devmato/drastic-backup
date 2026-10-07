from drastic_server.views.api.agents import blp as agents_blp
from drastic_server.views.api.auth import blp as auth_blp
from drastic_server.views.api.chains import blp as chains_blp
from drastic_server.views.api.jobs import blp as jobs_blp
from drastic_server.views.api.notifications import blp as notifications_blp
from drastic_server.views.api.repositories import blp as repositories_blp
from drastic_server.views.api.restores import blp as restores_blp
from drastic_server.views.api.retentions import blp as retentions_blp
from drastic_server.views.api.user import blp as user_blp

blueprints = [
    auth_blp,
    user_blp,
    agents_blp,
    repositories_blp,
    jobs_blp,
    chains_blp,
    retentions_blp,
    restores_blp,
    notifications_blp,
]
