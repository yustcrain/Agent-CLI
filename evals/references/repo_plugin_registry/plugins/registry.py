def _key(value):
    if not isinstance(value,str) or not value.strip():
        raise ValueError("plugin keys must be non-empty strings")
    return value.strip().casefold()

class Registry:
    def __init__(self):
        self._plugins = {}

    def register(self, plugin):
        keys=[_key(plugin.name),*(_key(a) for a in plugin.aliases)]
        if len(keys) != len(set(keys)):
            keys=list(dict.fromkeys(keys))
        for key in keys:
            owner=self._plugins.get(key)
            if owner is not None and owner is not plugin:
                raise ValueError(f"plugin key already registered: {key}")
        self._plugins.update({key:plugin for key in keys})

    def get(self, name):
        return self._plugins[_key(name)]
