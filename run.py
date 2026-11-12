#!/usr/bin/env python3
"""Start the MidKnight server and open the board in a browser.

    python run.py              # http://127.0.0.1:8234
    python run.py --port 9000 --no-browser
"""

import argparse
import sys
import threading
import webbrowser

if sys.version_info < (3, 10):
    sys.exit("MidKnight needs Python 3.10 or newer.")

from midknight.server import make_server  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8234)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    server = make_server(args.host, args.port)
    url = f"http://{args.host}:{server.server_port}/"
    print(f"MidKnight is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
