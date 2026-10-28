"""Small atomic local state store; never stores credentials or audio."""

import json
import os
from pathlib import Path


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "session.json"

    def load(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save(self, state):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.directory / "session.json.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, self.path)
