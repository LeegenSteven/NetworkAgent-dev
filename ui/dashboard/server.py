import http.server
import socketserver
import os

# Cloud Run passes the port as an environment variable
PORT = int(os.environ.get("PORT", 8080))
DIRECTORY = "."

class SPADirectoryRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # KILL CACHE for index.html, service-worker.js, main.dart.js
        # This kind of dev specific setting to be removed in production.
        # It makes the dashboard to load the latest code whenever a new build is deployed.
        if self.path == "/" or "index.html" in self.path or "flutter_service_worker.js" in self.path or "main.dart.js" in self.path:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_GET(self):
        # SPA ROUTING: If the file doesn't exist, serve index.html
        # This prevents 404s when refreshing on a Flutter route like /settings
        path = self.translate_path(self.path)
        if not os.path.exists(path):
            self.path = "/index.html"
        return super().do_GET()

with socketserver.TCPServer(("", PORT), SPADirectoryRequestHandler) as httpd:
    print(f"Serving Flutter Dashboard at port {PORT}")
    httpd.serve_forever()