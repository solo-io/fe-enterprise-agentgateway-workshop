#!/usr/bin/env python3
"""Check only the Solo docs MCP handshake and tool inventory. Sends no local data."""
import json
import sys
import urllib.error
import urllib.request

URL = "https://search.solo.io/mcp"
REQUIRED = {"search", "get_chunks", "get_full_page"}


def decode(body):
    text = body.decode("utf-8")
    try:
        return json.loads(text)
    except ValueError:
        for event in text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines()
                             if line.startswith("data:"))
            if data:
                result = json.loads(data)
                if "id" in result:
                    return result
        raise ValueError("no JSON-RPC response in the server event stream")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self):
        self.headers = {"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, params=None, request_id=None):
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if request_id is not None:
            payload["id"] = request_id
        req = urllib.request.Request(URL, json.dumps(payload).encode(), self.headers)
        with self.opener.open(req, timeout=15) as response:
            session = response.headers.get("Mcp-Session-Id")
            if session:
                self.headers["Mcp-Session-Id"] = session
            body = response.read(2 * 1024 * 1024)
        if request_id is None:
            return {}
        result = decode(body)
        if "error" in result:
            raise ValueError("server returned a JSON-RPC error")
        if result.get("id") != request_id:
            raise ValueError("server returned an unexpected response ID")
        return result["result"]

    def check(self):
        result = self.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "workshop-docs-check", "version": "1"}}, 1)
        self.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        self.request("notifications/initialized")
        names = set()
        cursor = None
        for request_id in range(2, 22):
            page = self.request("tools/list", {"cursor": cursor} if cursor else {}, request_id)
            names.update(tool["name"] for tool in page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        missing = REQUIRED - names
        if missing:
            raise ValueError("required tools missing: " + ", ".join(sorted(missing)))


def main():
    try:
        Client().check()
    except urllib.error.HTTPError as exc:
        print("Solo docs: NOT CONNECTED (HTTP %s). Configuration alone does not establish a connection." % exc.code)
        return 1
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        # Do not dump response bodies or environment configuration into reports.
        print("Solo docs: NOT CONNECTED (%s). Check network access and MCP configuration." % type(exc).__name__)
        return 1
    print("Solo docs: endpoint handshake and required tools verified. Also check that this agent session exposes the Solo tools.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
