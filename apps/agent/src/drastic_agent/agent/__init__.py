from drastic_agent.agent.exceptions import AgentExeption

__all__ = ["Agent", "AgentExeption"]


def __getattr__(name):
    if name == "Agent":
        from drastic_agent.runtime.agent import Agent

        return Agent

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
