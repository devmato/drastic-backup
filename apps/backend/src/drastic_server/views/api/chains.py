from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort

from drastic_server.extensions import db
from drastic_server.models.chain import BackupChain, BackupChainRun
from drastic_server.schemas.chain import ChainInputSchema
from drastic_server.services.chains import chain_payload, run_payload, save_chain, start_chain
from drastic_server.services.repository import RepositorySecretError

blp = Blueprint("chains", __name__, url_prefix="/api/chains", description="Sequential backup chains")


def owned_chain(chain_id):
    return BackupChain.query.filter_by(id=chain_id, user_id=get_jwt_identity()).first_or_404()


def persist(chain, data):
    try:
        save_chain(chain, get_jwt_identity(), data)
    except RepositorySecretError as exc:
        db.session.rollback()
        abort(401, message=str(exc), errors={"recovery_key": ["required"]})
    except ValueError as exc:
        db.session.rollback()
        abort(400, message=str(exc))
    return chain_payload(chain)


@blp.route("/")
class ChainList(MethodView):
    @jwt_required()
    def get(self):
        return [chain_payload(chain) for chain in BackupChain.query.filter_by(user_id=get_jwt_identity()).order_by(BackupChain.name)]

    @jwt_required()
    @blp.arguments(ChainInputSchema)
    def post(self, data):
        return persist(BackupChain(), data), 201


@blp.route("/<int:chain_id>")
class ChainDetail(MethodView):
    @jwt_required()
    @blp.arguments(ChainInputSchema)
    def put(self, data, chain_id):
        return persist(owned_chain(chain_id), data)

    @jwt_required()
    def delete(self, chain_id):
        chain = owned_chain(chain_id)
        if BackupChainRun.query.filter_by(active_chain_id=chain.id).first():
            abort(409, message="Cancel and finish the active chain run before deleting the chain")
        db.session.delete(chain)
        db.session.commit()
        return {"msg": "Backup chain deleted"}


@blp.route("/<int:chain_id>/run")
class ChainStart(MethodView):
    @jwt_required()
    def post(self, chain_id):
        try:
            run = start_chain(owned_chain(chain_id))
        except ValueError as exc:
            abort(409, message=str(exc))
        return run_payload(run), 202


@blp.route("/<int:chain_id>/runs")
class ChainHistory(MethodView):
    @jwt_required()
    def get(self, chain_id):
        owned_chain(chain_id)
        from flask import request

        try:
            before_id = int(request.args["before_id"]) if "before_id" in request.args else None
        except ValueError:
            abort(400, message="Invalid history cursor")
        query = BackupChainRun.query.filter_by(chain_id=chain_id)
        if before_id:
            query = query.filter(BackupChainRun.id < before_id)
        return [run_payload(run) for run in query.order_by(BackupChainRun.id.desc()).limit(25)]


@blp.route("/<int:chain_id>/runs/<int:run_id>/cancel")
class ChainCancel(MethodView):
    @jwt_required()
    def post(self, chain_id, run_id):
        owned_chain(chain_id)
        run = BackupChainRun.query.filter_by(id=run_id, chain_id=chain_id).first_or_404()
        if run.state == "running":
            run.cancel_requested = True
            db.session.commit()
        return {"msg": "Chain cancellation requested"}
