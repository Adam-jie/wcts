"""Local protocol simulator. It is not a physical MPU or MCU."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json


class Demo(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in {"/status", "/bad-status"}:
            self.send_error(404)
            return
        body = json.dumps({"led": self.path == "/status", "temperature": 25,
                           "device": "local-protocol-simulator"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9188)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Demo)
    print(f"Protocol simulator: http://127.0.0.1:{server.server_port}/status", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
