import json
import logging
import os


def backup(output):
    last = None
    for line in reversed(output):
        line = line.strip()
        if not line:
            continue
        message = json.loads(line)
        if last is None:
            last = message
        if isinstance(message, dict) and message.get("message_type") == "summary":
            return message

    return last or {}


def default(output):
    output_string = os.linesep.join(output)
    json_lines = []

    for line in output:
        stripped_line = line.strip()
        if not stripped_line:
            continue

        try:
            json_lines.append(json.loads(stripped_line))
        except json.JSONDecodeError:
            json_lines = []
            break

    if json_lines:
        if len(json_lines) == 1:
            return json_lines[0]
        return json_lines

    try:
        return json.loads(output_string)
    except json.JSONDecodeError as err:
        logging.debug(f"Parser-Exception: {err}")
        return output_string
