from dataclasses import dataclass
from typing import Any

@dataclass
class CacheEntry:
    value: Any
    timestamp: int

class Cache:
    def __init__(self):
        self._entries = {}

    def get(self, key, timestamp):
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.timestamp < timestamp:
            return None
        return entry.value

    def update(self, key, value, timestamp):
        entry = self._entries.get(key)
        if entry is None:
            self._entries[key] = CacheEntry(value, timestamp)
        else:
            if entry.timestamp < timestamp:
                entry.value = value
                entry.timestamp = timestamp

    def __iter__(self):
        for key, entry in self._entries.items():
            yield key, entry.value
