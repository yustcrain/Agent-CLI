class Registry:
    def __init__(self):
        self._plugins = {}

    def register(self, plugin):
        self._plugins[plugin.name] = plugin
        for alias in plugin.aliases:
            self._plugins[alias] = plugin

    def get(self, name):
        return self._plugins[name]
