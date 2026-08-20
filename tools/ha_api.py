"""Shared helper: log in to the local Home Assistant and return a bearer token."""
import json, urllib.parse, urllib.request

HA = "http://127.0.0.1:8123"
CID = HA + "/"


def load_env(path="/home/sergey/iot-stack/.env"):
    env = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    return env


def _post_json(path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(HA + path, data=json.dumps(payload).encode(), headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=60))


def get_token(env):
    flow = _post_json("/auth/login_flow", {
        "client_id": CID, "handler": ["homeassistant", None], "redirect_uri": CID})["flow_id"]
    code = _post_json("/auth/login_flow/" + flow, {
        "client_id": CID,
        "username": env["HA_ADMIN_USER"],
        "password": env["HA_ADMIN_PASSWORD"]})["result"]
    body = "grant_type=authorization_code&code=%s&client_id=%s" % (
        code, urllib.parse.quote(CID, safe=""))
    req = urllib.request.Request(HA + "/auth/token", data=body.encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req, timeout=30))["access_token"]


def api(path, token, payload=None, method=None):
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(HA + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
    return json.loads(body) if body else None
