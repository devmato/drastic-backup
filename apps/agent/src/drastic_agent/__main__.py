import atexit
import logging
import os
import signal
import sys

import click

from drastic_agent.agent import Agent, AgentExeption
from drastic_agent.config import DefaultConfig, env_value


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        main()


def main():
    env_name = env_value("DRASTIC_ENV", DefaultConfig.ENV).lower()
    log_levels = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARN,
        "WARNING": logging.WARN,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    configured_level = str(os.environ.get("DRASTIC_LOGLEVEL") or "").strip().upper()
    default_level = "DEBUG" if env_name in {"dev", "test"} else "INFO"
    log_level = log_levels.get(configured_level or default_level, logging.INFO)

    logging.basicConfig(level=log_level)

    try:
        agent = Agent()
        agent.init_config()
        atexit.register(agent.shutdown)
        signal.signal(signal.SIGTERM, lambda signum, frame: agent.shutdown())
        signal.signal(signal.SIGINT, lambda signum, frame: agent.shutdown())
        agent.startup()
    except AgentExeption as e:
        logging.critical(f"Critical error during start: {e}")
        sys.exit(1)


@cli.command()
def register():
    agent = Agent()
    agent.init_config()

    if agent.configured:
        print(f"Agent {agent.hostname} is configured with id {agent.identifier}.")
    else:
        print("Agent registration requires DRASTIC_USER and DRASTIC_PASSWORD.")


if __name__ == "__main__":
    cli()
