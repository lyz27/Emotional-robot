"""Mac inbox -> Jetson notes. Content-addressed imports, no source deletions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import subprocess
import sys
import time
import urllib.request


SUFFIXES = {".txt", ".md", ".markdown"}
LABEL = "com.enmotion.folder-sync"


def configure_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sync_sources (
            source TEXT NOT NULL, path TEXT NOT NULL, note_id INTEGER,
            body_hash TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(source,path));
        CREATE TABLE IF NOT EXISTS sync_versions (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL,
            body TEXT NOT NULL, body_hash TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_clients (
            source TEXT PRIMARY KEY, last_seen TEXT NOT NULL, scanned INTEGER, errors INTEGER);
    """)


def import_note(workspace, payload):
    from .companion import utc_now
    source = str(payload.get("source", "mac-inbox"))
    path = str(payload.get("path", ""))
    relative = PurePosixPath(path)
    if not source or len(source) > 80 or not path or len(path) > 500 or relative.is_absolute() or ".." in relative.parts or "\\" in path:
        raise ValueError("同步文件路径无效")
    if relative.suffix.lower() not in SUFFIXES:
        raise ValueError("仅支持 txt、md、markdown 文件")
    body = payload.get("body")
    if not isinstance(body, str) or len(body) > 20000:
        raise ValueError("单文件不能超过 20000 个字符，请拆分后再同步")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = utc_now()
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT * FROM sync_sources WHERE source=? AND path=?", (source, path)).fetchone()
        if old and old["body_hash"] == digest:
            return {"ok": True, "changed": False, "note_id": old["note_id"]}
        note = db.execute("SELECT * FROM notes WHERE id=?", (old["note_id"],)).fetchone() if old else None
        conflict = bool(note and hashlib.sha256(note["body"].encode()).hexdigest() != old["body_hash"])
        title = relative.stem[:140] + ("（Mac 同步副本）" if conflict else "")
        if note and not conflict:
            note_id = note["id"]
            db.execute("UPDATE notes SET body=?,updated_at=? WHERE id=?", (body, now, note_id))
        else:
            note_id = db.execute("INSERT INTO notes(title,body,updated_at) VALUES(?,?,?)", (title, body, now)).lastrowid
        db.execute("INSERT INTO sync_versions(source,path,body,body_hash,created_at) VALUES(?,?,?,?,?)", (source,path,body,digest,now))
        db.execute("""INSERT INTO sync_sources(source,path,note_id,body_hash,updated_at) VALUES(?,?,?,?,?)
            ON CONFLICT(source,path) DO UPDATE SET note_id=excluded.note_id,body_hash=excluded.body_hash,updated_at=excluded.updated_at""", (source,path,note_id,digest,now))
    return {"ok": True, "changed": True, "note_id": note_id, "conflict_copy": conflict}


def heartbeat(workspace, payload):
    from .companion import utc_now
    source = str(payload.get("source", "mac-inbox"))[:80]
    scanned = max(0, min(100000, int(payload.get("scanned", 0))))
    errors = max(0, min(100000, int(payload.get("errors", 0))))
    with workspace.connect() as db:
        db.execute("""INSERT INTO sync_clients(source,last_seen,scanned,errors) VALUES(?,?,?,?)
            ON CONFLICT(source) DO UPDATE SET last_seen=excluded.last_seen,scanned=excluded.scanned,errors=excluded.errors""", (source, utc_now(), scanned, errors))
    return {"ok": True}


def collect_files(root):
    root = Path(root).resolve()
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(directory)/d).is_symlink()]
        for name in sorted(files):
            path = Path(directory)/name
            if name.startswith(".") or path.is_symlink() or path.suffix.lower() not in SUFFIXES:
                continue
            yield path


def sync_once(config, state_path, post=None):
    if Path(config["folder"]).is_symlink():
        raise ValueError("同步根目录不能是符号链接")
    root = Path(config["folder"]).resolve()
    if not root.is_dir():
        raise ValueError("同步文件夹不存在")
    state_path = Path(state_path)
    try:
        state = json.loads(state_path.read_text())
    except (FileNotFoundError, ValueError):
        state = {}
    def remote(path, payload):
        req = urllib.request.Request(config["server"].rstrip("/")+path,
            data=json.dumps(payload).encode(), headers={"Content-Type":"application/json", "Authorization":"Bearer "+config["token"]})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    post = post or remote
    imported, scanned, errors = 0, 0, []
    for path in collect_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            before = path.stat()
            if time.time()-before.st_mtime < 2:
                continue
            scanned += 1
            if before.st_size > 100000:
                raise ValueError("超过100KB，请拆分")
            raw = path.read_bytes()
            after = path.stat()
            if (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size):
                continue
            body = raw.decode("utf-8-sig")
            if len(body) > 20000:
                raise ValueError("超过20000字符，请拆分")
            digest = hashlib.sha256(body.encode()).hexdigest()
            if state.get(relative) == digest:
                continue
            result = post("/api/sync/import", {"source":"mac-inbox", "path":relative, "body":body})
            if not result.get("ok"):
                raise ValueError("服务未确认导入")
            state[relative] = digest
            imported += int(result.get("changed", False))
        except Exception as exc:
            errors.append({"path":relative,"error":str(exc)[:200]})
    post("/api/sync/heartbeat", {"source":"mac-inbox", "scanned":scanned, "errors":len(errors)})
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state,ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(state_path)
    return {"scanned":scanned,"imported":imported,"errors":errors}


def install(config_path):
    config = json.loads(config_path.read_text())
    logs = config_path.parent/"logs"
    logs.mkdir(parents=True,exist_ok=True)
    agents = Path.home()/"Library/LaunchAgents"
    agents.mkdir(parents=True,exist_ok=True)
    plist = agents/(LABEL+".plist")
    definition = {"Label":LABEL,"ProgramArguments":[sys.executable,"-m","enmotion_robot.folder_sync","run","--config",str(config_path)],
                  "WorkingDirectory":str(Path(__file__).resolve().parents[2]),"RunAtLoad":True,"StartInterval":30,
                  "StandardOutPath":str(logs/"sync.log"),"StandardErrorPath":str(logs/"sync-error.log"),
                  "EnvironmentVariables":{"PYTHONUNBUFFERED":"1"},"ProcessType":"Background"}
    plist.write_bytes(plistlib.dumps(definition))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl","bootout",domain+"/"+LABEL],capture_output=True)
    subprocess.run(["launchctl","bootstrap",domain,str(plist)],check=True)
    print(json.dumps({"installed":str(plist),"folder":config["folder"],"interval_seconds":30},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action",choices=["install","run"])
    parser.add_argument("--config",required=True,type=Path)
    args = parser.parse_args()
    if args.action == "install":
        install(args.config)
    else:
        config = json.loads(args.config.read_text())
        result = sync_once(config,args.config.parent/"state.json")
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == "__main__":
    main()
