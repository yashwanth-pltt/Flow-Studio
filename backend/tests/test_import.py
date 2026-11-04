import importlib.util
from pathlib import Path


def test_backend_entry_exports_router():
    entry = Path(__file__).resolve().parents[1] / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("plugin_backend_entry", entry)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert getattr(module, "router", None) is not None
