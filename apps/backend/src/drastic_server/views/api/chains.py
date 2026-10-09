from flask import request
from flask.views import MethodView
from flask_jwt_extended import get_jwt_identity, jwt_required
from flask_smorest import Blueprint, abort

from drastic_server.schemas.chain import ChainInputSchema
from drastic_server.services import chains
from drastic_server.views.api.errors import register_service_errors

blp = Blueprint("chains", __name__, url_prefix="/api/chains", description="Sequential backup chains")
register_service_errors(blp)


@blp.route("/")
class ChainList(MethodView):
    @jwt_required()
    def get(self):
        return chains.list_chains(get_jwt_identity())

    @jwt_required()
    @blp.arguments(ChainInputSchema)
    def post(self, data):
        return chains.persist_chain(get_jwt_identity(), data), 201


@blp.route("/<int:chain_id>")
class ChainDetail(MethodView):
    @jwt_required()
    @blp.arguments(ChainInputSchema)
    def put(self, data, chain_id):
        return chains.persist_chain(get_jwt_identity(), data, chain_id)

    @jwt_required()
    def delete(self, chain_id):
        chains.delete_chain(get_jwt_identity(), chain_id)
        return {"msg": "Backup chain deleted"}


@blp.route("/<int:chain_id>/run")
class ChainStart(MethodView):
    @jwt_required()
    def post(self, chain_id):
        return chains.start_owned_chain(get_jwt_identity(), chain_id), 202


@blp.route("/<int:chain_id>/runs")
class ChainHistory(MethodView):
    @jwt_required()
    def get(self, chain_id):
        try:
            before_id = int(request.args["before_id"]) if "before_id" in request.args else None
        except ValueError:
            abort(400, message="Invalid history cursor")
        return chains.list_runs(get_jwt_identity(), chain_id, before_id)


@blp.route("/<int:chain_id>/runs/<int:run_id>/cancel")
class ChainCancel(MethodView):
    @jwt_required()
    def post(self, chain_id, run_id):
        chains.cancel_run(get_jwt_identity(), chain_id, run_id)
        return {"msg": "Chain cancellation requested"}
