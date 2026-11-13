"""Persist only the public show inventory to a dedicated Git branch.

No tokens, chat IDs, browser cookies or proxy credentials enter this database.
An API error is fatal: never silently reset deduplication history.
"""
import argparse
import base64
import json
import os
import sqlite3
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BRANCH = "showtime-state"
FILE = "watch.sqlite3"


def api(method, path, data=None, missing=False):
    repo = os.environ["GITHUB_REPOSITORY"]
    req = Request(f"https://api.github.com/repos/{repo}/{path}", method=method,
                  headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                           "Accept": "application/vnd.github+json",
                           "Content-Type": "application/json"},
                  data=None if data is None else json.dumps(data).encode())
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404 and missing:
            return None
        raise RuntimeError(f"State {method} failed (HTTP {exc.code}); history was not reset") from None


def check(path):
    with sqlite3.connect(path) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("State database is damaged")
        required = {"sources", "days", "inventory", "events", "health"}
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise RuntimeError("State database schema is incomplete")


def restore(directory):
    directory.mkdir(parents=True, exist_ok=True)
    state = api("GET", f"contents/{FILE}?ref={BRANCH}", missing=True)
    revision = {"sha": None}
    if state is not None:
        if state.get("encoding") != "base64" or not state.get("content"):
            raise RuntimeError("State response is incomplete; refusing to reset history")
        target = directory / FILE
        target.write_bytes(base64.b64decode(state["content"]))
        check(target)
        revision["sha"] = state["sha"]
        print("Restored alert history")
    else:
        # A fresh runner must not silently reuse an unrelated local database.
        if (directory / FILE).exists():
            raise RuntimeError("Remote state missing but local database exists")
        print("First run: initializing alert history")
    (directory / "revision.json").write_text(json.dumps(revision))


def save(directory):
    revision = json.loads((directory / "revision.json").read_text())
    target = directory / FILE
    if not target.exists():
        print("No database created; nothing to save")
        return
    check(target)
    if api("GET", f"git/ref/heads/{BRANCH}", missing=True) is None:
        api("POST", "git/refs", {"ref": f"refs/heads/{BRANCH}", "sha": os.environ["GITHUB_SHA"]})
    data = {"message": "Save Showtime Bell alert history [skip ci]", "branch": BRANCH,
            "content": base64.b64encode(target.read_bytes()).decode()}
    if revision["sha"]:
        data["sha"] = revision["sha"]
    api("PUT", f"contents/{FILE}", data)
    print("Saved alert history")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["restore", "save"])
    args = parser.parse_args()
    directory = Path(os.environ.get("STATE_DIR", "data"))
    {"restore": restore, "save": save}[args.command](directory)
