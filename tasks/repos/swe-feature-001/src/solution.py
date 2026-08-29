"""A simple key-value cache.

TODO: This cache currently silently drops new entries when full.
It should evict the least-recently-used entry instead.
"""


class Cache:
    """A simple key-value cache with a maximum size.

    When the cache is full and a new key is inserted, the
    least-recently-used entry should be evicted. Currently
    this does not happen — new keys are silently dropped.
    """

    def __init__(self, max_size=100):
        self.max_size = max_size
        self._store: dict[str, object] = {}

    def get(self, key):
        """Return the value for *key*, or None if not present."""
        return self._store.get(key)

    def put(self, key, value):
        """Insert or update *key* with *value*."""
        if len(self._store) >= self.max_size and key not in self._store:
            # BUG: silently drops the new entry instead of evicting LRU
            return
        self._store[key] = value

    def __len__(self):
        return len(self._store)

    def __contains__(self, key):
        return key in self._store
