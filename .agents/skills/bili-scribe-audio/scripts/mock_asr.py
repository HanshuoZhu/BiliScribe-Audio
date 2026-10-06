#!/usr/bin/env python3
"""A stand-in for an OpenAI-compatible /audio/transcriptions endpoint.

Lets anyone exercise the cloud ASR path end to end -- segmentation, upload, response
parsing, timestamp offsets, output writing -- without a key, a network call or an
account. The reply names the uploaded file and the request number, so the caller can
see that every segment really was sent, one at a time, in order.

    python scripts/mock_asr.py                       # listens on 127.0.0.1:8787
    # in another shell:
    ASR_API_KEY=dummy ASR_BASE_URL=http://127.0.0.1:8787/v1 ASR_MODEL=mock \
      python scripts/transcribe.py "downloads/p01_x.m4a" --engine cloud -o /tmp/out

No third-party dependencies: http.server + re.
"""

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COUNT = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if not self.path.endswith("/audio/transcriptions"):
            self.send_error(404, "only /audio/transcriptions is implemented")
            return
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            self.send_error(401, "missing bearer token")
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        # Pull the multipart filename and the model field straight out of the raw body.
        name = re.search(rb'filename="([^"]*)"', body)
        model = re.search(rb'name="model"\r\n\r\n([^\r]*)\r\n', body)
        COUNT["n"] += 1
        text = (f"第{COUNT['n']}段测试文本。收到文件 {name.group(1).decode('utf-8', 'replace') if name else '?'}"
                f"，{len(body)} 字节，model={model.group(1).decode('utf-8', 'replace') if model else '?'}。")
        payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"[mock] {fmt % args}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    print(f"[mock] listening on http://{args.host}:{args.port}/v1  (Ctrl-C to stop)", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
