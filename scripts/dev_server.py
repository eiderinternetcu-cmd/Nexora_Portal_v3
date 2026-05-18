"""
Dev server para Windows con Python 3.14.
psycopg3 async requiere SelectorEventLoop, no ProactorEventLoop (default en Windows).
Uso: python scripts/dev_server.py [--port 8000]
"""
import asyncio
import selectors
import sys
import argparse

sys.path.insert(0, __import__("os").path.dirname(os.path.dirname(__file__))
    if (os := __import__("os")) else ".")

import uvicorn


async def _serve(host: str, port: int, reload: bool) -> None:
    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexora dev server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true")
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.run(
            _serve(args.host, args.port, not args.no_reload),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(_serve(args.host, args.port, not args.no_reload))


if __name__ == "__main__":
    main()
