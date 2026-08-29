"""feedwell - All social media accounts suck. This one just sucks less.

Placeholder entry point: launches a minimal local web server and opens it
in the default browser. This will be replaced by the real Django app.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
import webbrowser

PORT = 8000

HTML = b"""<!doctype html>
<html>
<head><title>feedwell</title></head>
<body style="font-family: sans-serif; max-width: 40rem; margin: 4rem auto;">
<h1>feedwell</h1>
<p>All social media accounts suck. This one just sucks less.</p>
<p>(placeholder - real app coming soon)</p>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
        url = f"http://127.0.0.1:{PORT}"
        print(f"feedwell running at {url} (Ctrl+C to stop)")
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopping feedwell")


if __name__ == "__main__":
    main()
