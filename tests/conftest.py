"""Run every test against a throwaway profile, never the user's own.

The test modules point APPDATA at a temp dir with os.environ.setdefault(),
which does nothing on Windows, where APPDATA is always set: the suite then
read and wrote the real %APPDATA%\\FlowBench (settings, authorized targets,
the relay configuration) and even tried to reach the user's relay. pytest
imports this file before any test module, so the override is in place before
app.services.settings picks its path.
"""
import atexit
import os
import shutil
import tempfile

_PROFILE = tempfile.mkdtemp(prefix="flowbench-tests-")
for _name in ("APPDATA", "LOCALAPPDATA"):
    os.environ[_name] = os.path.join(_PROFILE, _name)
    os.makedirs(os.environ[_name], exist_ok=True)
atexit.register(shutil.rmtree, _PROFILE, True)
