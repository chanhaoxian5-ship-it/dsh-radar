"""Validate and back up the SQLite database together with immutable evidence objects."""
import hashlib,shutil,sqlite3
from pathlib import Path
from .engine import uid

def refs(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='evidence_objects'").fetchone():return []
    return list(con.execute('SELECT sha,size FROM evidence_objects'))

def check(path,objects=None):
    path=Path(path);objects=Path(objects) if objects else path.parent/'objects'
    con=sqlite3.connect(f'file:{path.resolve().as_posix()}?mode=ro',uri=True)
    try:
        if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('database validation failed')
        if not con.execute("SELECT value FROM meta WHERE key='service_epoch'").fetchone():raise ValueError('not a radar database')
        for sha,size in refs(con):
            if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('invalid object key')
            body=(objects/sha).read_bytes()
            if len(body)!=size or hashlib.sha256(body).hexdigest()!=sha:raise ValueError('missing or corrupt evidence object')
    finally:con.close()

def backup(source,destination):
    source=Path(source);destination=Path(destination);object_dir=Path(str(destination)+'.objects')
    if destination.exists() or object_dir.exists():raise ValueError('destination exists')
    check(source)
    with destination.open('xb'):pass
    src=sqlite3.connect(f'file:{source.resolve().as_posix()}?mode=ro',uri=True);dst=sqlite3.connect(destination)
    try:
        src.backup(dst);object_dir.mkdir()
        for sha,size in refs(dst):shutil.copyfile(source.parent/'objects'/sha,object_dir/sha)
    finally:dst.close();src.close()
    check(destination,object_dir)

def restore(source,directory):
    source=Path(source);directory=Path(directory);backup_objects=Path(str(source)+'.objects')
    if directory.exists():raise ValueError('restore requires a fresh directory')
    check(source,backup_objects);directory.mkdir(parents=True)
    destination=directory/'radar.sqlite3'
    src=sqlite3.connect(f'file:{source.resolve().as_posix()}?mode=ro',uri=True);con=sqlite3.connect(destination)
    try:
        src.backup(con);objects=directory/'objects';objects.mkdir()
        for sha,size in refs(con):shutil.copyfile(backup_objects/sha,objects/sha)
        con.execute("UPDATE meta SET value=? WHERE key='service_epoch'",(uid(),))
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='credentials'").fetchone():con.execute("UPDATE credentials SET revoked_at='restored-reissue-required'")
        con.execute("UPDATE runs SET owner=NULL,lease_until=NULL,status='queued',epoch=epoch+1 WHERE status='running'");con.commit()
    finally:con.close();src.close()
    check(destination)
