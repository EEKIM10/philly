import logging
import typing

from typing import Tuple, List
from .db import *


def validate_config(config: dict, *, set_defaults: bool = True) -> Tuple[dict, List[str]]:
    """Validates config and returns any missing keys."""
    required_keys = [
        "homeserver",
        # "database"
        "user",
        "login"
    ]
    defaults = {
        "device_name": "jimmy-bot",
        "store_path": "./state"
    }
    missing = []
    for key in required_keys:
        if key not in config:
            missing.append(key)
    if missing:
        return config, missing  # no point processing the rest

    if set_defaults:
        for key, value in defaults.items():
            if key not in config:
                logging.getLogger(__name__).warning(f"Setting default value for {key} to {value!r}")
                config[key] = value

    return config, missing


def command(name: str = None):
    """Sets up a function to later be added by Client.bulk_register_commands()"""
    def decorator(func):
        func._is_command = True
        func._command_name = name or func.__name__
        return func
    return decorator
