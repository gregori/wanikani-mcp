"""Entrypoint do Vercel: expõe o app ASGI como `app`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from wanikani_mcp.server import create_app  # noqa: E402

app = create_app()
