from datetime import datetime

import bcrypt
from flask import current_app, request
from flask_socketio import Namespace
from sqlalchemy import delete

from drastic_common.agent.commands import AGENT_PROTOCOL_VERSION
from drastic_common.ssh_keys import ssh_public_key_algorithm, ssh_public_key_fingerprint
from drastic_server.extensions import db, socketio
from drastic_server.models.agent import Agent, AgentSession
from drastic_server.services.agent import AgentRequest
from drastic_server.utils.realtime import emit_agent_state


class AgentNamespace(Namespace):

    def on_connect(self, auth):
        
        current_app.logger.info(f'Agent {request.remote_addr} attempting connection...')
        #print(f'Auth: {auth}, SID: {request.sid}')
        
        # Load auth data 
        try:
            agent_id = auth['agent_id']
            agent_secret = auth['agent_secret']
            agent_os = auth['agent_os']
            agent_hostname = auth['agent_hostname']
            agent_version = auth['agent_version']
            agent_install_type = auth.get('agent_install_type')
            agent_public_key = auth.get('agent_public_key')
            agent_ssh_public_key = auth.get('agent_ssh_public_key')
            protocol_version = auth.get('protocol_version', 0)

        # Terminate connection on invalid authdata
        except (KeyError, TypeError, AttributeError):
            current_app.logger.info('Terminating connection (Invalid authdata received)')
            return False

        if type(protocol_version) is not int or not 0 <= protocol_version <= AGENT_PROTOCOL_VERSION:
            current_app.logger.info('Terminating connection (Unsupported agent protocol version)')
            return False
        
        # Load agent
        agent = Agent.query.filter( Agent.id==agent_id ).first()

        # Terminate connection if unknown agent
        if not agent:
            return False

        # Verify password
        if bcrypt.checkpw(agent_secret.encode() , agent.secret.encode()):
            # Set agent data
            agent.os = agent_os
            agent.hostname = agent_hostname
            agent.version = agent_version
            agent.protocol_version = protocol_version
            agent.install_type = Agent.normalize_install_type(agent_install_type)
            if agent_public_key and agent.public_key != agent_public_key:
                agent.public_key = agent_public_key
            if agent_ssh_public_key and agent.ssh_public_key != agent_ssh_public_key:
                agent.ssh_public_key = agent_ssh_public_key
                agent.ssh_key_fingerprint = ssh_public_key_fingerprint(agent_ssh_public_key)
                agent.ssh_key_algorithm = ssh_public_key_algorithm(agent_ssh_public_key)
            agent.last_connection = datetime.now()
            # Create agent session
            db.session.execute(delete(AgentSession).where(AgentSession.agent_id == agent.id))
            agent_session = AgentSession( agent_id=agent.id, request_sid=request.sid )
            db.session.add(agent_session)
            db.session.commit()
            emit_agent_state(agent, online=True)

            current_app.logger.info(f'Agent {agent.id} connected with ip {request.remote_addr}')
        else:
            current_app.logger.info('Termating connection (Agent authentication failed)')
            return False
        
    # def on_message(self, data):
    #     print(data)
    
    def on_disconnect(self):
        agent_session = AgentSession.query.filter( AgentSession.request_sid == request.sid ).first()

        if agent_session:
            print(f'Removing session for agent {agent_session.agent_id}#{agent_session.request_sid}')

            agent = agent_session.agent
            agent.last_connection = datetime.now()
            db.session.delete(agent_session)
            db.session.commit()
            emit_agent_state(agent, online=False)

    def on_request(self, data):
        try:
            agent_request = AgentRequest.get_by_sid( request.sid )
            return {'success': True, 'result': getattr(agent_request, data['action'])( **data['args'] ) }
        except Exception as e:
            print(e)
            return {'success': False, 'result': e}

agentnamespace = AgentNamespace('/agent')
socketio.on_namespace(agentnamespace)
