import os

import uvicorn

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Démarrage de VASCO AI Agent...")
    print("🌐 Ouvrez votre navigateur sur : http://localhost:8000")
    print("=" * 60)
    # Auto-reload is for development only (set VASCO_DEV=1): on Windows it can hang
    # while long-lived streaming connections are open, leaving old code running.
    dev = os.getenv("VASCO_DEV", "").strip() in ("1", "true", "yes")
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=dev)
