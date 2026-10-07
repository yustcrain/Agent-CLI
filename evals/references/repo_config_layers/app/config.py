from copy import deepcopy
from .defaults import DEFAULTS

def _merge(base, override):
    result = deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result

def load_config(overrides, defaults=DEFAULTS):
    if not isinstance(defaults, dict) or not isinstance(overrides, dict):
        raise TypeError("defaults and overrides must be dictionaries")
    return _merge(defaults, overrides)
