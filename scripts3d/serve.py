"""Local development only. Secrets are stored outside source and never printed."""
import argparse
import os
import secrets
from pathlib import Path

import uvicorn
from server3d.api import create_app
from server3d.jobs import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path(".canvaslab3d"))
    parser.add_argument("--port", type=int, default=8031)
    args = parser.parse_args()
    store = Store(args.data)
    keyfile = store.root / "access.token"
    token = os.getenv("CANVASLAB3D_TOKEN")
    if not token:
        try:
            with keyfile.open("x", encoding="utf-8") as f:
                f.write(secrets.token_urlsafe(48))
            keyfile.chmod(0o600)
        except FileExistsError:
            pass
        token = keyfile.read_text(encoding="utf-8").strip()
    uvicorn.run(create_app(store, token), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
