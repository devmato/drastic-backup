from datetime import datetime

import bcrypt
from flask import current_app, request
from flask_socketio import Namespace
from sqlalchemy import delete

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
            agent_public_key = auth.get('agent_public_key')

        # Terminate connection on invalid authdata
        except KeyError:
            current_app.logger.info('Terminating connection (Invalid authdata received)')
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
            if agent_public_key and agent.public_key != agent_public_key:
                agent.public_key = agent_public_key
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
