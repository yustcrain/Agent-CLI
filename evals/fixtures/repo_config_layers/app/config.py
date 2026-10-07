from .defaults import DEFAULTS

def load_config(overrides, defaults=DEFAULTS):
    result = defaults.copy()
    result.update(overrides)
    return result
