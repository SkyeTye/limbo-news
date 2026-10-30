"""The fact-checker's database: checks, claims, the provenance chains, and the
source library (every page any agent reads, with its full text).

SQLite on the Railway volume. One connection per call, WAL mode, so the
background threads and the web requests can share it safely.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_init_lock = threading.Lock()
_ready = False

SCHEMA = """
create table if not exists checks (
  id text primary key,
  url text not null,
  clean_url text not null,
  platform text,
  status text not null default 'queued',   -- queued | running | done | partial | error
  step text default '',
  error text default '',
  via text default 'app',
  creator text default '',
  posted_at text default '',
  title text default '',
  caption text default '',
  transcript text default '',
  transcript_source text default '',
  duration real default 0,
  thesis text default '',
  data text default '{}',                  -- warnings, frames, stats, context, cost
  created_at text not null,
  updated_at text not null
);
create index if not exists checks_clean_url on checks(clean_url);

create table if not exists claims (
  id text primary key,
  check_id text not null,
  idx integer not null,
  quote text not null,
  claim text default '',
  timestamp text default '',
  seconds real,
  where_said text default '',
  type text default '',
  stated_source text default '',
  central integer default 0,
  rank integer default 0,
  quote_verified integer default 0,
  status text not null default 'unchecked', -- unchecked | queued | tracing | done | stopped | failed | opinion
  ending text default '',                   -- origin_found | unsupported_assertion | circular | dead_end | stopped
  verdict text default '',
  confidence text default '',
  bottom_line text default '',
  model text default '',
  data text default '{}',                   -- mutations, unknowns, fact-check leads, attempts, deeper article id
  created_at text not null,
  updated_at text not null
);
create index if not exists claims_check on claims(check_id);

create table if not exists sources (
  id text primary key,
  url text not null unique,
  final_url text default '',
  title text default '',
  publisher text default '',
  author text default '',
  published_at text default '',
  kind text default '',                     -- primary | secondary | unsupported | factcheck | unknown
  full_text text default '',
  content_hash text default '',
  previous_hash text default '',
  archive_url text default '',
  file_path text default '',
  fetched_via text default '',
  first_read text not null,
  last_read text not null,
  read_count integer default 1
);

create table if not exists passages (
  id integer primary key autoincrement,
  source_id text not null,
  text text not null,
  location integer default -1,              -- character offset in the saved text
  created_at text not null
);
create index if not exists passages_source on passages(source_id);

create table if not exists hops (
  id integer primary key autoincrement,
  claim_id text not null,
  position integer not null,                -- 1 = closest to the video, last = the origin
  source_id text not null,
  passage_id integer,
  kind text default '',                     -- primary | secondary | unsupported
  says text default '',
  cites text default '',
  mutation text default 'none',
  mutation_note text default '',
  source_type text default '',
  attempt integer default 1,
  dropped integer default 0,
  created_at text not null
);
create index if not exists hops_claim on hops(claim_id);

create table if not exists citations (
  from_source text not null,
  to_source text not null,
  context text default '',
  created_at text not null,
  primary key (from_source, to_source)
);

create table if not exists voices (
  id integer primary key autoincrement,
  check_id text not null,
  claim_id text default '',
  name text default '',
  affiliation text default '',
  stance text default '',                   -- supports | disputes | qualifies
  quote text default '',
  source_id text default '',
  passage_id integer,
  created_at text not null
);

create table if not exists second_opinions (
  id integer primary key autoincrement,
  check_id text not null,
  claim_id text default '',
  publisher text default '',
  url text default '',
  rating text default '',
  agrees text default '',                   -- yes | no | partly
  note text default '',
  created_at text not null
);

create table if not exists events (
  id integer primary key autoincrement,
  check_id text not null,
  claim_id text default '',
  action text not null,
  depth integer default 0,
  max_depth integer default 0,
  source_id text default '',
  at text not null
);
create index if not exists events_check on events(check_id);

create table if not exists usage (
  key text primary key,
  usd real default 0,
  checks integer default 0,
  lookups integer default 0
);

create table if not exists audit (
  id integer primary key autoincrement,
  check_id text not null,
  claim_id text default '',
  agent text default '',
  tool text default '',
  input text default '',
  result text default '',
  at text not null
);
create index if not exists audit_check on audit(check_id);
"""

FTS = """
create virtual table if not exists sources_fts using fts5(source_id unindexed, title, body);
create virtual table if not exists claims_fts using fts5(claim_id unindexed, body);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(n=10):
    return uuid.uuid4().hex[:n]


def _connect():
    os.makedirs(config.FC_DIR, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma journal_mode=wal")
    con.execute("pragma foreign_keys=on")
    return con


def init():
    global _ready
    with _init_lock:
        if _ready:
            return
        con = _connect()
        try:
            con.executescript(SCHEMA)
            try:
                con.executescript(FTS)
            except sqlite3.OperationalError:
                pass  # sqlite without FTS5: search falls back to LIKE
            # Columns added after the first deploy (databases created earlier lack them).
            for table, col, decl in [("hops", "source_type", "text default ''")]:
                cols = {r[1] for r in con.execute(f"pragma table_info({table})").fetchall()}
                if col not in cols:
                    con.execute(f"alter table {table} add column {col} {decl}")
            con.commit()
        finally:
            con.close()
        os.makedirs(config.FILES_DIR, exist_ok=True)
        _ready = True


@contextmanager
def conn():
    init()
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _has_fts(con):
    return bool(con.execute("select 1 from sqlite_master where name='sources_fts'").fetchone())


def row(r):
    if r is None:
        return None
    d = dict(r)
    if "data" in d:
        try:
            d["data"] = json.loads(d["data"] or "{}")
        except ValueError:
            d["data"] = {}
    return d


# ---------- checks ----------

def create_check(url, clean_url, platform, via="app"):
    cid = new_id(8)
    t = now()
    with conn() as c:
        c.execute("insert into checks (id, url, clean_url, platform, via, created_at, updated_at) values (?,?,?,?,?,?,?)",
                  (cid, url, clean_url, platform, via, t, t))
    return cid


def find_check_by_url(clean_url):
    with conn() as c:
        return row(c.execute("select * from checks where clean_url=? order by created_at desc limit 1", (clean_url,)).fetchone())


def get_check(cid):
    with conn() as c:
        return row(c.execute("select * from checks where id=?", (cid,)).fetchone())


def update_check(cid, **fields):
    if not fields:
        return
    if "data" in fields and isinstance(fields["data"], dict):
        fields["data"] = json.dumps(fields["data"], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"update checks set {cols} where id=?", (*fields.values(), cid))


def merge_check_data(cid, **patch):
    """Merge keys into checks.data (read-modify-write under one connection)."""
    with conn() as c:
        r = c.execute("select data from checks where id=?", (cid,)).fetchone()
        data = json.loads((r["data"] if r else None) or "{}")
        data.update(patch)
        c.execute("update checks set data=?, updated_at=? where id=?", (json.dumps(data, ensure_ascii=False), now(), cid))
        return data


def list_checks(limit=100):
    with conn() as c:
        rows = c.execute("select * from checks order by created_at desc limit ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = row(r)
            central = c.execute("select verdict, confidence, status, claim from claims where check_id=? and status not in ('opinion','unchecked') order by central desc, rank asc limit 1", (d["id"],)).fetchone()
            d["central"] = dict(central) if central else None
            d.pop("transcript", None)
            out.append(d)
        return out


def stale_running_checks():
    with conn() as c:
        return [row(r) for r in c.execute("select * from checks where status in ('queued','running')").fetchall()]


# ---------- claims ----------

def add_claim(check_id, idx, **f):
    cid = new_id(10)
    t = now()
    data = f.pop("data", {}) or {}
    cols = ["id", "check_id", "idx", "created_at", "updated_at", "data"] + list(f.keys())
    vals = [cid, check_id, idx, t, t, json.dumps(data, ensure_ascii=False)] + list(f.values())
    with conn() as c:
        c.execute(f"insert into claims ({', '.join(cols)}) values ({', '.join('?' for _ in cols)})", vals)
        if _has_fts(c):
            c.execute("insert into claims_fts (claim_id, body) values (?,?)", (cid, f"{f.get('claim','')} {f.get('quote','')}"))
    return cid


def get_claim(claim_id):
    with conn() as c:
        return row(c.execute("select * from claims where id=?", (claim_id,)).fetchone())


def claims_for(check_id):
    with conn() as c:
        return [row(r) for r in c.execute("select * from claims where check_id=? order by central desc, rank asc, idx asc", (check_id,)).fetchall()]


def update_claim(claim_id, **fields):
    if "data" in fields and isinstance(fields["data"], dict):
        fields["data"] = json.dumps(fields["data"], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"update claims set {cols} where id=?", (*fields.values(), claim_id))


def merge_claim_data(claim_id, **patch):
    with conn() as c:
        r = c.execute("select data from claims where id=?", (claim_id,)).fetchone()
        data = json.loads((r["data"] if r else None) or "{}")
        data.update(patch)
        c.execute("update claims set data=?, updated_at=? where id=?", (json.dumps(data, ensure_ascii=False), now(), claim_id))
        return data


def search_past_claims(query, exclude_check=None, limit=5):
    """Claims traced before (in other checks) that look like this one."""
    q = _fts_query(query)
    if not q:
        return []
    with conn() as c:
        if _has_fts(c):
            rows = c.execute(
                "select cl.* from claims_fts f join claims cl on cl.id=f.claim_id "
                "where claims_fts match ? and cl.status in ('done','stopped') and (? is null or cl.check_id != ?) "
                "order by rank limit ?", (q, exclude_check, exclude_check, limit)).fetchall()
        else:
            like = f"%{query[:60]}%"
            rows = c.execute("select * from claims where claim like ? and status in ('done','stopped') limit ?", (like, limit)).fetchall()
        return [row(r) for r in rows]


# ---------- sources (the library) ----------

def normalize_url(url):
    u = (url or "").strip()
    u = re.sub(r"#.*$", "", u)
    u = re.sub(r"[?&](utm_[^=&]+|fbclid|gclid|mc_cid|mc_eid|ref|ref_src)=[^&]*", "", u)
    u = u.replace("?&", "?").rstrip("?&")
    return u.rstrip("/")


def text_hash(text):
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()[:24]


def save_source(url, full_text, title="", final_url="", fetched_via="", archive_url="", publisher="", published_at="", file_path=""):
    """Insert or refresh a source. Returns (source_id, changed_since_last_read)."""
    key = normalize_url(url)
    h = text_hash(full_text)
    t = now()
    with conn() as c:
        r = c.execute("select id, content_hash from sources where url=?", (key,)).fetchone()
        if r:
            changed = bool(full_text) and r["content_hash"] and r["content_hash"] != h
            if full_text:
                c.execute("""update sources set full_text=?, content_hash=?, previous_hash=case when ? then content_hash else previous_hash end,
                             title=coalesce(nullif(?,''), title), final_url=coalesce(nullif(?,''), final_url),
                             fetched_via=?, archive_url=coalesce(nullif(?,''), archive_url),
                             publisher=coalesce(nullif(?,''), publisher), published_at=coalesce(nullif(?,''), published_at),
                             file_path=coalesce(nullif(?,''), file_path),
                             last_read=?, read_count=read_count+1 where id=?""",
                          (full_text, h, 1 if changed else 0, title, final_url, fetched_via, archive_url, publisher, published_at, file_path, t, r["id"]))
                if _has_fts(c):
                    c.execute("delete from sources_fts where source_id=?", (r["id"],))
                    c.execute("insert into sources_fts (source_id, title, body) values (?,?,?)", (r["id"], title, full_text))
            return r["id"], bool(changed)
        sid = new_id(10)
        c.execute("""insert into sources (id, url, final_url, title, publisher, published_at, full_text, content_hash, archive_url,
                     file_path, fetched_via, first_read, last_read) values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (sid, key, final_url, title, publisher, published_at, full_text, h, archive_url, file_path, fetched_via, t, t))
        if _has_fts(c):
            c.execute("insert into sources_fts (source_id, title, body) values (?,?,?)", (sid, title, full_text))
        return sid, False


def get_source(sid):
    with conn() as c:
        return row(c.execute("select * from sources where id=?", (sid,)).fetchone())


def get_source_by_url(url):
    with conn() as c:
        return row(c.execute("select * from sources where url=?", (normalize_url(url),)).fetchone())


def update_source(sid, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"update sources set {cols} where id=?", (*fields.values(), sid))


def _fts_query(text):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]{2,}", text or "")
    stop = {"the", "and", "for", "that", "this", "with", "from", "are", "was", "were", "has", "have", "had", "not", "but", "its", "their", "they", "said", "says", "about", "into", "than", "then", "there", "what", "when", "which", "who", "will", "would", "been", "being", "more", "most", "over", "also", "just", "very"}
    words = [w for w in words if w.lower() not in stop][:12]
    return " OR ".join(f'"{w}"' for w in words)


def search_sources(query, limit=8):
    q = _fts_query(query)
    if not q:
        return []
    with conn() as c:
        if _has_fts(c):
            rows = c.execute(
                "select s.id, s.url, s.title, s.publisher, s.published_at, s.kind, s.last_read, snippet(sources_fts, 2, '[', ']', ' … ', 24) as snip "
                "from sources_fts f join sources s on s.id=f.source_id where sources_fts match ? order by rank limit ?",
                (q, limit)).fetchall()
            return [dict(r) for r in rows]
        like = f"%{query[:60]}%"
        rows = c.execute("select id, url, title, publisher, published_at, kind, last_read, substr(full_text,1,200) as snip from sources where full_text like ? limit ?", (like, limit)).fetchall()
        return [dict(r) for r in rows]


def add_passage(source_id, text, location):
    with conn() as c:
        r = c.execute("select id from passages where source_id=? and text=?", (source_id, text)).fetchone()
        if r:
            return r["id"]
        cur = c.execute("insert into passages (source_id, text, location, created_at) values (?,?,?,?)", (source_id, text, location, now()))
        return cur.lastrowid


def passages_for(source_id):
    with conn() as c:
        return [dict(r) for r in c.execute("select * from passages where source_id=?", (source_id,)).fetchall()]


def add_citation(from_source, to_source, context=""):
    if not from_source or not to_source or from_source == to_source:
        return
    with conn() as c:
        c.execute("insert or ignore into citations (from_source, to_source, context, created_at) values (?,?,?,?)",
                  (from_source, to_source, context[:500], now()))


# ---------- hops ----------

def add_hop(claim_id, source_id, passage_id, kind, says, cites, mutation, mutation_note, attempt=1, source_type=""):
    with conn() as c:
        pos = (c.execute("select coalesce(max(position),0) from hops where claim_id=? and dropped=0", (claim_id,)).fetchone()[0] or 0) + 1
        cur = c.execute("""insert into hops (claim_id, position, source_id, passage_id, kind, says, cites, mutation, mutation_note, attempt, source_type, created_at)
                           values (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (claim_id, pos, source_id, passage_id, kind, says, cites, mutation, mutation_note, attempt, source_type, now()))
        return cur.lastrowid, pos


def hops_for(claim_id, include_dropped=False):
    with conn() as c:
        q = "select * from hops where claim_id=?" + ("" if include_dropped else " and dropped=0") + " order by position"
        return [dict(r) for r in c.execute(q, (claim_id,)).fetchall()]


def reorder_hops(claim_id, ordered_ids, drop_ids=()):
    with conn() as c:
        for hid in drop_ids or ():
            c.execute("update hops set dropped=1 where id=? and claim_id=?", (hid, claim_id))
        live = [h["id"] for h in c.execute("select id from hops where claim_id=? and dropped=0 order by position", (claim_id,)).fetchall()]
        order = [h for h in ordered_ids if h in live] + [h for h in live if h not in ordered_ids]
        for i, hid in enumerate(order, 1):
            c.execute("update hops set position=? where id=?", (i, hid))


def chain_for(claim_id):
    """Hops with their source and passage attached, ready for the report."""
    with conn() as c:
        rows = c.execute("""
          select h.*, s.url, s.title, s.publisher, s.author, s.published_at, s.archive_url, s.final_url, s.content_hash, s.previous_hash,
                 p.text as passage, p.location as passage_location
          from hops h join sources s on s.id=h.source_id left join passages p on p.id=h.passage_id
          where h.claim_id=? and h.dropped=0 order by h.position""", (claim_id,)).fetchall()
        return [dict(r) for r in rows]


# ---------- context ----------

def add_voice(check_id, claim_id, name, affiliation, stance, quote, source_id, passage_id):
    with conn() as c:
        c.execute("""insert into voices (check_id, claim_id, name, affiliation, stance, quote, source_id, passage_id, created_at)
                     values (?,?,?,?,?,?,?,?,?)""", (check_id, claim_id, name, affiliation, stance, quote, source_id, passage_id, now()))


def voices_for(check_id):
    with conn() as c:
        return [dict(r) for r in c.execute("""select v.*, s.url, s.title, s.publisher from voices v left join sources s on s.id=v.source_id
                                             where v.check_id=? order by v.id""", (check_id,)).fetchall()]


def add_second_opinion(check_id, claim_id, publisher, url, rating, agrees, note):
    with conn() as c:
        c.execute("""insert into second_opinions (check_id, claim_id, publisher, url, rating, agrees, note, created_at)
                     values (?,?,?,?,?,?,?,?)""", (check_id, claim_id, publisher, url, rating, agrees, note, now()))


def second_opinions_for(check_id):
    with conn() as c:
        return [dict(r) for r in c.execute("select * from second_opinions where check_id=? order by id", (check_id,)).fetchall()]


def clear_context(check_id):
    with conn() as c:
        c.execute("delete from voices where check_id=?", (check_id,))
        c.execute("delete from second_opinions where check_id=?", (check_id,))


# ---------- audit log + rabbit hole events ----------

def audit(check_id, claim_id, agent, tool, inp, result):
    try:
        with conn() as c:
            c.execute("insert into audit (check_id, claim_id, agent, tool, input, result, at) values (?,?,?,?,?,?,?)",
                      (check_id, claim_id or "", agent, tool, json.dumps(inp, ensure_ascii=False)[:2000], str(result)[:2000], now()))
    except Exception:
        pass


def audit_for(check_id):
    with conn() as c:
        return [dict(r) for r in c.execute("select * from audit where check_id=? order by id", (check_id,)).fetchall()]


def add_event(check_id, claim_id, action, depth=0, max_depth=0, source_id=""):
    with conn() as c:
        c.execute("insert into events (check_id, claim_id, action, depth, max_depth, source_id, at) values (?,?,?,?,?,?,?)",
                  (check_id, claim_id or "", action[:40], int(depth or 0), int(max_depth or 0), source_id or "", now()))


def rabbit_hole_stats():
    """The success metric: how far down the chain people go."""
    with conn() as c:
        total = c.execute("select count(*) from checks where status in ('done','partial')").fetchone()[0]
        viewed = c.execute("select count(distinct check_id) from events where action='view'").fetchone()[0]
        opened = c.execute("select count(distinct check_id) from events where action like 'open_%'").fetchone()[0]
        origin = c.execute("select count(distinct check_id) from events where action like 'open_%' and depth>0 and depth>=max_depth").fetchone()[0]
        original_doc = c.execute("select count(distinct check_id) from events where action in ('open_live','open_saved','open_archive') and depth>0 and depth>=max_depth").fetchone()[0]
        avg = c.execute("select avg(d) from (select max(depth) d from events where action like 'open_%' group by check_id)").fetchone()[0]
        return {"checks": total, "viewed": viewed, "opened_any_receipt": opened,
                "reached_origin": origin, "opened_origin_document": original_doc,
                "avg_max_depth": round(avg or 0, 2)}


# ---------- usage ----------

def usage_add(key, usd=0.0, checks=0, lookups=0):
    with conn() as c:
        c.execute("""insert into usage (key, usd, checks, lookups) values (?,?,?,?)
                     on conflict(key) do update set usd=usd+excluded.usd, checks=checks+excluded.checks, lookups=lookups+excluded.lookups""",
                  (key, usd, checks, lookups))


def usage_get(key):
    with conn() as c:
        r = c.execute("select * from usage where key=?", (key,)).fetchone()
        return dict(r) if r else {"key": key, "usd": 0.0, "checks": 0, "lookups": 0}


def library_stats():
    with conn() as c:
        return {
            "sources": c.execute("select count(*) from sources").fetchone()[0],
            "passages": c.execute("select count(*) from passages").fetchone()[0],
            "citations": c.execute("select count(*) from citations").fetchone()[0],
            "claims_traced": c.execute("select count(*) from claims where status in ('done','stopped')").fetchone()[0],
            "checks": c.execute("select count(*) from checks").fetchone()[0],
        }
