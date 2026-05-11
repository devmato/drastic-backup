import atexit
import logging
import os
import sys

import click

from drastic_agent.agent import Agent, AgentExeption


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        main()


def main():
    env_name = str(os.environ.get("DRASTIC_ENV") or "dev").strip().lower()
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
