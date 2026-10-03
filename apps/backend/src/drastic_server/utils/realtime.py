from drastic_server.extensions import socketio


def user_room(user_id):
    return f"user:{user_id}"


def emit_ui_event(user_id, name, data=None):
    socketio.emit(
        "event",
        {"name": name, "data": data or {}},
        namespace="/",
        to=user_room(user_id),
    )


def emit_agent_state(agent, online=None):
    emit_ui_event(
        agent.user_id,
        f"agentstate{agent.id}",
        {
            "agent_id": agent.id,
            "online": agent.online if online is None else online,
            "hostname": agent.hostname,
            "display_name": agent.display_name,
            "os": agent.os,
            "version": agent.version,
        },
    )


def emit_agents_update(user_id):
    emit_ui_event(user_id, "agentsupdate")


def emit_job_state(job):
    emit_ui_event(job.agent.user_id, f"jobstate{job.id}", {"job_id": job.id})


def emit_jobs_update(user_id):
    emit_ui_event(user_id, "jobsupdate")


def emit_operation_update(operation):
    emit_ui_event(
        operation.agent.user_id,
        f"operationupdate{operation.id}",
        {
            "operation_id": operation.id,
            "agent_id": operation.agent_id,
            "job_id": operation.job_id,
            "type": operation.type.name if operation.type else None,
            "state": operation.state.name if operation.state else None,
        },
    )
