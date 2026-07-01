r"""Run the app with Waitress (Windows-friendly production server).

Usage (recommended):
  .\\.venv\\Scripts\\python.exe run_waitress.py

Environment:
  HOST=0.0.0.0
  PORT=5000
  WAITRESS_THREADS=8
"""

import os

from waitress import serve

from wsgi import app


def main() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    threads = int(os.getenv("WAITRESS_THREADS", "8"))
    serve(app, host=host, port=port, threads=threads)


if __name__ == "__main__":
    main()
