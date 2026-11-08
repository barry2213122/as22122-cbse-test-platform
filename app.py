#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
as22122 - Your Personal CBSE Class 10 Test Platform
====================================================
Single-file Streamlit application. No database, no SQL, no external services.

Data (question bank, chapters, tests, results, mistakes) is saved as plain JSON files in a
folder next to this file (as22122_data/). IMPORTANT: Streamlit Community Cloud does NOT
guarantee that local files survive a restart or redeploy - use Settings & Backup to download
a backup regularly (and see the README for the auto-restore trick).

Run:        streamlit run app.py
Self-tests: python app.py --selftest   (optional)
"""
from __future__ import annotations

import base64
import copy
import csv
import hashlib
import hmac
import html
import io
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

try:
    import streamlit as st
except ImportError:  # allows `python app.py --selftest` without Streamlit
    st = None

# =============================================================================
# SECTION 1 - CONSTANTS
# =============================================================================
APP_NAME = "as22122"
TAGLINE = "Your Personal CBSE Class 10 Test Platform"
IST = timezone(timedelta(hours=5, minutes=30), "IST")

SUBJECTS = [
    "Mathematics Standard",
    "Science",
    "Social Science",
    "English Language and Literature",
    "Hindi",
]
SUBJECT_ALIASES = {
    "mathematics": "Mathematics Standard", "maths": "Mathematics Standard",
    "math": "Mathematics Standard", "mathematics standard": "Mathematics Standard",
    "science": "Science", "social science": "Social Science", "sst": "Social Science",
    "social studies": "Social Science", "english": "English Language and Literature",
    "english language and literature": "English Language and Literature",
    "hindi": "Hindi",
}
QTYPES = [
    "MCQ", "Assertion and Reason", "Short Answer", "Long Answer", "Case-Based",
    "Competency-Based", "Numerical", "Diagram-Based", "Map-Based",
    "Previous-Year Question", "Source-Based", "Extract-Based",
]
DIFFICULTIES = ["Easy", "Medium", "Hard"]
EVAL_MODES = ["option", "numeric", "text", "manual"]
EVAL_LABELS = {
    "option": "Automatic - choose one option (MCQ style)",
    "numeric": "Automatic - numerical answer (with tolerance)",
    "text": "Automatic - exact text answer",
    "manual": "Manual - I will check and award marks",
}
LETTERS = ["A", "B", "C", "D"]
AR_OPTIONS = [
    "Both A and R are true and R is the correct explanation of A.",
    "Both A and R are true but R is not the correct explanation of A.",
    "A is true but R is false.",
    "A is false but R is true.",
]
# Starter list built ONLY from the units/chapters the owner supplied. It is NOT an
# official CBSE syllabus. Edit it in "Subjects & Chapters" to match your session.
STARTER_CHAPTERS = {
    "Mathematics Standard": [
        "Real Numbers", "Polynomials", "Pair of Linear Equations in Two Variables",
        "Quadratic Equations", "Arithmetic Progressions", "Triangles",
        "Coordinate Geometry", "Introduction to Trigonometry",
        "Some Applications of Trigonometry", "Circles", "Areas Related to Circles",
        "Surface Areas and Volumes", "Statistics", "Probability",
    ],
    "Science": ["Physics", "Chemistry", "Biology"],
    "Social Science": ["History", "Geography", "Political Science", "Economics"],
    "English Language and Literature": ["Reading", "Writing", "Grammar", "Literature", "Extract-Based Questions"],
    "Hindi": ["Reading", "Writing", "Grammar", "Literature", "Extract-Based Questions"],
}
MIN_Q_FOR_RANK = 3          # minimum evaluated questions before a chapter is ranked weak/strong
SESSION_TTL = 6 * 3600      # login session token lifetime (seconds)
MAX_IMAGE_BYTES = 1_500_000

PRESETS = [
    "Quick Quiz (10 questions)", "Chapter Test", "Multiple Chapter Test", "Subject Test",
    "Full Syllabus Test", "Previous-Year Paper Practice", "Weak Chapter Test",
    "Mistake Reattempt Test",
]
STATUS_ICON = {
    "not_visited": "⚪", "not_answered": "🔴", "answered": "🟢",
    "marked": "🟣", "answered_marked": "🟣✔",
}


# =============================================================================
# SECTION 2 - SMALL UTILITIES
# =============================================================================
def now_ts() -> float:
    return time.time()


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def clean_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v).strip()


def safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    s = clean_str(v)
    if s == "":
        return default
    try:
        x = float(s)
        return x if math.isfinite(x) else default
    except ValueError:
        return default


def safe_int(v: Any, default: Optional[int] = None) -> Optional[int]:
    x = safe_float(v)
    if x is None or x != int(x):
        return default
    return int(x)


def parse_number(text: Any) -> Optional[float]:
    """Parse '12', '-3.5', '1,200', '3/4', '−7' (unicode minus) into a float."""
    s = clean_str(text).replace("\u2212", "-").replace(",", "").replace(" ", "")
    if not s:
        return None
    m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)", s)
    if m:
        den = float(m.group(2))
        return float(m.group(1)) / den if den != 0 else None
    try:
        x = float(s)
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def norm_text(s: Any) -> str:
    t = clean_str(s).casefold()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .,;:!?")


def fmt_clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_ts(ts: Optional[float]) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(float(ts), tz=IST).strftime("%d %b %Y, %I:%M %p")


def fmt_iso(iso: Optional[str]) -> str:
    if not iso:
        return "-"
    try:
        d = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return d.astimezone(IST).strftime("%d %b %Y")
    except ValueError:
        return str(iso)


def fmt_marks(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{x:g}" if abs(x - round(x)) < 1e-9 else f"{x:.2f}".rstrip("0").rstrip(".")


def pct(a: float, b: float) -> float:
    return (a / b * 100.0) if b else 0.0


# =============================================================================
# SECTION 3 - OPTIONAL SECRETS, SIMPLE .env LOADER, SESSION TOKENS
# =============================================================================
def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env reader (KEY=VALUE lines). Existing environment variables win."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


_load_dotenv()


def get_secret(name: str, default: Any = None) -> Any:
    if st is not None:
        try:
            if name in st.secrets:
                return st.secrets[name]
        except Exception:
            pass
    return os.environ.get(name, default)


def _session_key() -> bytes:
    return hashlib.sha256(("as22122-session|" + str(get_secret("APP_PASSWORD", ""))).encode()).digest()


def make_session_token(owner: str, ttl: int = SESSION_TTL, now: Optional[float] = None) -> str:
    exp = int((now or time.time()) + ttl)
    sig = hmac.new(_session_key(), f"{owner}|{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_session_token(token: str, owner: str, now: Optional[float] = None) -> bool:
    try:
        exp_s, sig = str(token).split(".", 1)
        exp = int(exp_s)
        if exp < (now or time.time()):
            return False
        good = hmac.new(_session_key(), f"{owner}|{exp}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good)
    except Exception:
        return False


# =============================================================================
# SECTION 4 - DATA MODEL (tables and columns stored in the JSON files)
# =============================================================================
# Column types: text | int | real | bool | json (json = nested list/dict stored natively)
SCHEMA: Dict[str, Dict[str, Any]] = {
    "chapters": {
        "cols": [("id", "text"), ("owner_id", "text"), ("subject", "text"), ("name", "text"),
                 ("sort_order", "int"), ("enabled", "bool"), ("created_at", "text")],
        "unique": [["owner_id", "subject", "name"]],
        "indexes": [["owner_id", "subject"]],
    },
    "questions": {
        "cols": [("id", "text"), ("owner_id", "text"), ("subject", "text"), ("chapter", "text"),
                 ("topic", "text"), ("qtype", "text"), ("difficulty", "text"), ("question_text", "text"),
                 ("options", "json"), ("correct_answer", "text"), ("eval_mode", "text"),
                 ("tolerance", "real"), ("explanation", "text"), ("model_answer", "text"),
                 ("max_marks", "real"), ("marking_scheme", "text"), ("source", "text"),
                 ("pyq_exam", "text"), ("pyq_year", "int"), ("image_b64", "text"),
                 ("created_at", "text"), ("updated_at", "text")],
        "indexes": [["owner_id", "subject", "chapter"], ["owner_id", "qtype"], ["owner_id", "pyq_year"]],
    },
    "attempts": {
        "cols": [("id", "text"), ("owner_id", "text"), ("name", "text"), ("subject", "text"),
                 ("mode", "text"), ("chapters", "json"), ("status", "text"), ("duration_min", "real"),
                 ("start_ts", "real"), ("deadline_ts", "real"), ("submitted_ts", "real"),
                 ("auto_submitted", "bool"), ("neg_marking", "bool"), ("neg_fraction", "real"),
                 ("config", "json"), ("questions", "json"), ("state", "json"),
                 ("max_total", "real"), ("obtained", "real"), ("pending_marks", "real"),
                 ("created_at", "text")],
        "indexes": [["owner_id", "status"], ["owner_id", "created_at"]],
    },
    "attempt_answers": {
        "cols": [("id", "text"), ("owner_id", "text"), ("attempt_id", "text"), ("question_id", "text"),
                 ("position", "int"), ("section", "text"), ("subject", "text"), ("chapter", "text"),
                 ("qtype", "text"), ("response", "text"), ("attempted", "bool"), ("status", "text"),
                 ("marks", "real"), ("max_marks", "real"), ("feedback", "text"),
                 ("evaluated_at", "text"), ("mistake_logged", "bool")],
        "fk": {"attempt_id": "attempts"},
        "indexes": [["owner_id", "attempt_id"], ["owner_id", "subject", "chapter"]],
    },
    "mistakes": {
        "cols": [("id", "text"), ("owner_id", "text"), ("question_id", "text"), ("subject", "text"),
                 ("chapter", "text"), ("question_text", "text"), ("my_answer", "text"),
                 ("correct_answer", "text"), ("explanation", "text"), ("first_date", "text"),
                 ("last_date", "text"), ("fail_count", "int"), ("reattempt_count", "int"),
                 ("correct_count", "int"), ("revised", "bool"), ("last_result", "text")],
        "indexes": [["owner_id", "subject", "chapter"], ["owner_id", "question_id"]],
    },
    "blueprints": {
        "cols": [("id", "text"), ("owner_id", "text"), ("name", "text"), ("config", "json"),
                 ("created_at", "text")],
        "indexes": [["owner_id"]],
    },
    "settings": {
        "cols": [("id", "text"), ("owner_id", "text"), ("skey", "text"), ("svalue", "text")],
        "unique": [["owner_id", "skey"]],
        "indexes": [["owner_id"]],
    },
}


# =============================================================================
# SECTION 5 - LOCAL JSON STORAGE
# =============================================================================
class StoreError(Exception):
    """Any storage problem. The UI shows these instead of crashing."""


def _order_list(order: Any, default: str = "id") -> List[Tuple[str, bool]]:
    items = [order] if isinstance(order, str) else list(order or [default])
    return [(o.lstrip("-"), o.startswith("-")) for o in items]


def _types(table: str) -> Dict[str, str]:
    return dict(SCHEMA[table]["cols"])


def _coerce(table: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert provided values to the column types (unknown keys are ignored)."""
    types = _types(table)
    out: Dict[str, Any] = {}
    for k, v in row.items():
        t = types.get(k)
        if t is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            v = None
        if v is None:
            out[k] = None
        elif t == "json":
            out[k] = json.loads(json.dumps(v, ensure_ascii=False, default=str))   # deep copy, JSON-safe
        elif t == "bool":
            out[k] = bool(v)
        elif t == "int":
            out[k] = None if v == "" else int(v)
        elif t == "real":
            out[k] = None if v == "" else float(v)
        else:
            out[k] = str(v)
    return out


class JsonStore:
    """One JSON file per table inside a folder. Everything is kept in memory and each change
    rewrites only that table's file (atomically, with a rolling .bak copy)."""
    backend = "json"

    def __init__(self, folder: str):
        self.folder = folder
        self.lock = threading.RLock()
        self.data: Dict[str, Dict[str, Dict[str, Any]]] = {t: {} for t in SCHEMA}
        self.notes: List[str] = []
        self._bak_ts: Dict[str, float] = {}
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".write_test")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as e:
            raise StoreError(f"Cannot write to the data folder '{folder}': {e}") from e
        for t in SCHEMA:
            self._load(t)

    def _path(self, table: str) -> str:
        return os.path.join(self.folder, f"{table}.json")

    def _load(self, table: str) -> None:
        p = self._path(table)
        had_main = os.path.exists(p)
        for cand in (p, p + ".bak"):
            if not os.path.exists(cand):
                continue
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    rows = json.load(fh)
                self.data[table] = {r["id"]: r for r in rows}
                if cand != p:
                    self.notes.append(f"{table}.json was unreadable; recovered from its backup copy.")
                return
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if had_main:
            try:
                os.replace(p, p + ".corrupt")
                self.notes.append(f"{table}.json was damaged and moved to {table}.json.corrupt; starting that table empty.")
            except OSError:
                pass

    def _flush(self, table: str) -> None:
        p = self._path(table)
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(list(self.data[table].values()), fh, ensure_ascii=False)
            if os.path.exists(p) and time.time() - self._bak_ts.get(table, 0) > 60:
                shutil.copyfile(p, p + ".bak")
                self._bak_ts[table] = time.time()
            os.replace(tmp, p)
        except OSError as e:
            raise StoreError(f"Could not save {table}.json: {e}") from e

    @staticmethod
    def _match(row: Dict[str, Any], where: Dict[str, Any]) -> bool:
        for k, v in where.items():
            if isinstance(v, tuple) and v and v[0] == "in":
                if row.get(k) not in v[1]:
                    return False
            elif row.get(k) != v:
                return False
        return True

    def _clone(self, table: str, row: Dict[str, Any], cols: List[str]) -> Dict[str, Any]:
        types = _types(table)
        out = {}
        for c in cols:
            v = row.get(c)
            t = types.get(c)
            if t == "json":
                out[c] = copy.deepcopy(v)
            elif t == "bool":
                out[c] = bool(v) if v is not None else False
            else:
                out[c] = v
        return out

    def select(self, table, where=None, cols=None, order=None, limit=None) -> List[Dict[str, Any]]:
        sel = cols or [c for c, _ in SCHEMA[table]["cols"]]
        w = {k: (("in", set(v[1])) if isinstance(v, tuple) and v and v[0] == "in" else v) for k, v in (where or {}).items()}
        with self.lock:
            rows = [r for r in self.data[table].values() if self._match(r, w)]
            for c, desc in reversed(_order_list(order)):
                rows.sort(key=lambda r, c=c: (r.get(c) is None, r.get(c) if r.get(c) is not None else 0), reverse=desc)
            if limit:
                rows = rows[:int(limit)]
            return [self._clone(table, r, sel) for r in rows]

    def _check_unique(self, table: str, merged: Dict[str, Dict[str, Any]]) -> None:
        for u in SCHEMA[table].get("unique", []):
            seen = {}
            for rid, r in merged.items():
                key = tuple(r.get(c) for c in u)
                if key in seen and seen[key] != rid:
                    raise StoreError(f"Duplicate entry for {', '.join(u)} in {table}")
                seen[key] = rid

    def upsert(self, table, rows) -> None:
        if not rows:
            return
        blank = {c: None for c, _ in SCHEMA[table]["cols"]}
        with self.lock:
            merged = dict(self.data[table])
            for r in rows:
                c = _coerce(table, r)
                rid = c.get("id")
                if not rid:
                    raise StoreError("Row without id")
                cur = dict(merged.get(rid) or blank)
                cur.update(c)
                merged[rid] = cur
            self._check_unique(table, merged)
            self.data[table] = merged
            self._flush(table)

    def update(self, table, values, where) -> None:
        vals = _coerce(table, values)
        if not vals:
            return
        w = {k: (("in", set(v[1])) if isinstance(v, tuple) and v and v[0] == "in" else v) for k, v in where.items()}
        with self.lock:
            hit = False
            for r in self.data[table].values():
                if self._match(r, w):
                    r.update(copy.deepcopy(vals))
                    hit = True
            if hit:
                self._flush(table)

    def delete(self, table, where) -> None:
        if not where:
            raise StoreError("Refusing to delete without a filter")
        w = {k: (("in", set(v[1])) if isinstance(v, tuple) and v and v[0] == "in" else v) for k, v in where.items()}
        with self.lock:
            gone = [rid for rid, r in self.data[table].items() if self._match(r, w)]
            for rid in gone:
                del self.data[table][rid]
            if gone:
                self._flush(table)

    def replace_table(self, table: str, rows: List[Dict[str, Any]]) -> None:
        blank = {c: None for c, _ in SCHEMA[table]["cols"]}
        with self.lock:
            new = {}
            for r in rows:
                c = _coerce(table, r)
                if c.get("id"):
                    d = dict(blank)
                    d.update(c)
                    new[c["id"]] = d
            self._check_unique(table, new)
            self.data[table] = new
            self._flush(table)

    def is_empty(self) -> bool:
        return not any(self.data[t] for t in ("questions", "chapters", "attempts"))

    def check(self) -> Tuple[bool, str]:
        msg = f"Local JSON files in: {os.path.abspath(self.folder)}"
        if self.notes:
            msg += " | " + " ".join(self.notes)
        return True, msg


# ---- backup / restore (plain JSON; also used for the optional auto-restore) -------------------
BACKUP_FILENAME = "as22122_backup.json"


def build_backup(store: "JsonStore", owner: str) -> str:
    tables = {t: store.select(t, {"owner_id": owner}) for t in SCHEMA}
    return json.dumps({"app": APP_NAME, "version": 2, "created": iso_now(), "tables": tables}, ensure_ascii=False)


def restore_into_store(store: "JsonStore", payload: Any, owner: str) -> Dict[str, int]:
    """Replace ALL data of `owner` with the backup contents. Raises ValueError for bad files."""
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except ValueError as e:
            raise ValueError(f"Not a valid JSON file: {e}")
    if not isinstance(payload, dict):
        raise ValueError("Backup must be a JSON object.")
    tables = payload.get("tables", payload)
    if not isinstance(tables, dict) or not any(t in tables for t in SCHEMA):
        raise ValueError("This does not look like an as22122 backup (no known tables found).")
    counts: Dict[str, int] = {}
    staged: Dict[str, List[Dict[str, Any]]] = {}
    for t in SCHEMA:
        rows = tables.get(t, [])
        if not isinstance(rows, list):
            raise ValueError(f"Table '{t}' in the backup is not a list.")
        fixed = []
        for r in rows:
            if not isinstance(r, dict):
                raise ValueError(f"A row in '{t}' is not an object.")
            r = dict(r)
            r["owner_id"] = owner
            for c, typ in SCHEMA[t]["cols"]:      # older backups may hold JSON columns as strings
                if typ == "json" and isinstance(r.get(c), str):
                    try:
                        r[c] = json.loads(r[c])
                    except ValueError:
                        r[c] = None
            fixed.append(r)
        counts[t] = len(fixed)
        staged[t] = fixed
    for t in SCHEMA:
        store.replace_table(t, staged[t])
    return counts


def _app_dir() -> str:
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def make_store() -> "JsonStore":
    """Data folder: DATA_DIR (optional secret/env) or ./as22122_data next to app.py.
    If that location is read-only, fall back to the system temp folder."""
    owner = str(get_secret("OWNER_ID", "owner"))
    wanted = str(get_secret("DATA_DIR", "") or os.path.join(_app_dir(), "as22122_data"))
    try:
        store = JsonStore(wanted)
    except StoreError:
        store = JsonStore(os.path.join(tempfile.gettempdir(), "as22122_data"))
        store.notes.append("The app folder was read-only, so the system temp folder is being used.")
    # Auto-restore: a fresh/empty server + a backup file committed next to app.py -> load it.
    seed = os.path.join(_app_dir(), BACKUP_FILENAME)
    if store.is_empty() and os.path.exists(seed):
        try:
            with open(seed, "r", encoding="utf-8") as fh:
                restore_into_store(store, json.load(fh), owner)
            store.notes.append(f"Data was automatically restored from {BACKUP_FILENAME}.")
        except (OSError, ValueError, StoreError) as e:
            store.notes.append(f"Could not auto-restore {BACKUP_FILENAME}: {e}")
    return store


# ---- module-level handles used by every data function --------------------------------------
DB = None            # set in main() / tests
OWNER = "owner"      # single user today; rows carry owner_id so multi-user could be added later


def invalidate_cache() -> None:
    if st is not None:
        try:
            st.cache_data.clear()
        except Exception:
            pass


def q_select(table, where=None, cols=None, order=None, limit=None):
    w = dict(where or {})
    w["owner_id"] = OWNER
    return DB.select(table, w, cols, order, limit)


def q_upsert(table, rows, touch=True):
    DB.upsert(table, [dict(r, owner_id=OWNER) for r in rows])
    if touch:
        invalidate_cache()


def q_update(table, values, where, touch=True):
    w = dict(where)
    w["owner_id"] = OWNER
    DB.update(table, values, w)
    if touch:
        invalidate_cache()


def q_delete(table, where, touch=True):
    w = dict(where)
    w["owner_id"] = OWNER
    DB.delete(table, w)
    if touch:
        invalidate_cache()


# =============================================================================
# SECTION 6 - CHAPTERS, SETTINGS
# =============================================================================
def load_chapters_df() -> pd.DataFrame:
    rows = q_select("chapters", order=["subject", "sort_order", "name"])
    cols = [c for c, _ in SCHEMA["chapters"]["cols"]]
    return pd.DataFrame(rows, columns=cols)


def chapter_names(df: pd.DataFrame, subject: str, enabled_only: bool = True) -> List[str]:
    if df is None or df.empty:
        return []
    d = df[df["subject"] == subject]
    if enabled_only:
        d = d[d["enabled"] == True]  # noqa: E712
    return d.sort_values(["sort_order", "name"])["name"].tolist()


def chapter_map(df: pd.DataFrame, enabled_only: bool = False) -> Dict[str, Dict[str, str]]:
    """{subject: {lowercase chapter name: canonical name}} used by validation."""
    res: Dict[str, Dict[str, str]] = {s: {} for s in SUBJECTS}
    if df is None or df.empty:
        return res
    for _, r in df.iterrows():
        if enabled_only and not r["enabled"]:
            continue
        res.setdefault(r["subject"], {})[str(r["name"]).lower()] = r["name"]
    return res


def add_chapter(subject: str, name: str) -> None:
    name = clean_str(name)
    if subject not in SUBJECTS:
        raise ValueError("Unknown subject")
    if not name:
        raise ValueError("Chapter name cannot be empty")
    df = load_chapters_df()
    if name.lower() in chapter_map(df).get(subject, {}):
        raise ValueError(f"'{name}' already exists in {subject}")
    nxt = int(df[df["subject"] == subject]["sort_order"].max() + 1) if not df[df["subject"] == subject].empty else 1
    q_upsert("chapters", [{"id": new_id("ch"), "subject": subject, "name": name, "sort_order": nxt,
                           "enabled": True, "created_at": iso_now()}])


def seed_starter_chapters() -> int:
    df = load_chapters_df()
    existing = chapter_map(df)
    rows, n = [], 0
    for subj, names in STARTER_CHAPTERS.items():
        for i, nm in enumerate(names, 1):
            if nm.lower() in existing.get(subj, {}):
                continue
            rows.append({"id": new_id("ch"), "subject": subj, "name": nm, "sort_order": i,
                         "enabled": True, "created_at": iso_now()})
            n += 1
    q_upsert("chapters", rows)
    return n


def rename_chapter(chapter_id: str, new_name: str) -> int:
    """Rename a chapter and every question that uses it. Returns number of questions updated."""
    new_name = clean_str(new_name)
    rows = q_select("chapters", {"id": chapter_id})
    if not rows:
        raise ValueError("Chapter not found")
    ch = rows[0]
    if not new_name:
        raise ValueError("New name cannot be empty")
    df = load_chapters_df()
    if new_name.lower() in chapter_map(df).get(ch["subject"], {}) and new_name.lower() != ch["name"].lower():
        raise ValueError(f"'{new_name}' already exists in {ch['subject']}")
    q_update("chapters", {"name": new_name}, {"id": chapter_id})
    qs = q_select("questions", {"subject": ch["subject"], "chapter": ch["name"]}, cols=["id"])
    if qs:
        q_update("questions", {"chapter": new_name, "updated_at": iso_now()}, {"id": ("in", [x["id"] for x in qs])})
    return len(qs)


def set_chapter_fields(chapter_id: str, **values) -> None:
    q_update("chapters", values, {"id": chapter_id})


def delete_chapter(chapter_id: str) -> None:
    rows = q_select("chapters", {"id": chapter_id})
    if not rows:
        return
    ch = rows[0]
    used = q_select("questions", {"subject": ch["subject"], "chapter": ch["name"]}, cols=["id"], limit=1)
    if used:
        raise ValueError("This chapter still has questions. Disable it instead (or move/delete its questions first).")
    q_delete("chapters", {"id": chapter_id})


def get_setting(key: str, default: str = "") -> str:
    rows = q_select("settings", {"skey": key})
    return rows[0]["svalue"] if rows and rows[0]["svalue"] is not None else default


def set_setting(key: str, value: str) -> None:
    q_upsert("settings", [{"id": f"{OWNER}:{key}", "skey": key, "svalue": value}])


def subject_label(subject: str, hindi_course: str = "") -> str:
    if subject == "Hindi" and hindi_course:
        return f"Hindi ({hindi_course})"
    return subject


# =============================================================================
# SECTION 7 - QUESTION VALIDATION, IMPORT / EXPORT
# =============================================================================
CSV_COLUMNS = ["question_id", "subject", "chapter", "topic", "qtype", "difficulty", "question_text",
               "option_a", "option_b", "option_c", "option_d", "correct_answer", "eval_mode",
               "tolerance", "explanation", "model_answer", "max_marks", "marking_scheme", "source",
               "pyq_exam", "pyq_year"]
CSV_REQUIRED = ["subject", "chapter", "qtype", "difficulty", "question_text", "max_marks"]


def _norm_key(s: str) -> str:
    s = clean_str(s).lower().replace("&", " and ")
    return re.sub(r"[\s_\-]+", " ", s).strip()


def _canon(value: str, choices: List[str], aliases: Optional[Dict[str, str]] = None) -> Optional[str]:
    k = _norm_key(value)
    for c in choices:
        if _norm_key(c) == k:
            return c
    if aliases and k in aliases:
        return aliases[k]
    return None


def default_eval_mode(qtype: str, options: List[str], correct: str) -> str:
    if qtype in ("MCQ", "Assertion and Reason"):
        return "option"
    if qtype == "Numerical":
        return "numeric"
    if len(options) >= 2 and clean_str(correct).upper() in LETTERS[:len(options)]:
        return "option"
    return "manual"


def auto_question_id(row: Dict[str, Any]) -> str:
    basis = "|".join([row["subject"], row["chapter"], row["qtype"], row["question_text"],
                      json.dumps(row.get("options") or [], ensure_ascii=False), row.get("correct_answer", "")])
    return "Q-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]


def validate_question(raw: Dict[str, Any], chapters: Optional[Dict[str, Dict[str, str]]] = None,
                      allow_new_chapter: bool = False) -> Tuple[Dict[str, Any], List[str]]:
    """Validate and normalise one question. Returns (clean_row, errors). Used by the form,
    CSV/JSON import and bulk paste, so every entry path obeys the same rules."""
    errs: List[str] = []
    g = lambda k: clean_str(raw.get(k))  # noqa: E731
    qid = g("question_id")
    if qid and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", qid):
        errs.append("question_id may only contain letters, digits, '.', '_' and '-' (max 64 characters)")
    subject = _canon(g("subject"), SUBJECTS, SUBJECT_ALIASES)
    if not subject:
        errs.append(f"Unknown subject '{g('subject')}'. Use one of: {', '.join(SUBJECTS)}")
    chapter = g("chapter")
    if not chapter:
        errs.append("chapter is required")
    elif chapters is not None and subject and not allow_new_chapter:
        canon = chapters.get(subject, {}).get(chapter.lower())
        if canon:
            chapter = canon
        else:
            errs.append(f"Chapter '{chapter}' is not in the chapter list for {subject} (add it in Subjects & Chapters)")
    qtype = _canon(g("qtype"), QTYPES)
    if not qtype:
        errs.append(f"Unknown question type '{g('qtype')}'. Use one of: {', '.join(QTYPES)}")
    diff = _canon(g("difficulty"), DIFFICULTIES)
    if not diff:
        errs.append(f"Difficulty must be Easy, Medium or Hard (got '{g('difficulty')}')")
    text = g("question_text")
    if not text:
        errs.append("question_text is required")
    marks = safe_float(g("max_marks"))
    if marks is None or marks <= 0 or marks > 100:
        errs.append("max_marks must be a number greater than 0 (and at most 100)")

    if isinstance(raw.get("options"), (list, tuple)):
        opts = [clean_str(x) for x in raw["options"]]
    else:
        opts = [g(f"option_{c.lower()}") for c in LETTERS]
    while opts and not opts[-1]:
        opts.pop()
    if len(opts) > 4:
        errs.append("At most 4 options (A-D) are supported")
        opts = opts[:4]
    if any(not o for o in opts):
        errs.append("Options must be filled in order (A, B, C, D) without gaps")
    if qtype == "Assertion and Reason" and not opts:
        opts = list(AR_OPTIONS)

    correct = g("correct_answer")
    mode = g("eval_mode").lower()
    if mode and mode not in EVAL_MODES:
        errs.append(f"eval_mode must be one of {', '.join(EVAL_MODES)}")
        mode = ""
    if not mode:
        mode = default_eval_mode(qtype or "", opts, correct)
    tol = 0.0
    if mode == "option":
        if len(opts) < 2:
            errs.append("Option-based questions need at least 2 options")
        else:
            c = correct.strip("()[]. ").upper()
            if c in LETTERS[:len(opts)]:
                correct = c
            else:
                match = [i for i, o in enumerate(opts) if norm_text(o) == norm_text(correct) and correct]
                if match:
                    correct = LETTERS[match[0]]
                else:
                    errs.append(f"correct_answer must be one of {', '.join(LETTERS[:len(opts)])} for option-based questions")
    elif mode == "numeric":
        if parse_number(correct) is None:
            errs.append("correct_answer must be a number for numerical auto-checking (e.g. 12.5 or 3/4)")
        tol = safe_float(g("tolerance"), 0.0)
        if tol is None or tol < 0:
            errs.append("tolerance must be a number >= 0")
            tol = 0.0
    elif mode == "text":
        if not correct:
            errs.append("correct_answer is required for exact-text auto-checking (use || to separate alternatives)")

    exam, year_s = g("pyq_exam"), g("pyq_year")
    year = None
    if year_s:
        year = safe_int(year_s)
        if year is None or not (1990 <= year <= 2100):
            errs.append("pyq_year must be a four-digit year (1990-2100)")
            year = None
    if exam and not year_s:
        errs.append("pyq_year is required when pyq_exam is given (never guess a year)")
    if year_s and not exam:
        errs.append("pyq_exam is required when pyq_year is given")
    if (exam or year_s) and not g("source"):
        errs.append("source is required for previous-year questions (so they can be verified)")

    row = {
        "id": qid, "subject": subject or "", "chapter": chapter, "topic": g("topic"), "qtype": qtype or "",
        "difficulty": diff or "", "question_text": text, "options": opts, "correct_answer": correct,
        "eval_mode": mode, "tolerance": float(tol or 0.0), "explanation": g("explanation"),
        "model_answer": g("model_answer"), "max_marks": float(marks) if marks else 0.0,
        "marking_scheme": g("marking_scheme"), "source": g("source"),
        "pyq_exam": exam or None, "pyq_year": year,
    }
    return row, errs


def is_verified_pyq(row: Dict[str, Any]) -> bool:
    return bool(clean_str(row.get("pyq_exam")) and safe_int(row.get("pyq_year")) and clean_str(row.get("source")))


def parse_csv_bytes(data: bytes) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """Returns (raw_rows, fatal_errors, warnings)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], ["The file is not valid UTF-8. In Excel use 'Save As -> CSV UTF-8'."], []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ["The CSV file is empty or has no header row."], []
    headers = [clean_str(h).lower() for h in reader.fieldnames]
    missing = [c for c in CSV_REQUIRED if c not in headers]
    fatal = [f"Missing required column(s): {', '.join(missing)}"] if missing else []
    warns = [f"Unknown column ignored: '{h}'" for h in headers if h and h not in CSV_COLUMNS]
    rows = []
    for r in reader:
        rows.append({clean_str(k).lower(): v for k, v in r.items() if k is not None})
    return rows, fatal, warns


def parse_json_bytes(data: bytes) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except Exception as e:
        return [], [f"Invalid JSON: {e}"], []
    if isinstance(obj, dict) and isinstance(obj.get("questions"), list):
        obj = obj["questions"]
    if not isinstance(obj, list) or not all(isinstance(x, dict) for x in obj):
        return [], ["JSON must be a list of question objects (or {\"questions\": [...]})."], []
    rows = []
    for x in obj:
        d = {clean_str(k).lower(): v for k, v in x.items()}
        if "id" in d and "question_id" not in d:
            d["question_id"] = d["id"]
        rows.append(d)
    return rows, [], []


_BULK_KEYS = {"q": "question_text", "question": "question_text", "a": "option_a", "b": "option_b",
              "c": "option_c", "d": "option_d", "ans": "correct_answer", "answer": "correct_answer",
              "exp": "explanation", "explanation": "explanation", "model": "model_answer",
              "topic": "topic", "scheme": "marking_scheme", "marks": "max_marks", "source": "source",
              "mode": "eval_mode", "tol": "tolerance", "id": "question_id"}


def parse_bulk_text(text: str, defaults: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Blocks separated by a line containing only ---. Lines look like `Q: ...`, `A: ...`, `Ans: B`."""
    blocks = re.split(r"(?m)^\s*---+\s*$", text or "")
    out = []
    pat = re.compile(r"^\s*(q|question|a|b|c|d|ans|answer|exp|explanation|model|topic|scheme|marks|source|mode|tol|id)\s*[:\uff1a]\s*(.*)$", re.I)
    for blk in blocks:
        if not blk.strip():
            continue
        d: Dict[str, Any] = dict(defaults)
        cur = None
        for line in blk.splitlines():
            m = pat.match(line)
            if m:
                cur = _BULK_KEYS[m.group(1).lower()]
                d[cur] = m.group(2).strip()
            elif cur and line.strip():
                d[cur] = (clean_str(d.get(cur)) + "\n" + line.strip()).strip()
        out.append(d)
    return out


def load_question_ids() -> set:
    return {r["id"] for r in q_select("questions", cols=["id"])}


def prepare_import(raws: List[Dict[str, Any]], chapters: Dict[str, Dict[str, str]],
                   existing_ids: set, allow_new_chapter: bool = False) -> List[Dict[str, Any]]:
    """Validate every row. Nothing is dropped silently: each row gets a status and messages."""
    results, seen = [], {}
    for i, raw in enumerate(raws, start=1):
        row, errs = validate_question(raw, chapters, allow_new_chapter)
        if not errs and not row["id"]:
            row["id"] = auto_question_id(row)
        status = "invalid" if errs else "new"
        if not errs:
            if row["id"] in seen:
                status, errs = "invalid", [f"Duplicate ID inside the file (first seen at row {seen[row['id']]})"]
            else:
                seen[row["id"]] = i
                if row["id"] in existing_ids:
                    status = "exists"
        results.append({"row": i, "status": status, "errors": errs, "data": row})
    return results


def import_questions(items: List[Dict[str, Any]], overwrite_existing: bool,
                     create_missing_chapters: bool = False) -> Dict[str, int]:
    new_rows, over_rows = [], []
    skipped = 0
    for it in items:
        if it["status"] == "new":
            new_rows.append(dict(it["data"], created_at=iso_now(), updated_at=iso_now()))
        elif it["status"] == "exists" and overwrite_existing:
            over_rows.append(dict(it["data"], updated_at=iso_now()))
        else:
            skipped += 1
    if create_missing_chapters:
        df = load_chapters_df()
        known = chapter_map(df)
        for r in new_rows + over_rows:
            if r["chapter"].lower() not in known.get(r["subject"], {}):
                add_chapter(r["subject"], r["chapter"])
                known.setdefault(r["subject"], {})[r["chapter"].lower()] = r["chapter"]
    for batch in (new_rows, over_rows):
        for i in range(0, len(batch), 300):
            q_upsert("questions", batch[i:i + 300], touch=False)
    invalidate_cache()
    return {"added": len(new_rows), "overwritten": len(over_rows), "skipped": skipped}


def question_template_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    w.writerow(["", "Mathematics Standard", "Real Numbers", "", "MCQ", "Easy",
                "REPLACE THIS with your own MCQ text", "option one", "option two", "option three", "option four",
                "B", "", "", "Why B is correct", "", "1", "1 mark for the correct option", "", "", ""])
    w.writerow(["", "Mathematics Standard", "Real Numbers", "", "Numerical", "Medium",
                "REPLACE THIS with your own numerical question", "", "", "", "", "12.5", "numeric", "0.01",
                "Working steps", "", "2", "1 for method, 1 for answer", "", "", ""])
    return buf.getvalue()


def questions_to_csv(df: pd.DataFrame) -> str:
    rows = []
    for _, r in df.iterrows():
        opts = r.get("options") if isinstance(r.get("options"), list) else []
        d = {"question_id": r["id"], "subject": r["subject"], "chapter": r["chapter"], "topic": r.get("topic", ""),
             "qtype": r["qtype"], "difficulty": r["difficulty"], "question_text": r["question_text"]}
        for i, c in enumerate("abcd"):
            d[f"option_{c}"] = opts[i] if i < len(opts) else ""
        d.update({"correct_answer": r.get("correct_answer", ""), "eval_mode": r.get("eval_mode", ""),
                  "tolerance": r.get("tolerance", ""), "explanation": r.get("explanation", ""),
                  "model_answer": r.get("model_answer", ""), "max_marks": r.get("max_marks", ""),
                  "marking_scheme": r.get("marking_scheme", ""), "source": r.get("source", ""),
                  "pyq_exam": r.get("pyq_exam") or "", "pyq_year": "" if pd.isna(r.get("pyq_year")) else int(r["pyq_year"])})
        rows.append(d)
    return pd.DataFrame(rows, columns=CSV_COLUMNS).to_csv(index=False)


def df_to_csv(df: pd.DataFrame) -> str:
    d = df.copy()
    for c in d.columns:
        if d[c].map(lambda x: isinstance(x, (list, dict))).any():
            d[c] = d[c].map(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else x)
    return d.to_csv(index=False)


# =============================================================================
# SECTION 8 - QUESTION BANK ACCESS
# =============================================================================
def load_questions_df() -> pd.DataFrame:
    cols = [c for c, _ in SCHEMA["questions"]["cols"] if c != "image_b64"]
    rows = q_select("questions", cols=cols, order=["subject", "chapter", "id"])
    df = pd.DataFrame(rows, columns=cols)
    return df


def get_question(qid: str, with_image: bool = False) -> Optional[Dict[str, Any]]:
    rows = q_select("questions", {"id": qid})
    if not rows:
        return None
    r = rows[0]
    if not with_image:
        r.pop("image_b64", None)
    return r


def get_image_bytes(qid: str) -> Optional[bytes]:
    rows = q_select("questions", {"id": qid}, cols=["image_b64"])
    if rows and rows[0].get("image_b64"):
        try:
            return base64.b64decode(rows[0]["image_b64"])
        except Exception:
            return None
    return None


def process_image(data: bytes) -> str:
    """Return base64 text. Large images are down-scaled (needs Pillow, which Streamlit installs)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        im.thumbnail((1400, 1400))
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        out = buf.getvalue()
        if len(out) > MAX_IMAGE_BYTES:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
            out = buf.getvalue()
        if len(out) > MAX_IMAGE_BYTES * 2:
            raise ValueError("Image is too large even after compression; please use a smaller image.")
        return base64.b64encode(out).decode("ascii")
    except ImportError:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Image larger than 1.5 MB and Pillow is not installed to shrink it.")
        return base64.b64encode(data).decode("ascii")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Could not read the image: {e}")


def save_question(row: Dict[str, Any], is_new: bool, image_bytes: Optional[bytes] = None,
                  remove_image: bool = False) -> str:
    """Insert a new question or update an existing one. Returns the question id."""
    row = dict(row)
    if not row.get("id"):
        row["id"] = auto_question_id(row)
    if is_new and q_select("questions", {"id": row["id"]}, cols=["id"]):
        raise ValueError(f"A question with ID '{row['id']}' already exists.")
    row["updated_at"] = iso_now()
    if is_new:
        row["created_at"] = iso_now()
    if image_bytes:
        row["image_b64"] = process_image(image_bytes)
    elif remove_image:
        row["image_b64"] = None
    q_upsert("questions", [row])
    return row["id"]


def delete_questions(ids: List[str]) -> None:
    if ids:
        q_delete("questions", {"id": ("in", list(ids))})


def question_label(r: Dict[str, Any]) -> str:
    t = clean_str(r.get("question_text")).replace("\n", " ")
    return f"{r['id']} | {r.get('chapter','')} | {r.get('qtype','')} | {r.get('max_marks','')}m | {t[:70]}"


# =============================================================================
# SECTION 9 - SCORING (pure functions, covered by the self-tests)
# =============================================================================
def evaluate_response(q: Dict[str, Any], resp: Any, neg_enabled: bool = False,
                      neg_fraction: float = 0.0) -> Dict[str, Any]:
    """Score one answer.
    status: unattempted | correct | incorrect | pending (needs manual marking)
    Unanswered = 0 marks. Negative marking only if enabled for the test, and only for
    automatically checked questions."""
    max_m = float(q.get("max_marks") or 0)
    r = clean_str(resp)
    if r == "":
        return {"status": "unattempted", "marks": 0.0}
    mode = q.get("eval_mode") or "manual"
    if mode == "manual":
        return {"status": "pending", "marks": None}
    correct = clean_str(q.get("correct_answer"))
    ok: Optional[bool] = None
    if mode == "option":
        ok = (r.upper() == correct.upper()) if correct else None
    elif mode == "numeric":
        b = parse_number(correct)
        if b is not None:
            a = parse_number(r)
            tol = float(q.get("tolerance") or 0.0)
            ok = a is not None and abs(a - b) <= tol + 1e-9
    elif mode == "text":
        accepted = [norm_text(x) for x in correct.split("||") if x.strip()]
        ok = (norm_text(r) in accepted) if accepted else None
    if ok is None:  # answer key unusable -> never guess; send to manual checking
        return {"status": "pending", "marks": None}
    if ok:
        return {"status": "correct", "marks": max_m}
    penalty = -abs(float(neg_fraction or 0.0)) * max_m if neg_enabled else 0.0
    return {"status": "incorrect", "marks": penalty}


def manual_status(marks: float, max_marks: float) -> str:
    if marks >= max_marks - 1e-9:
        return "correct"
    if marks <= 1e-9:
        return "incorrect"
    return "partial"


def compute_totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: dicts with status, marks, max_marks, attempted."""
    t = {"n": len(rows), "max_total": 0.0, "obtained": 0.0, "pending_marks": 0.0, "correct": 0,
         "incorrect": 0, "partial": 0, "unattempted": 0, "pending": 0, "attempted": 0}
    for r in rows:
        mm = float(r.get("max_marks") or 0)
        t["max_total"] += mm
        st_ = r.get("status")
        if st_ in t:
            t[st_] += 1
        if r.get("attempted"):
            t["attempted"] += 1
        if st_ == "pending":
            t["pending_marks"] += mm
        elif r.get("marks") is not None:
            t["obtained"] += float(r["marks"])
    answered_eval = t["correct"] + t["incorrect"] + t["partial"]
    t["accuracy"] = pct(t["correct"], answered_eval)
    t["percentage"] = pct(t["obtained"], t["max_total"])
    t["final"] = t["pending"] == 0
    return t


# =============================================================================
# SECTION 10 - TEST CREATION (selection logic)
# =============================================================================
def filter_pool(df: pd.DataFrame, subjects: Optional[List[str]] = None, chapters: Optional[List[str]] = None,
                qtypes: Optional[List[str]] = None, difficulties: Optional[List[str]] = None,
                exclude_ids: Optional[Iterable[str]] = None) -> pd.DataFrame:
    d = df
    if subjects:
        d = d[d["subject"].isin(subjects)]
    if chapters:
        d = d[d["chapter"].isin(chapters)]
    if qtypes:
        d = d[d["qtype"].isin(qtypes)]
    if difficulties:
        d = d[d["difficulty"].isin(difficulties)]
    if exclude_ids:
        d = d[~d["id"].isin(list(exclude_ids))]
    return d


def pick_questions(pool: pd.DataFrame, count: int = 0, total_marks: float = 0.0,
                   rng: Optional[random.Random] = None) -> List[Dict[str, Any]]:
    """Random selection without repeats. Stops at `count` questions and/or when the marks
    target `total_marks` would be exceeded (0 = ignore that limit). If both are 0, nothing is picked."""
    rng = rng or random.Random()
    recs = pool.to_dict("records")
    rng.shuffle(recs)
    if count <= 0 and total_marks <= 0:
        return []
    chosen, running = [], 0.0
    for r in recs:
        if count > 0 and len(chosen) >= count:
            break
        m = float(r.get("max_marks") or 0)
        if total_marks > 0:
            if running + m > total_marks + 1e-9:
                continue
        chosen.append(r)
        running += m
        if total_marks > 0 and abs(running - total_marks) < 1e-9:
            break
    return chosen


def build_board_paper(pool: pd.DataFrame, blueprint: List[Dict[str, Any]],
                      rng: Optional[random.Random] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    """blueprint rows: {section, qtype, difficulty ('Any'|Easy|Medium|Hard), count, marks_each (0 = use bank marks)}.
    Never repeats a question. Returns (questions_with_section, warnings)."""
    rng = rng or random.Random()
    used: set = set()
    out, warns = [], []
    for row in blueprint:
        sec, qt = clean_str(row.get("section")) or "Section", clean_str(row.get("qtype"))
        cnt = safe_int(row.get("count"), 0) or 0
        if cnt <= 0 or not qt:
            continue
        cand = pool[(pool["qtype"] == qt) & (~pool["id"].isin(used))]
        diff = clean_str(row.get("difficulty"))
        if diff and diff != "Any":
            cand = cand[cand["difficulty"] == diff]
        recs = cand.to_dict("records")
        rng.shuffle(recs)
        take = recs[:cnt]
        if len(take) < cnt:
            warns.append(f"{sec} / {qt}: wanted {cnt} but only {len(take)} matching question(s) available.")
        me = safe_float(row.get("marks_each"), 0.0) or 0.0
        for r in take:
            used.add(r["id"])
            r = dict(r)
            r["section"] = sec
            if me > 0:
                r["max_marks"] = me
            out.append(r)
    return out, warns


# =============================================================================
# SECTION 11 - ATTEMPTS (exam engine, persistence, submission, manual evaluation)
# =============================================================================
SNAP_KEYS = ["subject", "chapter", "topic", "qtype", "difficulty", "question_text", "options", "correct_answer",
             "eval_mode", "tolerance", "explanation", "model_answer", "max_marks", "marking_scheme", "source",
             "pyq_exam", "pyq_year"]


def snapshot_question(r: Dict[str, Any], section: str = "") -> Dict[str, Any]:
    """Frozen copy of a question stored inside the attempt, so later edits/deletes in the bank
    never change an old test or its result. (Images are looked up by question id when shown.)"""
    d: Dict[str, Any] = {}
    for k in SNAP_KEYS:
        v = r.get(k)
        if k == "options":
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    v = []
            d[k] = [clean_str(x) for x in (v if isinstance(v, (list, tuple)) else [])]
        elif k in ("tolerance", "max_marks"):
            d[k] = safe_float(v, 0.0) or 0.0
        elif k == "pyq_year":
            d[k] = safe_int(v)
        else:
            d[k] = clean_str(v)
    d["pyq_exam"] = d["pyq_exam"] or None
    d["qid"] = r.get("qid") or r["id"]
    d["section"] = section or clean_str(r.get("section"))
    return d


def start_attempt(name: str, subject: str, mode: str, questions: List[Dict[str, Any]], duration_min: float,
                  neg_enabled: bool, neg_fraction: float, chapters: Optional[List[str]] = None,
                  config: Optional[Dict[str, Any]] = None) -> str:
    seen, snaps = set(), []
    for r in questions:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        snaps.append(snapshot_question(r))
    if not snaps:
        raise ValueError("A test needs at least one question.")
    start = now_ts()
    aid = new_id("att")
    state = {"responses": {}, "visited": [snaps[0]["qid"]], "marked": [], "current": 0}
    q_upsert("attempts", [{
        "id": aid, "name": name, "subject": subject, "mode": mode, "chapters": chapters or [],
        "status": "in_progress", "duration_min": float(duration_min or 0), "start_ts": start,
        "deadline_ts": start + duration_min * 60 if duration_min and duration_min > 0 else 0.0,
        "submitted_ts": None, "auto_submitted": False, "neg_marking": bool(neg_enabled),
        "neg_fraction": float(neg_fraction or 0), "config": config or {}, "questions": snaps, "state": state,
        "max_total": sum(s["max_marks"] for s in snaps), "obtained": 0.0, "pending_marks": 0.0,
        "created_at": iso_now()}])
    return aid


def get_attempt(aid: str) -> Optional[Dict[str, Any]]:
    rows = q_select("attempts", {"id": aid})
    return rows[0] if rows else None


def load_answers(aid: str) -> List[Dict[str, Any]]:
    return q_select("attempt_answers", {"attempt_id": aid}, order=["position"])


def record_mistake_result(q: Dict[str, Any], ok: bool, my_answer: str) -> None:
    """Keep exactly one notebook row per question; repeated attempts update counters."""
    mid = f"{OWNER}__{q['qid']}"
    rows = q_select("mistakes", {"id": mid})
    now = iso_now()
    ca = q.get("correct_answer") or ""
    if q.get("eval_mode") == "option" and ca.upper() in LETTERS and len(q.get("options") or []) > LETTERS.index(ca.upper()):
        ca = f"{ca.upper()}. {q['options'][LETTERS.index(ca.upper())]}"
    if ok:
        if rows:
            r = rows[0]
            q_update("mistakes", {"reattempt_count": (r["reattempt_count"] or 0) + 1,
                                  "correct_count": (r["correct_count"] or 0) + 1,
                                  "last_result": "correct", "last_date": now}, {"id": mid}, touch=False)
        return
    if rows:
        r = rows[0]
        q_update("mistakes", {"fail_count": (r["fail_count"] or 0) + 1,
                              "reattempt_count": (r["reattempt_count"] or 0) + 1, "my_answer": my_answer,
                              "revised": False, "last_result": "wrong", "last_date": now}, {"id": mid}, touch=False)
    else:
        q_upsert("mistakes", [{
            "id": mid, "question_id": q["qid"], "subject": q.get("subject"), "chapter": q.get("chapter"),
            "question_text": q.get("question_text"), "my_answer": my_answer, "correct_answer": ca or q.get("model_answer") or "",
            "explanation": q.get("explanation") or "", "first_date": now, "last_date": now, "fail_count": 1,
            "reattempt_count": 0, "correct_count": 0, "revised": False, "last_result": "wrong"}], touch=False)


def submit_attempt(aid: str, auto: bool = False) -> Optional[Dict[str, Any]]:
    """Finalise an attempt: evaluate, store every answer, update the mistake notebook. Idempotent."""
    att = get_attempt(aid)
    if not att or att["status"] != "in_progress":
        return att
    state = att.get("state") or {}
    resp = state.get("responses", {})
    dl = att.get("deadline_ts") or 0
    submitted = min(now_ts(), dl) if (auto and dl) else now_ts()
    existing = {r["question_id"]: r for r in load_answers(aid)}
    rows = []
    for pos, q in enumerate(att["questions"], start=1):
        ev = evaluate_response(q, resp.get(q["qid"], ""), att["neg_marking"], att["neg_fraction"])
        prev = existing.get(q["qid"])
        rows.append({
            "id": f"{aid}__{q['qid']}", "attempt_id": aid, "question_id": q["qid"], "position": pos,
            "section": q.get("section") or "", "subject": q["subject"], "chapter": q["chapter"],
            "qtype": q["qtype"], "response": clean_str(resp.get(q["qid"], "")),
            "attempted": clean_str(resp.get(q["qid"], "")) != "", "status": ev["status"], "marks": ev["marks"],
            "max_marks": q["max_marks"], "feedback": "", "evaluated_at": None,
            "mistake_logged": bool(prev and prev.get("mistake_logged"))})
    q_upsert("attempt_answers", rows, touch=False)
    for row, q in zip(rows, att["questions"]):
        if row["status"] in ("incorrect", "unattempted", "correct") and not row["mistake_logged"]:
            record_mistake_result(q, row["status"] == "correct", row["response"])
            row["mistake_logged"] = True
            q_update("attempt_answers", {"mistake_logged": True}, {"id": row["id"]}, touch=False)
    tot = compute_totals(rows)
    q_update("attempts", {
        "status": "completed" if tot["final"] else "pending_eval", "submitted_ts": submitted,
        "auto_submitted": bool(auto), "obtained": tot["obtained"], "pending_marks": tot["pending_marks"],
        "max_total": tot["max_total"]}, {"id": aid})
    return get_attempt(aid)


def save_manual_evaluation(aid: str, qid: str, marks: float, feedback: str) -> Dict[str, Any]:
    rows = q_select("attempt_answers", {"id": f"{aid}__{qid}"})
    if not rows:
        raise ValueError("Answer record not found")
    ans = rows[0]
    mm = float(ans["max_marks"])
    marks = max(0.0, min(float(marks), mm))
    status = manual_status(marks, mm)
    q_update("attempt_answers", {"marks": marks, "status": status, "feedback": clean_str(feedback),
                                 "evaluated_at": iso_now()}, {"id": ans["id"]}, touch=False)
    if not ans.get("mistake_logged"):
        att = get_attempt(aid)
        qsnap = next((x for x in att["questions"] if x["qid"] == qid), None)
        if qsnap:
            record_mistake_result(qsnap, marks >= 0.5 * mm, ans["response"])
        q_update("attempt_answers", {"mistake_logged": True}, {"id": ans["id"]}, touch=False)
    tot = compute_totals(load_answers(aid))
    q_update("attempts", {"status": "completed" if tot["final"] else "pending_eval", "obtained": tot["obtained"],
                          "pending_marks": tot["pending_marks"]}, {"id": aid})
    return tot


def delete_attempt(aid: str) -> None:
    q_delete("attempt_answers", {"attempt_id": aid}, touch=False)
    q_delete("attempts", {"id": aid})


# =============================================================================
# SECTION 12 - DATA FRAMES FOR DASHBOARD / ANALYTICS
# =============================================================================
ATTEMPT_LIST_COLS = ["id", "name", "subject", "mode", "chapters", "status", "duration_min", "start_ts",
                     "deadline_ts", "submitted_ts", "auto_submitted", "neg_marking", "max_total", "obtained",
                     "pending_marks", "created_at"]


def load_attempts_df() -> pd.DataFrame:
    rows = q_select("attempts", cols=ATTEMPT_LIST_COLS, order="-created_at")
    df = pd.DataFrame(rows, columns=ATTEMPT_LIST_COLS)
    if not df.empty:
        df["percentage"] = df.apply(lambda r: pct(r["obtained"] or 0, r["max_total"] or 0), axis=1)
        df["time_taken"] = (df["submitted_ts"].fillna(0) - df["start_ts"].fillna(0)).clip(lower=0)
    else:
        df["percentage"], df["time_taken"] = [], []
    return df


def load_answers_df() -> pd.DataFrame:
    cols = [c for c, _ in SCHEMA["attempt_answers"]["cols"] if c != "owner_id"]
    rows = q_select("attempt_answers", cols=cols, order="id")
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df["marks_pos"] = df["marks"].fillna(0).clip(lower=0)
    else:
        df["marks_pos"] = []
    return df


def load_mistakes_df() -> pd.DataFrame:
    cols = [c for c, _ in SCHEMA["mistakes"]["cols"] if c != "owner_id"]
    return pd.DataFrame(q_select("mistakes", cols=cols, order=["-last_date"]), columns=cols)


def chapter_stats(ans: pd.DataFrame) -> pd.DataFrame:
    cols = ["subject", "chapter", "questions", "attempted", "correct", "accuracy", "marks", "max_marks", "score_pct"]
    if ans is None or ans.empty:
        return pd.DataFrame(columns=cols)
    d = ans[ans["status"] != "pending"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    g = d.groupby(["subject", "chapter"]).agg(
        questions=("id", "count"), attempted=("attempted", "sum"),
        correct=("status", lambda s: int((s == "correct").sum())),
        marks=("marks_pos", "sum"), max_marks=("max_marks", "sum")).reset_index()
    g["accuracy"] = g.apply(lambda r: pct(r["correct"], r["attempted"]), axis=1)
    g["score_pct"] = g.apply(lambda r: pct(r["marks"], r["max_marks"]), axis=1)
    return g[cols]


def ranked_chapters(cs: pd.DataFrame, weakest: bool, n: int = 5) -> pd.DataFrame:
    if cs is None or cs.empty:
        return cs
    d = cs[cs["questions"] >= MIN_Q_FOR_RANK]
    return d.sort_values("score_pct", ascending=weakest).head(n)


def type_stats(ans: pd.DataFrame) -> pd.DataFrame:
    if ans is None or ans.empty:
        return pd.DataFrame(columns=["qtype", "questions", "attempted", "correct", "accuracy"])
    d = ans[ans["status"] != "pending"]
    if d.empty:
        return pd.DataFrame(columns=["qtype", "questions", "attempted", "correct", "accuracy"])
    g = d.groupby("qtype").agg(questions=("id", "count"), attempted=("attempted", "sum"),
                               correct=("status", lambda s: int((s == "correct").sum()))).reset_index()
    g["accuracy"] = g.apply(lambda r: pct(r["correct"], r["attempted"]), axis=1)
    return g.sort_values("accuracy")


# =============================================================================
# SECTION 13 - PRINTABLE REPORT
# =============================================================================
def build_report_html(att: Dict[str, Any], answers: List[Dict[str, Any]]) -> str:
    e = html.escape
    tot = compute_totals(answers)
    qmap = {q["qid"]: q for q in att["questions"]}
    rows = []
    for a in answers:
        q = qmap.get(a["question_id"], {})
        ca = q.get("correct_answer") or ""
        if q.get("eval_mode") == "option" and ca.upper() in LETTERS and len(q.get("options") or []) > LETTERS.index(ca.upper()):
            ca = f"{ca.upper()}. {q['options'][LETTERS.index(ca.upper())]}"
        rows.append(
            f"<div class='q'><b>Q{a['position']}.</b> {e(clean_str(q.get('question_text')))}"
            f"<div class='m'>{e(a['chapter'] or '')} &middot; {e(a['qtype'] or '')} &middot; "
            f"Status: <b>{e(a['status'])}</b> &middot; Marks: {fmt_marks(a['marks'])} / {fmt_marks(a['max_marks'])}</div>"
            f"<div><i>Your answer:</i> {e(a['response'] or '(not attempted)')}</div>"
            f"<div><i>Correct answer:</i> {e(ca)}</div>"
            + (f"<div><i>Model answer:</i> {e(q.get('model_answer') or '')}</div>" if q.get("model_answer") else "")
            + (f"<div><i>Explanation:</i> {e(q.get('explanation') or '')}</div>" if q.get("explanation") else "")
            + (f"<div><i>Feedback:</i> {e(a['feedback'])}</div>" if a.get("feedback") else "")
            + "</div>")
    score = (f"{fmt_marks(tot['obtained'])} / {fmt_marks(tot['max_total'])} ({tot['percentage']:.1f}%)"
             if tot["final"] else
             f"PROVISIONAL {fmt_marks(tot['obtained'])} / {fmt_marks(tot['max_total'] - tot['pending_marks'])} on checked questions; "
             f"{fmt_marks(tot['pending_marks'])} marks pending manual evaluation")
    taken = fmt_clock((att.get("submitted_ts") or 0) - (att.get("start_ts") or 0))
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(att['name'])} - result</title>"
            "<style>body{font-family:Arial,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#111}"
            "h1{margin-bottom:0}.q{border:1px solid #ccc;border-radius:8px;padding:10px;margin:10px 0;page-break-inside:avoid}"
            ".m{color:#555;font-size:13px;margin:4px 0}</style></head><body>"
            f"<h1>{e(APP_NAME)} - Result report</h1><p>{e(TAGLINE)}</p>"
            f"<h2>{e(att['name'])}</h2><p>Subject: {e(att['subject'])} &middot; Date: {fmt_ts(att.get('start_ts'))} "
            f"&middot; Time taken: {taken}</p><p><b>Score: {e(score)}</b></p>"
            f"<p>Correct {tot['correct']} &middot; Incorrect {tot['incorrect']} &middot; Partial {tot['partial']} &middot; "
            f"Unattempted {tot['unattempted']} &middot; Pending {tot['pending']} &middot; Accuracy {tot['accuracy']:.1f}%</p>"
            + "".join(rows) + "<p style='color:#777;font-size:12px'>Open this file in a browser and use Print -> Save as PDF. "
            "Pending marks are not part of the score until you evaluate them.</p></body></html>")


# =============================================================================
# SECTION 14 - BUILT-IN SELF-TESTS  (run with:  python app.py --selftest)
# =============================================================================
def run_selftests() -> List[Tuple[str, bool, str]]:
    """Runs against a throw-away temporary folder; never touches your real data."""
    global DB, OWNER
    results: List[Tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), "" if cond else detail))

    def mcq(correct="B", marks=1.0, **kw):
        d = {"qid": "q1", "eval_mode": "option", "correct_answer": correct, "max_marks": marks, "tolerance": 0}
        d.update(kw)
        return d

    # ---- scoring -----------------------------------------------------------------------------
    check("MCQ correct", evaluate_response(mcq(), "B") == {"status": "correct", "marks": 1.0})
    check("MCQ wrong, no negative marking", evaluate_response(mcq(), "A") == {"status": "incorrect", "marks": 0.0})
    r = evaluate_response(mcq(marks=2), "A", True, 0.25)
    check("MCQ wrong, negative 1/4 of 2 marks", r["marks"] == -0.5, str(r))
    check("Unanswered is always 0 even with negative marking", evaluate_response(mcq(), "  ", True, 1.0) == {"status": "unattempted", "marks": 0.0})
    n = {"eval_mode": "numeric", "correct_answer": "12.5", "tolerance": 0.05, "max_marks": 2}
    check("Numeric inside tolerance", evaluate_response(n, "12.52")["status"] == "correct")
    check("Numeric outside tolerance", evaluate_response(n, "12.6")["status"] == "incorrect")
    check("Numeric fraction input", evaluate_response({"eval_mode": "numeric", "correct_answer": "0.75", "tolerance": 0, "max_marks": 1}, "3/4")["status"] == "correct")
    check("Numeric non-number answer is incorrect", evaluate_response(n, "abc")["status"] == "incorrect")
    t = {"eval_mode": "text", "correct_answer": "Photosynthesis||photo synthesis", "max_marks": 1}
    check("Text alternatives + case/space tolerant", evaluate_response(t, "  PHOTO   synthesis. ")["status"] == "correct")
    check("Manual question is pending, not scored", evaluate_response({"eval_mode": "manual", "max_marks": 5}, "essay") == {"status": "pending", "marks": None})
    check("Unusable answer key goes to manual", evaluate_response({"eval_mode": "numeric", "correct_answer": "x", "max_marks": 1}, "3")["status"] == "pending")
    rows = [
        {"status": "correct", "marks": 1.0, "max_marks": 1, "attempted": True},
        {"status": "incorrect", "marks": -0.25, "max_marks": 1, "attempted": True},
        {"status": "unattempted", "marks": 0.0, "max_marks": 1, "attempted": False},
        {"status": "pending", "marks": None, "max_marks": 5, "attempted": True},
        {"status": "partial", "marks": 1.5, "max_marks": 3, "attempted": True},
    ]
    tt = compute_totals(rows)
    check("Totals: obtained", abs(tt["obtained"] - 2.25) < 1e-9, str(tt))
    check("Totals: max / pending / final flag", tt["max_total"] == 11 and tt["pending_marks"] == 5 and tt["final"] is False, str(tt))
    check("Totals: accuracy = correct / answered-and-evaluated", abs(tt["accuracy"] - 100 / 3) < 1e-6, str(tt))
    check("Manual partial status", manual_status(1.5, 3) == "partial" and manual_status(3, 3) == "correct" and manual_status(0, 3) == "incorrect")

    # ---- validation / import -----------------------------------------------------------------
    chmap = {s: {c.lower(): c for c in ch} for s, ch in STARTER_CHAPTERS.items()}
    good = {"subject": "maths", "chapter": "real numbers", "qtype": "mcq", "difficulty": "easy", "question_text": "Q?",
            "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4", "correct_answer": "b", "max_marks": "1"}
    row, errs = validate_question(good, chmap)
    check("Valid MCQ accepted + normalised", not errs and row["subject"] == "Mathematics Standard" and row["correct_answer"] == "B" and row["eval_mode"] == "option", str(errs))
    _, errs = validate_question(dict(good, correct_answer="E"), chmap)
    check("Bad option letter rejected", bool(errs))
    _, errs = validate_question(dict(good, chapter="Nonexistent"), chmap)
    check("Unknown chapter rejected", bool(errs))
    _, errs = validate_question(dict(good, pyq_exam="CBSE Board", pyq_year="2023"), chmap)
    check("PYQ without source rejected", any("source" in e for e in errs), str(errs))
    _, errs = validate_question(dict(good, pyq_year="2023"), chmap)
    check("PYQ year without exam rejected", bool(errs))
    row, errs = validate_question(dict(good, qtype="Assertion and Reason", option_a="", option_b="", option_c="", option_d=""), chmap)
    check("Assertion-Reason options auto-filled", not errs and row["options"] == AR_OPTIONS, str(errs))
    row, errs = validate_question({"subject": "Science", "chapter": "Physics", "qtype": "Numerical", "difficulty": "Hard",
                                   "question_text": "n", "correct_answer": "9.8", "tolerance": "0.1", "max_marks": "2"}, chmap)
    check("Numerical defaults to numeric mode", not errs and row["eval_mode"] == "numeric" and row["tolerance"] == 0.1, str(errs))
    _, errs = validate_question(dict(good, max_marks="0"), chmap)
    check("Zero marks rejected", bool(errs))
    items = prepare_import([good, dict(good), dict(good, question_text="")], chmap, set())
    check("Import flags in-file duplicate and invalid rows", [i["status"] for i in items] == ["new", "invalid", "invalid"], str([i["status"] for i in items]))
    items = prepare_import([good], chmap, {auto_question_id(validate_question(good, chmap)[0])})
    check("Import flags ID already in database", items[0]["status"] == "exists")
    blocks = parse_bulk_text("Q: First?\nA: x\nB: y\nAns: A\n---\nQ: Second\nmore text\nAns: B", {"subject": "Science"})
    check("Bulk paste parser", len(blocks) == 2 and blocks[1]["question_text"] == "Second\nmore text" and blocks[0]["correct_answer"] == "A", str(blocks))
    rows_csv, fatal, _ = parse_csv_bytes(question_template_csv().encode())
    check("Template CSV parses and has required columns", len(rows_csv) == 2 and not fatal)
    _, fatal, _ = parse_csv_bytes(b"subject,chapter\nScience,Physics\n")
    check("CSV with missing columns reported", bool(fatal))

    # ---- selection ---------------------------------------------------------------------------
    pool = pd.DataFrame([{"id": f"id{i}", "qtype": "MCQ" if i < 6 else "Short Answer", "difficulty": "Easy", "max_marks": 1.0 if i < 6 else 3.0,
                          "subject": "Science", "chapter": "Physics"} for i in range(10)])
    pk = pick_questions(pool, count=5, rng=random.Random(1))
    check("Random pick: right size, no repeats", len(pk) == 5 and len({p["id"] for p in pk}) == 5)
    pk = pick_questions(pool, count=0, total_marks=7, rng=random.Random(2))
    check("Marks target never exceeded", sum(p["max_marks"] for p in pk) <= 7 and len(pk) > 0)
    paper, warns = build_board_paper(pool, [{"section": "A", "qtype": "MCQ", "count": 4, "marks_each": 1, "difficulty": "Any"},
                                            {"section": "B", "qtype": "Short Answer", "count": 9, "marks_each": 2, "difficulty": "Any"}], random.Random(3))
    check("Board paper: sections, marks override, shortage warning, no repeats",
          len(paper) == 8 and len({p["id"] for p in paper}) == 8 and len(warns) == 1 and all(p["max_marks"] == 2 for p in paper if p["section"] == "B"), str(warns))

    # ---- auth --------------------------------------------------------------------------------
    os.environ["APP_PASSWORD"] = "selftest-password"
    try:
        tok = make_session_token("owner", 60)
        check("Session token valid, tamper/expiry rejected", check_session_token(tok, "owner") and not check_session_token(tok + "x", "owner")
              and not check_session_token(tok, "other") and not check_session_token(make_session_token("owner", 60, now=time.time() - 3600), "owner"))
    finally:
        os.environ.pop("APP_PASSWORD", None)

    # ---- data round trip (temporary JSON folder) ---------------------------------------------------
    old = (DB, OWNER)
    tmpdir = tempfile.mkdtemp()
    try:
        DB, OWNER = JsonStore(os.path.join(tmpdir, "data")), "selftest"
        check("Seed starter chapters", seed_starter_chapters() > 0 and seed_starter_chapters() == 0)
        try:
            add_chapter("Science", "Physics")
            check("Duplicate chapter rejected", False)
        except ValueError:
            check("Duplicate chapter rejected", True)
        cm = chapter_map(load_chapters_df())
        raws = [dict(good, question_id="T1", question_text="Q one"),
                dict(good, question_id="T2", question_text="Q two", correct_answer="A"),
                {"question_id": "T3", "subject": "Mathematics Standard", "chapter": "Real Numbers", "qtype": "Long Answer", "difficulty": "Hard",
                 "question_text": "Prove something", "model_answer": "Proof", "max_marks": "5", "marking_scheme": "5 steps"},
                {"question_id": "T4", "subject": "Mathematics Standard", "chapter": "Statistics", "qtype": "Numerical", "difficulty": "Medium",
                 "question_text": "Mean?", "correct_answer": "10", "tolerance": "0.5", "max_marks": "2"}]
        items = prepare_import(raws, cm, load_question_ids())
        res = import_questions(items, overwrite_existing=False)
        check("Import writes all valid rows", res["added"] == 4, str(res))
        res = import_questions(prepare_import(raws, cm, load_question_ids()), overwrite_existing=False)
        check("Re-import skips duplicates (no silent duplication)", res["added"] == 0 and res["skipped"] == 4, str(res))
        qdf = load_questions_df()
        check("Question bank loads", len(qdf) == 4)
        pool = filter_pool(qdf, subjects=["Mathematics Standard"], chapters=["Real Numbers"], qtypes=["MCQ"])
        check("Filters work", len(pool) == 2)
        picked = pick_questions(qdf, count=10, rng=random.Random(5))
        aid = start_attempt("Self-test", "Mathematics Standard", "custom", picked, 30, True, 0.25)
        att = get_attempt(aid)
        check("Attempt stored with deadline", att["status"] == "in_progress" and abs(att["deadline_ts"] - att["start_ts"] - 1800) < 1e-6)
        ids = {q["qid"]: q for q in att["questions"]}
        st_ = att["state"]
        st_["responses"] = {"T1": "B", "T2": "B", "T3": "my proof", "T4": "10.2"}   # T1 right, T2 wrong
        q_update("attempts", {"state": st_}, {"id": aid})
        check("State survives reload (refresh safety)", get_attempt(aid)["state"]["responses"]["T3"] == "my proof")
        fin = submit_attempt(aid)
        check("Submit -> pending_eval, objective score correct", fin["status"] == "pending_eval" and abs(fin["obtained"] - (1 - 0.25 + 2)) < 1e-9 and fin["pending_marks"] == 5, str(fin["obtained"]))
        check("Submit twice is harmless", submit_attempt(aid)["obtained"] == fin["obtained"])
        mk = load_mistakes_df()
        check("Wrong answer saved once to notebook", list(mk["question_id"]) == ["T2"] and mk.iloc[0]["fail_count"] == 1)
        tot = save_manual_evaluation(aid, "T3", 3.5, "Good but incomplete")
        att2 = get_attempt(aid)
        check("Manual partial marks update final total", att2["status"] == "completed" and abs(att2["obtained"] - 6.25) < 1e-9 and tot["final"], str(att2["obtained"]))
        aid2 = start_attempt("Re-attempt", "Mathematics Standard", "mistakes", [dict(q, id=q["qid"]) for q in [ids["T2"]]], 0, False, 0)
        s2 = get_attempt(aid2)["state"]
        s2["responses"] = {"T2": "C"}
        q_update("attempts", {"state": s2}, {"id": aid2})
        submit_attempt(aid2)
        mk = load_mistakes_df()
        check("Repeated mistake updates counters (no duplicate row)", len(mk) == 1 and mk.iloc[0]["fail_count"] == 2 and mk.iloc[0]["reattempt_count"] == 1, str(mk.to_dict("records")))
        aid3 = start_attempt("Third", "Mathematics Standard", "mistakes", [dict(ids["T2"], id="T2")], 0, False, 0)
        s3 = get_attempt(aid3)["state"]
        s3["responses"] = {"T2": "A"}
        q_update("attempts", {"state": s3}, {"id": aid3})
        submit_attempt(aid3)
        mk = load_mistakes_df()
        check("Correct reattempt recorded as improvement", mk.iloc[0]["last_result"] == "correct" and mk.iloc[0]["correct_count"] == 1)
        cs = chapter_stats(load_answers_df())
        check("Chapter analytics built from real answers", not cs.empty and cs["questions"].sum() >= 4)
        check("Report HTML builds", "Self-test" in build_report_html(get_attempt(aid), load_answers(aid)))
        n_renamed = rename_chapter(next(r["id"] for r in q_select("chapters") if r["name"] == "Real Numbers"), "Real Numbers (edited)")
        check("Chapter rename updates its questions", n_renamed == 3 and len(load_questions_df().query("chapter == 'Real Numbers (edited)'")) == 3, str(n_renamed))
        delete_attempt(aid)
        check("Attempt delete cascades to answers", get_attempt(aid) is None and load_answers(aid) == [])
        save_question(validate_question(dict(good, question_id="T9", question_text="Nine"), {"Mathematics Standard": {"real numbers (edited)": "Real Numbers (edited)"}}, True)[0], True)
        delete_questions(["T9"])
        check("Question add + delete", get_question("T9") is None)
        # ---- JSON store specifics: persistence, recovery, backup/restore -----------------------
        reopened = JsonStore(os.path.join(tmpdir, "data"))
        check("Data persists on disk (re-opening the store finds it)", len(reopened.select("questions", {"owner_id": "selftest"})) == 4 and len(reopened.select("attempts", {"owner_id": "selftest"})) == 2)
        qf = os.path.join(tmpdir, "data", "questions.json")
        shutil.copyfile(qf, qf + ".bak")
        with open(qf, "w") as fh:
            fh.write("{ broken json")
        rec = JsonStore(os.path.join(tmpdir, "data"))
        check("Damaged file is recovered from its .bak copy", len(rec.select("questions", {"owner_id": "selftest"})) == 4 and bool(rec.notes), str(rec.notes))
        blob = build_backup(DB, "selftest")
        other = JsonStore(os.path.join(tmpdir, "data2"))
        counts = restore_into_store(other, blob, "selftest")
        check("Backup -> restore round trip keeps every row", counts["questions"] == 4 and len(other.select("questions", {"owner_id": "selftest"})) == 4
              and len(other.select("attempt_answers", {"owner_id": "selftest"})) == len(DB.select("attempt_answers", {"owner_id": "selftest"})))
        try:
            restore_into_store(other, "[1,2]", "selftest")
            check("Bad backup file rejected", False)
        except ValueError:
            check("Bad backup file rejected", True)
    except Exception as e:  # report instead of crashing
        check("Database round trip", False, repr(e))
    finally:
        DB, OWNER = old
    return results


# =============================================================================
# SECTION 15 - STREAMLIT HELPERS (decorators, CSS, cached loaders, shared widgets)
# =============================================================================
def cached(ttl: int = 300):
    def deco(fn):
        return st.cache_data(ttl=ttl, show_spinner=False)(fn) if st is not None else fn
    return deco


def ui_fragment(**kw):
    def deco(fn):
        return st.fragment(**kw)(fn) if st is not None else fn
    return deco


def ui_dialog(title: str):
    def deco(fn):
        return st.dialog(title)(fn) if st is not None else fn
    return deco


def wide(fn, *args, **kw):
    """Full-width rendering that works on old and new Streamlit versions."""
    try:
        return fn(*args, width="stretch", **kw)
    except Exception:
        return fn(*args, use_container_width=True, **kw)


@cached(ttl=600)
def cached_questions() -> pd.DataFrame:
    return load_questions_df()


@cached(ttl=600)
def cached_chapters() -> pd.DataFrame:
    return load_chapters_df()


@cached(ttl=600)
def cached_attempts() -> pd.DataFrame:
    return load_attempts_df()


@cached(ttl=600)
def cached_answers() -> pd.DataFrame:
    return load_answers_df()


@cached(ttl=600)
def cached_mistakes() -> pd.DataFrame:
    return load_mistakes_df()


@cached(ttl=3600)
def cached_image(qid: str) -> Optional[bytes]:
    return get_image_bytes(qid)


@cached(ttl=600)
def cached_hindi_course() -> str:
    return get_setting("hindi_course", "")


APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root{--bg:#070b1a;--card:#0f1731;--card2:#141f44;--line:#243064;--text:#e8ecff;--muted:#9aa6d6;
--purple:#8b5cf6;--blue:#38bdf8;--green:#34d399;--red:#fb7185;--amber:#fbbf24;}
html,body,.stApp,[class*="css"]{font-family:'Inter',system-ui,-apple-system,Segoe UI,sans-serif;}
.stApp{background:radial-gradient(1100px 560px at 8% -10%,rgba(139,92,246,.20),transparent 60%),
radial-gradient(900px 480px at 100% 0%,rgba(56,189,248,.15),transparent 55%),var(--bg);color:var(--text);}
[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:1.6rem;max-width:1280px;}
h1,h2,h3,h4,h5,h6,.stMarkdown,label,p,li,span{color:var(--text);}
.stCaption,[data-testid="stCaptionContainer"]{color:var(--muted)!important;}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#0b1128,#050814);border-right:1px solid var(--line);}
[data-testid="stSidebar"] .stButton>button{width:100%;justify-content:flex-start;text-align:left;border-radius:10px;
background:transparent;border:1px solid transparent;color:var(--text);transition:all .18s ease;padding:.45rem .7rem;}
[data-testid="stSidebar"] .stButton>button:hover{background:rgba(139,92,246,.14);border-color:var(--line);transform:translateX(2px);}
[data-testid="stSidebar"] .stButton>button[kind="primary"]{background:linear-gradient(90deg,rgba(139,92,246,.35),rgba(56,189,248,.22));
border-color:rgba(139,92,246,.6);}
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{border-radius:10px;border:1px solid var(--line);
background:var(--card2);color:var(--text);transition:all .18s ease;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--purple);transform:translateY(-1px);
box-shadow:0 6px 18px rgba(139,92,246,.25);}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"],.stFormSubmitButton>button{
background:linear-gradient(90deg,#7c3aed,#2563eb);border:none;color:#fff;font-weight:600;}
.stTextInput input,.stTextArea textarea,.stNumberInput input,[data-baseweb="select"]>div,[data-baseweb="input"]>div{
background:#0c1433!important;color:var(--text)!important;border-color:var(--line)!important;border-radius:10px!important;}
[data-testid="stExpander"],[data-testid="stForm"]{background:var(--card);border:1px solid var(--line);border-radius:14px;}
[data-baseweb="tab-list"]{gap:6px;}
[data-baseweb="tab"]{background:var(--card);border-radius:10px 10px 0 0;padding:8px 16px;}
[data-baseweb="tab"][aria-selected="true"]{background:var(--card2);border-bottom:2px solid var(--purple);}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:12px;overflow:hidden;}
.hero{background:linear-gradient(120deg,rgba(124,58,237,.38),rgba(37,99,235,.28));border:1px solid rgba(139,92,246,.45);
border-radius:20px;padding:22px 26px;margin-bottom:18px;}
.hero h1{margin:0;font-size:2.1rem;font-weight:800;letter-spacing:-.5px;
background:linear-gradient(90deg,#c4b5fd,#7dd3fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.hero p{margin:.25rem 0 0 0;color:var(--muted);}
.brand{font-weight:800;font-size:1.5rem;letter-spacing:-.4px;background:linear-gradient(90deg,#c4b5fd,#7dd3fc);
-webkit-background-clip:text;-webkit-text-fill-color:transparent;}
.kpi{background:linear-gradient(160deg,var(--card),var(--card2));border:1px solid var(--line);border-radius:16px;
padding:14px 16px;margin-bottom:10px;transition:all .2s ease;}
.kpi:hover{transform:translateY(-2px);border-color:var(--purple);box-shadow:0 10px 24px rgba(0,0,0,.35);}
.kpi-l{color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;}
.kpi-v{font-size:1.65rem;font-weight:800;margin-top:2px;}
.kpi-s{color:var(--muted);font-size:.78rem;}
.chip{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.74rem;margin:0 6px 6px 0;
background:rgba(139,92,246,.18);border:1px solid rgba(139,92,246,.45);color:#d9ccff;}
.chip.blue{background:rgba(56,189,248,.14);border-color:rgba(56,189,248,.45);color:#bfe8ff;}
.chip.green{background:rgba(52,211,153,.14);border-color:rgba(52,211,153,.45);color:#c5f7e3;}
.chip.amber{background:rgba(251,191,36,.14);border-color:rgba(251,191,36,.5);color:#ffe9a8;}
.chip.red{background:rgba(251,113,133,.14);border-color:rgba(251,113,133,.5);color:#ffd0d8;}
.timer{font-size:2rem;font-weight:800;text-align:center;border-radius:14px;padding:6px 10px;
background:linear-gradient(160deg,var(--card),var(--card2));border:1px solid var(--line);}
.timer.low{color:var(--red);border-color:var(--red);}
.qbox{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin-bottom:10px;}
@media (max-width:760px){.block-container{padding:1rem .7rem;}.hero h1{font-size:1.5rem;}.kpi-v{font-size:1.3rem;}}
</style>
"""


def inject_css() -> None:
    st.markdown(APP_CSS, unsafe_allow_html=True)


def kpi(label: str, value: str, sub: str = "") -> str:
    e = html.escape
    return (f"<div class='kpi'><div class='kpi-l'>{e(label)}</div><div class='kpi-v'>{e(str(value))}</div>"
            f"<div class='kpi-s'>{e(sub) if sub else '&nbsp;'}</div></div>")


def chip(text: str, color: str = "") -> str:
    return f"<span class='chip {color}'>{html.escape(str(text))}</span>"


def page_title(title: str, subtitle: str = "") -> None:
    st.markdown(f"## {title}")
    if subtitle:
        st.caption(subtitle)


def go(page: str) -> None:
    st.session_state["page"] = page


def md_text(s: Any) -> str:
    return clean_str(s).replace("\n", "  \n")


def correct_answer_text(q: Dict[str, Any]) -> str:
    ca = clean_str(q.get("correct_answer"))
    mode = q.get("eval_mode")
    opts = q.get("options") or []
    if mode == "option" and ca.upper() in LETTERS and len(opts) > LETTERS.index(ca.upper()):
        return f"{ca.upper()}. {opts[LETTERS.index(ca.upper())]}"
    if mode == "numeric":
        tol = float(q.get("tolerance") or 0)
        return f"{ca}" + (f"  (accepted within +/-{tol:g})" if tol else "")
    if mode == "text":
        return " / ".join(x.strip() for x in ca.split("||") if x.strip())
    return ca


def render_question(q: Dict[str, Any], show_answer: bool = True, number: Optional[int] = None,
                    show_options: bool = True) -> None:
    qid = q.get("qid") or q.get("id")
    chips = [chip(q.get("subject", ""), "blue"), chip(q.get("chapter", "")), chip(q.get("qtype", "")),
             chip(q.get("difficulty", ""), {"Easy": "green", "Medium": "amber", "Hard": "red"}.get(q.get("difficulty"), "")),
             chip(f"{fmt_marks(q.get('max_marks'))} marks", "blue")]
    if q.get("section"):
        chips.insert(0, chip(q["section"], "amber"))
    if is_verified_pyq(q):
        chips.append(chip(f"Verified PYQ: {q['pyq_exam']} {q['pyq_year']}", "green"))
    elif q.get("qtype") == "Previous-Year Question":
        chips.append(chip("PYQ - source/year not verified", "red"))
    st.markdown("".join(chips), unsafe_allow_html=True)
    prefix = f"**Q{number}.** " if number else ""
    st.markdown(prefix + md_text(q.get("question_text")))
    img = cached_image(qid) if qid else None
    if img:
        st.image(img)
    if show_options and q.get("eval_mode") == "option":
        for i, o in enumerate(q.get("options") or []):
            st.markdown(f"**{LETTERS[i]}.** {md_text(o)}")
    if show_answer:
        st.success(f"Correct answer: {correct_answer_text(q)}" if clean_str(q.get("correct_answer")) else "No fixed answer key (manual marking).")
        for label, key in (("Explanation", "explanation"), ("Model answer", "model_answer"),
                           ("Marking scheme", "marking_scheme"), ("Source", "source")):
            if clean_str(q.get(key)):
                st.markdown(f"**{label}:** {md_text(q[key])}")


# =============================================================================
# SECTION 16 - OPTIONAL PASSWORD (no setup needed)
# =============================================================================
# By default the app opens directly. To lock it, add ONE line in Streamlit Cloud ->
# App settings -> Secrets:   APP_PASSWORD = "your password"   (no hashing, no terminal).
# You can also make the app private in Streamlit Cloud (Share -> only specific people).
def login_screen(expected: str) -> None:
    _, mid, _ = st.columns([1, 1.3, 1])
    with mid:
        st.markdown(f"<div class='hero' style='text-align:center'><h1>{APP_NAME}</h1><p>{TAGLINE}</p></div>", unsafe_allow_html=True)
        lock = st.session_state.get("lock_until", 0)
        if time.time() < lock:
            st.error(f"Too many failed attempts. Try again in {int(lock - time.time())} seconds.")
            return
        with st.form("login"):
            pw = st.text_input("Password", type="password")
            ok = st.form_submit_button("Open", type="primary")
        if ok:
            if hmac.compare_digest(pw.encode(), expected.encode()):
                st.session_state["authed"] = True
                st.session_state["fails"] = 0
                st.query_params["s"] = make_session_token("owner")
                st.rerun()
            else:
                st.session_state["fails"] = st.session_state.get("fails", 0) + 1
                time.sleep(min(2 * st.session_state["fails"], 6))
                if st.session_state["fails"] >= 5:
                    st.session_state["lock_until"] = time.time() + 120
                    st.session_state["fails"] = 0
                st.error("Incorrect password.")


def password_enabled() -> bool:
    return bool(get_secret("APP_PASSWORD"))


def auth_gate() -> bool:
    if st.session_state.get("authed"):
        return True
    if not password_enabled():
        st.session_state["authed"] = True      # open access (default)
        return True
    tok = st.query_params.get("s")
    if tok and check_session_token(str(tok), "owner"):
        st.session_state["authed"] = True
        return True
    login_screen(str(get_secret("APP_PASSWORD")))
    return False


def logout() -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.query_params.clear()


# =============================================================================
# SECTION 17 - SIDEBAR / NAVIGATION
# =============================================================================
NAV = [
    ("Dashboard", ":material/dashboard:"), ("Start a Test", ":material/play_circle:"),
    ("Board Exam Mode", ":material/school:"), ("Results", ":material/assignment:"),
    ("Evaluate Answers", ":material/rate_review:"), ("Question Bank", ":material/library_books:"),
    ("Previous-Year Questions", ":material/history_edu:"), ("Subjects & Chapters", ":material/menu_book:"),
    ("Mistake Notebook", ":material/error_outline:"), ("Analytics", ":material/insights:"),
    ("Settings & Backup", ":material/settings:"),
]


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(f"<div class='brand'>{APP_NAME}</div>", unsafe_allow_html=True)
        st.caption(TAGLINE)
        cur = st.session_state.get("page", "Dashboard")
        pend = 0
        try:
            a = cached_attempts()
            pend = int((a["status"] == "pending_eval").sum()) if not a.empty else 0
        except StoreError:
            pass
        for label, icon in NAV:
            shown = f"{label} ({pend})" if label == "Evaluate Answers" and pend else label
            st.button(shown, icon=icon, key=f"nav_{label}", type="primary" if cur == label else "secondary",
                      on_click=go, args=(label,))
        st.divider()
        st.warning("Data is stored in local JSON files. Streamlit Cloud may erase them on restart or redeploy - download a backup in Settings & Backup.")
        if not password_enabled():
            st.caption("Open access: anyone with the link can use this app. See Settings & Backup to lock it.")
        if password_enabled():
            st.button("Sign out", icon=":material/logout:", on_click=logout, key="signout")


# =============================================================================
# SECTION 18 - DASHBOARD
# =============================================================================
def begin_exam(aid: str) -> None:
    st.session_state["active_attempt"] = aid
    st.session_state.pop("exam", None)


def stale_chapters(ans: pd.DataFrame, att: pd.DataFrame, days: int = 14) -> pd.DataFrame:
    if ans.empty or att.empty:
        return pd.DataFrame(columns=["subject", "chapter", "days_since"])
    m = ans[["attempt_id", "subject", "chapter"]].merge(att[["id", "submitted_ts"]], left_on="attempt_id", right_on="id")
    m = m.dropna(subset=["submitted_ts"])
    if m.empty:
        return pd.DataFrame(columns=["subject", "chapter", "days_since"])
    last = m.groupby(["subject", "chapter"])["submitted_ts"].max().reset_index()
    last["days_since"] = ((time.time() - last["submitted_ts"]) / 86400).astype(int)
    return last[last["days_since"] >= days].sort_values("days_since", ascending=False)[["subject", "chapter", "days_since"]]


def score_text(r: pd.Series) -> str:
    if r["status"] == "pending_eval":
        return f"{fmt_marks(r['obtained'])} provisional (+{fmt_marks(r['pending_marks'])} pending)"
    return f"{fmt_marks(r['obtained'])} / {fmt_marks(r['max_total'])}"


def page_dashboard() -> None:
    st.markdown(f"<div class='hero'><h1>{APP_NAME}</h1><p>{TAGLINE}</p></div>", unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1])
    c1.markdown(f"### Welcome back\n{datetime.now(IST).strftime('%A, %d %B %Y')} - ready for your next paper?")
    c2.button("Start New Test", type="primary", icon=":material/play_arrow:", on_click=go, args=("Start a Test",), key="dash_start")

    chdf, att, ans, mk = cached_chapters(), cached_attempts(), cached_answers(), cached_mistakes()
    if not cached_questions().empty:
        st.caption("Tip: your data lives in local files that Streamlit Cloud may erase on restart - download a backup from Settings & Backup.")
    if chdf.empty:
        st.info("No chapters yet. Load the starter list (from the units you supplied) or add your own - then check them against the official CBSE syllabus for your session.")
        if st.button("Load starter chapters", key="seed_dash"):
            n = seed_starter_chapters()
            st.success(f"Added {n} chapters. Edit them in Subjects & Chapters.")
            st.rerun()

    live = att[att["status"] == "in_progress"] if not att.empty else att
    for _, r in live.iterrows():
        left = (r["deadline_ts"] - time.time()) if r["deadline_ts"] else None
        with st.container(border=True):
            a, b = st.columns([4, 1])
            a.markdown(f"**Unfinished test:** {r['name']} ({r['subject']})  \n" +
                       (f"Time left: **{fmt_clock(left)}**" if left and left > 0 else "Untimed" if not r["deadline_ts"] else "Time is up - it will be submitted when you open it."))
            b.button("Resume", key=f"resume_{r['id']}", type="primary", on_click=resume_exam, args=(r["id"],))

    done = att[att["status"].isin(["completed", "pending_eval"])] if not att.empty else att
    finals = att[att["status"] == "completed"] if not att.empty else att
    pending_n = int((att["status"] == "pending_eval").sum()) if not att.empty else 0
    attempted_q = int(ans["attempted"].sum()) if not ans.empty else 0
    ev = ans[ans["status"].isin(["correct", "incorrect", "partial"])] if not ans.empty else ans
    acc = pct(int((ev["status"] == "correct").sum()), len(ev)) if not ev.empty else 0.0
    best = finals.sort_values("percentage", ascending=False).iloc[0] if not finals.empty else None
    cols = st.columns(5)
    cols[0].markdown(kpi("Tests attempted", len(done), "submitted tests"), unsafe_allow_html=True)
    cols[1].markdown(kpi("Average", f"{finals['percentage'].mean():.1f}%" if not finals.empty else "-", "fully evaluated tests"), unsafe_allow_html=True)
    cols[2].markdown(kpi("Highest marks", f"{fmt_marks(best['obtained'])}/{fmt_marks(best['max_total'])}" if best is not None else "-",
                         f"{best['percentage']:.1f}%" if best is not None else ""), unsafe_allow_html=True)
    cols[3].markdown(kpi("Questions attempted", attempted_q, "answered so far"), unsafe_allow_html=True)
    cols[4].markdown(kpi("Overall accuracy", f"{acc:.1f}%" if not ev.empty else "-", "correct / answered & checked"), unsafe_allow_html=True)

    p1, p2 = st.columns(2)
    with p1:
        with st.container(border=True):
            st.markdown(f"**Pending manual evaluations: {pending_n}**")
            st.caption("Written answers are NOT scored until you evaluate them.")
            if pending_n:
                st.button("Evaluate now", on_click=go, args=("Evaluate Answers",), key="dash_eval")
    with p2:
        with st.container(border=True):
            un = int((~mk["revised"]).sum()) if not mk.empty else 0
            st.markdown(f"**Mistakes waiting for revision: {un}**")
            st.caption("Saved automatically from incorrect and unanswered questions.")
            if un:
                st.button("Open Mistake Notebook", on_click=go, args=("Mistake Notebook",), key="dash_mist")

    st.markdown("#### Subject-wise performance")
    if finals.empty:
        st.caption("No fully evaluated tests yet - this chart fills in from your real results.")
    else:
        sp = finals.groupby("subject")["percentage"].mean().reindex(SUBJECTS).fillna(0).reset_index()
        sp.columns = ["Subject", "Average %"]
        st.bar_chart(sp.set_index("Subject"), color="#8b5cf6")

    st.markdown("#### Recent examination results")
    if done.empty:
        st.caption("Nothing yet. Start a test to see results here.")
    else:
        rec = done.head(8).copy()
        rec["Score"] = rec.apply(score_text, axis=1)
        rec["Date"] = rec["start_ts"].map(fmt_ts)
        rec["Result"] = rec.apply(lambda r: "Pending evaluation" if r["status"] == "pending_eval" else f"{r['percentage']:.1f}%", axis=1)
        wide(st.dataframe, rec[["name", "subject", "Date", "Score", "Result"]].rename(columns={"name": "Test", "subject": "Subject"}), hide_index=True)

    cs = chapter_stats(ans)
    w, s = st.columns(2)
    with w:
        st.markdown("#### Weakest chapters")
        wk = ranked_chapters(cs, True)
        if wk is None or wk.empty:
            st.caption(f"Needs at least {MIN_Q_FOR_RANK} checked questions in a chapter before it is ranked.")
        else:
            wide(st.dataframe, wk[["subject", "chapter", "score_pct", "questions"]].rename(columns={"score_pct": "score %"}).round(1), hide_index=True)
    with s:
        st.markdown("#### Strongest chapters")
        sk = ranked_chapters(cs, False)
        if sk is None or sk.empty:
            st.caption("Not enough data yet.")
        else:
            wide(st.dataframe, sk[["subject", "chapter", "score_pct", "questions"]].rename(columns={"score_pct": "score %"}).round(1), hide_index=True)

    st.markdown("#### Revision reminders")
    rem = []
    if not mk.empty:
        for _, r in mk[~mk["revised"]].sort_values("fail_count", ascending=False).head(5).iterrows():
            rem.append(f"Revise **{r['chapter']}** ({r['subject']}) - missed {r['fail_count']}x: {clean_str(r['question_text'])[:80]}")
    for _, r in stale_chapters(ans, att).head(5).iterrows():
        rem.append(f"**{r['chapter']}** ({r['subject']}) not practised for {r['days_since']} days")
    if rem:
        for line in rem:
            st.markdown("- " + line)
    else:
        st.caption("No reminders - they are generated from your real mistakes and practice dates.")


# =============================================================================
# SECTION 19 - TEST BUILDER
# =============================================================================
PRESET_DEFAULTS = {  # (question count, minutes) - only starting suggestions, all editable
    "Quick Quiz (10 questions)": (10, 15), "Chapter Test": (15, 30), "Multiple Chapter Test": (25, 45),
    "Subject Test": (30, 60), "Full Syllabus Test": (50, 120), "Previous-Year Paper Practice": (20, 60),
    "Weak Chapter Test": (15, 30), "Mistake Reattempt Test": (10, 20),
}


def verified_pyq_pool(qdf: pd.DataFrame) -> pd.DataFrame:
    if qdf.empty:
        return qdf
    ok = qdf["pyq_year"].notna() & (qdf["pyq_exam"].fillna("") != "") & (qdf["source"].fillna("") != "")
    return qdf[ok]


def neg_marking_widgets(prefix: str) -> Tuple[bool, float]:
    c1, c2 = st.columns(2)
    neg = c1.checkbox("Negative marking (optional)", value=False, key=f"{prefix}_neg",
                      help="Off by default - CBSE objective questions do not all carry negative marking. Applies to auto-checked questions only; unanswered = 0.")
    frac = 0.0
    if neg:
        frac = c2.selectbox("Penalty for a wrong answer (fraction of the question's marks)", [0.25, 1 / 3, 0.5, 1.0],
                            format_func=lambda x: {0.25: "1/4", 0.5: "1/2", 1.0: "full marks"}.get(x, "1/3"), key=f"{prefix}_negf")
    return neg, float(frac)


def page_builder() -> None:
    page_title("Start a Test", "Build a custom test from your own question bank.")
    qdf, hindi = cached_questions(), cached_hindi_course()
    if qdf.empty:
        st.info("Your question bank is empty. Add or import questions first.")
        st.button("Go to Question Bank", on_click=go, args=("Question Bank",), key="b_goqb")
        return
    preset = st.selectbox("Test type", PRESETS + ["Board Pattern Mock Examination"], key="b_preset")
    if preset.startswith("Board Pattern"):
        st.info("Board Pattern Mock Examinations have their own page where you configure sections and marks.")
        st.button("Open Board Exam Mode", on_click=go, args=("Board Exam Mode",), key="b_goboard", type="primary")
        return
    count_def, min_def = PRESET_DEFAULTS[preset]
    avail = [s for s in SUBJECTS if s in set(qdf["subject"])]
    multi_subject = preset in ("Full Syllabus Test", "Previous-Year Paper Practice", "Mistake Reattempt Test")
    if multi_subject:
        subjects = st.multiselect("Subjects", avail, default=avail, format_func=lambda s: subject_label(s, hindi), key=f"b_subs_{preset}")
    else:
        subjects = [st.selectbox("Subject", avail, format_func=lambda s: subject_label(s, hindi), key=f"b_sub_{preset}")]
    base = qdf
    if preset == "Previous-Year Paper Practice":
        base = verified_pyq_pool(qdf)
        st.caption("Only verified PYQs (exam, year and source all recorded) are used.")
    mk = cached_mistakes()
    if preset == "Mistake Reattempt Test":
        only_open = st.checkbox("Only mistakes not yet marked as revised", value=True, key="b_onlyopen")
        mids = set(mk[~mk["revised"]]["question_id"]) if (only_open and not mk.empty) else set(mk["question_id"]) if not mk.empty else set()
        base = qdf[qdf["id"].isin(mids)]
        missing = len(mids) - len(base)
        if missing > 0:
            st.caption(f"{missing} mistake question(s) no longer exist in the bank and are skipped.")
    sub_pool = base[base["subject"].isin(subjects)] if subjects else base.iloc[0:0]
    counts = sub_pool.groupby("chapter").size() if not sub_pool.empty else pd.Series(dtype=int)
    opts = sorted(counts.index)
    fmt = lambda c: f"{c} ({counts[c]})"  # noqa: E731
    chapters: List[str] = []
    if preset == "Chapter Test":
        if opts:
            chapters = [st.selectbox("Chapter", opts, format_func=fmt, key="b_chap")]
    elif preset == "Multiple Chapter Test":
        chapters = st.multiselect("Chapters", opts, format_func=fmt, key="b_chaps")
    elif preset in ("Quick Quiz (10 questions)", "Previous-Year Paper Practice", "Mistake Reattempt Test"):
        chapters = st.multiselect("Chapters (leave empty for all)", opts, format_func=fmt, key=f"b_chapsopt_{preset}")
    elif preset == "Weak Chapter Test":
        cs = chapter_stats(cached_answers())
        cs = cs[cs["subject"].isin(subjects)] if not cs.empty else cs
        n = st.slider("How many of your weakest chapters?", 1, 5, 3, key="b_nweak")
        weak = ranked_chapters(cs, True, n) if not cs.empty else cs
        if weak is None or weak.empty:
            st.warning(f"Not enough real data yet: a chapter needs at least {MIN_Q_FOR_RANK} checked questions from past tests.")
            return
        chapters = [c for c in weak["chapter"] if c in set(opts)]
        wide(st.dataframe, weak[["chapter", "score_pct", "questions"]].round(1), hide_index=True)
    paper_rows = None
    if preset == "Previous-Year Paper Practice" and not base.empty:
        papers = base.groupby(["pyq_exam", "pyq_year"]).size().reset_index(name="n")
        labels = ["Any (random from the filtered PYQs)"] + [f"{r.pyq_exam} {int(r.pyq_year)} ({r.n} questions)" for r in papers.itertuples()]
        pick = st.selectbox("Paper", labels, key="b_paper")
        if pick != labels[0]:
            r = papers.iloc[labels.index(pick) - 1]
            paper_rows = base[(base["pyq_exam"] == r["pyq_exam"]) & (base["pyq_year"] == r["pyq_year"])]
            paper_rows = paper_rows[paper_rows["subject"].isin(subjects)] if subjects else paper_rows
    f1, f2 = st.columns(2)
    qtypes = f1.multiselect("Question types (empty = all)", QTYPES, key=f"b_qt_{preset}")
    diffs = f2.multiselect("Difficulty (empty = all)", DIFFICULTIES, key=f"b_df_{preset}")
    pool = filter_pool(base, subjects, chapters, qtypes, diffs)
    if paper_rows is not None:
        pool = filter_pool(paper_rows, None, chapters, qtypes, diffs)
    st.markdown(chip(f"{len(pool)} matching questions", "blue") + chip(f"{fmt_marks(float(pool['max_marks'].sum()) if len(pool) else 0)} marks available", "green"), unsafe_allow_html=True)

    name = st.text_input("Test name", value=f"{preset.split(' (')[0]} - {datetime.now(IST).strftime('%d %b')}", key=f"b_name_{preset}")
    s1, s2, s3, s4 = st.columns(4)
    count = s1.number_input("Number of questions", 1, 500, count_def, key=f"b_cnt_{preset}")
    total_marks = s2.number_input("Total marks target (0 = off)", 0.0, 1000.0, 0.0, step=1.0, key=f"b_tm_{preset}",
                                  help="If set, questions are added until this total would be exceeded.")
    duration = s3.number_input("Duration in minutes (0 = untimed)", 0, 600, min_def, key=f"b_dur_{preset}")
    mode = s4.radio("Selection", ["Random", "Manual"], horizontal=True, key=f"b_sel_{preset}")
    neg, frac = neg_marking_widgets("b")
    rows_by_id = {r["id"]: r for r in pool.to_dict("records")}
    manual_ids: List[str] = []
    if mode == "Manual":
        manual_ids = st.multiselect("Choose questions", list(rows_by_id), format_func=lambda i: question_label(rows_by_id[i]), key=f"b_man_{preset}")
    if rows_by_id:
        with st.expander("Preview a question before adding it to a test"):
            pid = st.selectbox("Question", list(rows_by_id), format_func=lambda i: question_label(rows_by_id[i]), key=f"b_prev_{preset}")
            render_question(snapshot_question(rows_by_id[pid]))
    if st.button("Create & start test", type="primary", key="b_start"):
        try:
            if mode == "Manual":
                picked = [rows_by_id[i] for i in manual_ids]
            elif paper_rows is not None:
                picked = sorted(pool.to_dict("records"), key=lambda r: (r["max_marks"], r["id"]))
            else:
                picked = pick_questions(pool, int(count), float(total_marks))
            if not picked:
                st.error("No questions could be selected with these settings. Widen the filters or add questions.")
            else:
                if mode == "Random" and paper_rows is None and int(count) > len(picked) and float(total_marks) <= 0:
                    st.toast(f"Only {len(picked)} matching questions were available.")
                aid = start_attempt(name.strip() or "Untitled test", subjects[0] if len(subjects) == 1 else "Multiple subjects",
                                    "custom", picked, float(duration), neg, frac, chapters, {"preset": preset})
                begin_exam(aid)
                st.rerun()
        except (ValueError, StoreError) as e:
            st.error(str(e))


# =============================================================================
# SECTION 20 - BOARD EXAMINATION MODE
# =============================================================================
def default_blueprint() -> pd.DataFrame:
    return pd.DataFrame([
        {"section": "Section A", "qtype": "MCQ", "difficulty": "Any", "count": 10, "marks_each": 1.0},
        {"section": "Section A", "qtype": "Assertion and Reason", "difficulty": "Any", "count": 2, "marks_each": 1.0},
        {"section": "Section B", "qtype": "Short Answer", "difficulty": "Any", "count": 4, "marks_each": 2.0},
        {"section": "Section C", "qtype": "Long Answer", "difficulty": "Any", "count": 3, "marks_each": 5.0},
        {"section": "Section D", "qtype": "Case-Based", "difficulty": "Any", "count": 2, "marks_each": 4.0},
    ])


def load_blueprint(cfg: Dict[str, Any]) -> None:
    st.session_state["bp_df"] = pd.DataFrame(cfg.get("rows") or [], columns=["section", "qtype", "difficulty", "count", "marks_each"])
    st.session_state["bp_ver"] = st.session_state.get("bp_ver", 0) + 1
    st.session_state["bd_duration"] = int(cfg.get("duration", 180))
    st.session_state["bd_neg"] = bool(cfg.get("neg", False))


def page_board() -> None:
    page_title("Board Exam Mode", "Configure a paper structure, generate a paper from your question bank and sit it under exam conditions.")
    st.warning("The structure below is an editable EXAMPLE, not the official CBSE blueprint. "
               "Configure sections, question types, marks and duration against the official CBSE sample paper and marking scheme for your session.")
    qdf, hindi = cached_questions(), cached_hindi_course()
    if qdf.empty:
        st.info("Add questions to the bank first.")
        return
    avail = [s for s in SUBJECTS if s in set(qdf["subject"])]
    subject = st.selectbox("Subject", avail, format_func=lambda s: subject_label(s, hindi), key="bd_subject")
    chs = sorted(set(qdf[qdf["subject"] == subject]["chapter"]))
    chapters = st.multiselect("Chapters to draw from (empty = whole subject)", chs, key="bd_chapters")
    st.session_state.setdefault("bp_df", default_blueprint())
    st.session_state.setdefault("bp_ver", 0)
    saved = q_select("blueprints", order="name")
    if saved:
        sc1, sc2, sc3 = st.columns([3, 1, 1])
        pick = sc1.selectbox("Saved paper structures", [b["id"] for b in saved], format_func=lambda i: next(b["name"] for b in saved if b["id"] == i), key="bd_saved")
        sc2.button("Load", key="bd_load", on_click=lambda: load_blueprint(next((b["config"] or {}) for b in saved if b["id"] == st.session_state["bd_saved"])))
        if sc3.button("Delete", key="bd_del"):
            q_delete("blueprints", {"id": pick})
            st.rerun()
    st.markdown("**Paper structure** (add or delete rows; 'marks each' = 0 uses each question's own marks)")
    edited = st.data_editor(
        st.session_state["bp_df"], num_rows="dynamic", key=f"bp_editor_{st.session_state['bp_ver']}",
        column_config={
            "section": st.column_config.TextColumn("Section", required=True),
            "qtype": st.column_config.SelectboxColumn("Question type", options=QTYPES, required=True),
            "difficulty": st.column_config.SelectboxColumn("Difficulty", options=["Any"] + DIFFICULTIES, default="Any"),
            "count": st.column_config.NumberColumn("Questions", min_value=0, step=1, default=1),
            "marks_each": st.column_config.NumberColumn("Marks each", min_value=0.0, step=0.5, default=1.0)})
    d1, d2 = st.columns(2)
    duration = d1.number_input("Duration (minutes)", 10, 600, 180, key="bd_duration")
    with d2:
        neg, frac = neg_marking_widgets("bd")
    n1, n2 = st.columns([3, 1])
    bpname = n1.text_input("Save this structure as", key="bd_bpname", placeholder="e.g. Maths sample-paper layout")
    if n2.button("Save structure", key="bd_save"):
        if not bpname.strip():
            st.warning("Give the structure a name.")
        else:
            ex = next((b for b in saved if b["name"].lower() == bpname.strip().lower()), None)
            q_upsert("blueprints", [{"id": ex["id"] if ex else new_id("bp"), "name": bpname.strip(), "created_at": iso_now(),
                                     "config": {"rows": edited.to_dict("records"), "duration": int(duration), "neg": neg}}])
            st.success("Saved.")
            st.rerun()
    pool = filter_pool(qdf, [subject], chapters)
    if st.button("Generate paper", type="primary", key="bd_gen"):
        paper, warns = build_board_paper(pool, edited.to_dict("records"))
        st.session_state["board_draft"] = {"paper": paper, "warns": warns, "subject": subject, "chapters": chapters,
                                           "rows": edited.to_dict("records"), "duration": int(duration), "neg": neg, "frac": frac}
    draft = st.session_state.get("board_draft")
    if draft:
        for w in draft["warns"]:
            st.warning(w)
        if not draft["paper"]:
            st.error("No questions matched the structure.")
            return
        summary = pd.DataFrame(draft["paper"]).groupby("section").agg(Questions=("id", "count"), Marks=("max_marks", "sum")).reset_index()
        wide(st.dataframe, summary, hide_index=True)
        st.markdown(chip(f"{len(draft['paper'])} questions", "blue") + chip(f"{fmt_marks(float(summary['Marks'].sum()))} total marks", "green") +
                    chip(f"{draft['duration']} minutes", "amber"), unsafe_allow_html=True)
        b1, b2 = st.columns(2)
        if b1.button("Start examination now", type="primary", key="bd_start"):
            try:
                aid = start_attempt(f"Board mock - {draft['subject']} - {datetime.now(IST).strftime('%d %b %H:%M')}", draft["subject"], "board",
                                    draft["paper"], float(draft["duration"]), draft["neg"], draft["frac"], draft["chapters"],
                                    {"blueprint": draft["rows"]})
                st.session_state.pop("board_draft", None)
                begin_exam(aid)
                st.rerun()
            except (ValueError, StoreError) as e:
                st.error(str(e))
        if b2.button("Discard this paper", key="bd_discard"):
            st.session_state.pop("board_draft", None)
            st.rerun()


# =============================================================================
# SECTION 21 - EXAMINATION INTERFACE (timer from stored deadline, persistent answers)
# =============================================================================
def _wkey(aid: str, qid: str) -> str:
    return f"w_{aid}_{qid}"


def q_status(qid: str, state: Dict[str, Any]) -> str:
    answered = bool(clean_str(state["responses"].get(qid)))
    marked = qid in state["marked"]
    if marked:
        return "answered_marked" if answered else "marked"
    if answered:
        return "answered"
    return "not_answered" if qid in state["visited"] else "not_visited"


def exam_ctx() -> Optional[Dict[str, Any]]:
    aid = st.session_state.get("active_attempt")
    ex = st.session_state.get("exam")
    if ex and ex["att"]["id"] == aid:
        return ex
    att = get_attempt(aid) if aid else None
    if not att or att["status"] != "in_progress":
        st.session_state["active_attempt"] = None
        st.session_state.pop("exam", None)
        return None
    state = att.get("state") or {}
    state.setdefault("responses", {})
    state.setdefault("visited", [])
    state.setdefault("marked", [])
    state.setdefault("current", 0)
    ex = {"att": att, "state": state}
    st.session_state["exam"] = ex
    return ex


def persist_exam_state() -> None:
    """Write the answers/navigation state to the database (so refresh / reconnect cannot lose them)."""
    ex = st.session_state.get("exam")
    if not ex:
        return
    dl = ex["att"].get("deadline_ts") or 0
    if dl and time.time() > dl + 3:     # grace of 3 s; later edits are not accepted
        return
    try:
        q_update("attempts", {"state": ex["state"]}, {"id": ex["att"]["id"]}, touch=False)
        st.session_state.pop("save_error", None)
    except StoreError as e:
        st.session_state["save_error"] = str(e)


def sync_current_response() -> None:
    ex = st.session_state.get("exam")
    if not ex:
        return
    state, att = ex["state"], ex["att"]
    qs = att["questions"]
    if not qs:
        return
    q = qs[min(max(state["current"], 0), len(qs) - 1)]
    key = _wkey(att["id"], q["qid"])
    if key not in st.session_state:
        return
    v = st.session_state[key]
    v = "" if v is None else str(v).strip()
    if v != clean_str(state["responses"].get(q["qid"])):
        if v:
            state["responses"][q["qid"]] = v
        else:
            state["responses"].pop(q["qid"], None)
        persist_exam_state()


def cb_answer() -> None:
    sync_current_response()


def cb_goto(i: int) -> None:
    ex = st.session_state.get("exam")
    if not ex:
        return
    sync_current_response()
    qs = ex["att"]["questions"]
    i = min(max(int(i), 0), len(qs) - 1)
    ex["state"]["current"] = i
    if qs[i]["qid"] not in ex["state"]["visited"]:
        ex["state"]["visited"].append(qs[i]["qid"])
    persist_exam_state()


def cb_save() -> None:
    sync_current_response()
    persist_exam_state()
    st.toast("Answer saved")


def cb_clear() -> None:
    ex = st.session_state.get("exam")
    if not ex:
        return
    q = ex["att"]["questions"][ex["state"]["current"]]
    key = _wkey(ex["att"]["id"], q["qid"])
    mode = q.get("eval_mode")
    st.session_state[key] = None if mode == "option" else ""
    ex["state"]["responses"].pop(q["qid"], None)
    persist_exam_state()


def cb_mark() -> None:
    ex = st.session_state.get("exam")
    if not ex:
        return
    sync_current_response()
    q = ex["att"]["questions"][ex["state"]["current"]]
    m = ex["state"]["marked"]
    if q["qid"] in m:
        m.remove(q["qid"])
    else:
        m.append(q["qid"])
    persist_exam_state()


def cb_exit_exam() -> None:
    sync_current_response()
    st.session_state["active_attempt"] = None
    st.session_state.pop("exam", None)
    st.session_state["page"] = "Dashboard"


def resume_exam(aid: str) -> None:
    att = get_attempt(aid)
    if not att or att["status"] != "in_progress":
        return
    dl = att.get("deadline_ts") or 0
    if dl and time.time() >= dl:
        submit_attempt(aid, auto=True)
        st.session_state.update({"view_attempt": aid, "page": "Results", "flash": "Time had run out, so the test was submitted automatically."})
        return
    begin_exam(aid)


def resume_session_once() -> None:
    """After a browser refresh / reconnect: finish expired tests and reopen the running one."""
    if st.session_state.get("resumed"):
        return
    st.session_state["resumed"] = True
    latest = None
    for r in q_select("attempts", {"status": "in_progress"}, cols=["id", "deadline_ts", "created_at"], order="-created_at"):
        if r["deadline_ts"] and time.time() >= r["deadline_ts"]:
            submit_attempt(r["id"], auto=True)
            st.session_state["view_attempt"] = r["id"]
            st.session_state["flash"] = "A test whose time ran out was submitted automatically (answers saved before the deadline were scored)."
        elif latest is None:
            latest = r["id"]
    if latest:
        begin_exam(latest)


def do_submit(auto: bool) -> None:
    aid = st.session_state.get("active_attempt")
    if not aid:
        return
    submit_attempt(aid, auto=auto)
    st.session_state.update({"active_attempt": None, "view_attempt": aid, "page": "Results"})
    st.session_state.pop("exam", None)
    if auto:
        st.session_state["flash"] = "Time is up - the examination was submitted automatically."


@ui_dialog("Submit examination?")
def submit_dialog() -> None:
    ex = exam_ctx()
    if not ex:
        st.rerun()
        return
    qs, state = ex["att"]["questions"], ex["state"]
    answered = sum(1 for q in qs if clean_str(state["responses"].get(q["qid"])))
    unanswered = len(qs) - answered
    st.markdown(f"**Answered:** {answered} / {len(qs)}  \n**Unanswered:** {unanswered}  \n**Marked for review:** {len(state['marked'])}")
    st.warning("After submitting you cannot change your answers.")
    ok = True
    if unanswered:
        ok = st.checkbox(f"I understand that {unanswered} unanswered question(s) will score 0.", key="submit_ack")
    c1, c2 = st.columns(2)
    if c1.button("Confirm and submit", type="primary", disabled=not ok, key="submit_confirm"):
        try:
            do_submit(False)
        except StoreError as e:
            st.error(f"Could not submit: {e}. Your answers are still saved - try again.")
            return
        st.rerun()
    if c2.button("Go back", key="submit_back"):
        st.rerun()


@ui_fragment(run_every=1)
def timer_box(deadline: float, start: float) -> None:
    now = time.time()
    if deadline:
        rem = deadline - now
        if rem <= 0:
            st.rerun()
            return
        st.markdown(f"<div class='timer {'low' if rem < 300 else ''}'>&#9201; {fmt_clock(rem)}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='timer'>&#9201; {fmt_clock(now - start)}</div>", unsafe_allow_html=True)
        st.caption("Untimed practice - elapsed time")


def render_exam() -> None:
    ex = exam_ctx()
    if ex is None:
        st.rerun()
        return
    att, state = ex["att"], ex["state"]
    qs = att["questions"]
    dl = att.get("deadline_ts") or 0
    if dl and time.time() >= dl:               # authoritative server-side deadline check on every rerun
        do_submit(True)
        st.rerun()
        return
    n = len(qs)
    i = min(max(state["current"], 0), n - 1)
    q = qs[i]
    with st.sidebar:
        st.markdown(f"<div class='brand'>{APP_NAME}</div>", unsafe_allow_html=True)
        st.caption("Examination in progress")
        st.markdown(f"**{att['name']}**")
        st.button("Leave exam (answers stay saved)", on_click=cb_exit_exam, key="leave_exam")
        st.caption("The timer keeps running while you are away. Resume from the Dashboard.")
    if st.session_state.get("save_error"):
        st.error("Could not save to the database: " + st.session_state["save_error"] + " - your answers are kept on screen; keep working and press Save answer to retry.")
    h1, h2 = st.columns([3, 1])
    with h1:
        st.markdown(f"### {att['name']}")
        chs = att.get("chapters") or sorted({x["chapter"] for x in qs})
        st.caption(f"{att['subject']}  |  Chapters: {', '.join(chs[:5])}{'...' if len(chs) > 5 else ''}  |  Negative marking: "
                   f"{'ON (-' + format(att['neg_fraction'], '.2f') + ' x marks, auto-checked only)' if att['neg_marking'] else 'off'}")
    with h2:
        timer_box(dl, att["start_ts"])
    answered = sum(1 for x in qs if clean_str(state["responses"].get(x["qid"])))
    st.progress(answered / n, text=f"Progress: {answered} of {n} answered")
    left, right = st.columns([3, 1.1])
    with left:
        st.markdown("<div class='qbox'>", unsafe_allow_html=True)
        render_question(q, show_answer=False, number=i + 1, show_options=False)
        st.markdown("</div>", unsafe_allow_html=True)
        key = _wkey(att["id"], q["qid"])
        saved = clean_str(state["responses"].get(q["qid"]))
        mode = q.get("eval_mode")
        if mode == "option":
            letters = LETTERS[:len(q["options"])]
            st.radio("Your answer", letters, index=letters.index(saved) if saved in letters else None,
                     format_func=lambda L: f"{L}.  {q['options'][letters.index(L)]}", key=key, on_change=cb_answer)
        elif mode in ("numeric", "text"):
            st.text_input("Your answer" + (" (a number)" if mode == "numeric" else ""), value=saved, key=key, on_change=cb_answer)
        else:
            st.text_area("Write your answer here (changes are saved when you click outside the box or press Save answer)",
                         value=saved, height=260, key=key, on_change=cb_answer)
            st.caption("Written answers are marked by you afterwards (Evaluate Answers) - until then they show as Pending Evaluation.")
        b = st.columns(5)
        b[0].button("Previous", on_click=cb_goto, args=(i - 1,), disabled=i == 0, key=f"prev_{i}")
        b[1].button("Save answer", on_click=cb_save, key=f"save_{i}")
        b[2].button("Clear", on_click=cb_clear, key=f"clear_{i}")
        b[3].button("Unmark review" if q["qid"] in state["marked"] else "Mark for review", on_click=cb_mark, key=f"mark_{i}")
        b[4].button("Next", on_click=cb_goto, args=(i + 1,), disabled=i == n - 1, key=f"next_{i}", type="primary")
    with right:
        st.markdown(f"**Question {i + 1} of {n}**")
        stats = {"answered": 0, "answered_marked": 0, "marked": 0, "not_answered": 0, "not_visited": 0}
        for x in qs:
            stats[q_status(x["qid"], state)] += 1
        st.markdown(chip(f"Answered {stats['answered'] + stats['answered_marked']}", "green") + chip(f"Unanswered {n - answered}", "red") +
                    chip(f"Marked {stats['marked'] + stats['answered_marked']}", "") + chip(f"Not visited {stats['not_visited']}", "blue"),
                    unsafe_allow_html=True)
        with st.container(height=300):
            for r0 in range(0, n, 5):
                cols = st.columns(5)
                for j, c in enumerate(cols):
                    idx = r0 + j
                    if idx >= n:
                        break
                    c.button(f"{STATUS_ICON[q_status(qs[idx]['qid'], state)]}{idx + 1}", key=f"pal_{att['id']}_{idx}",
                             on_click=cb_goto, args=(idx,), type="primary" if idx == i else "secondary")
        st.caption("⚪ not visited  🔴 not answered  🟢 answered  🟣 marked  🟣✔ answered + marked")
        if st.button("Submit examination", type="primary", key="submit_open"):
            sync_current_response()
            submit_dialog()


# =============================================================================
# SECTION 22 - RESULTS
# =============================================================================
def page_results() -> None:
    page_title("Results", "Review every test, with answers, explanations and chapter-wise performance.")
    flash = st.session_state.pop("flash", None)
    if flash:
        st.info(flash)
    att = cached_attempts()
    done = att[att["status"].isin(["completed", "pending_eval"])] if not att.empty else att
    if done.empty:
        st.info("No submitted tests yet.")
        return
    ids = done["id"].tolist()
    va = st.session_state.pop("view_attempt", None)
    if va in ids:
        st.session_state["res_sel"] = va
    if st.session_state.get("res_sel") not in ids:
        st.session_state.pop("res_sel", None)
    labels = {r["id"]: f"{r['name']}  |  {fmt_ts(r['start_ts'])}  |  {score_text(r)}" for _, r in done.iterrows()}
    aid = st.selectbox("Choose a test", ids, format_func=lambda x: labels[x], key="res_sel")
    a = get_attempt(aid)
    answers = load_answers(aid)
    if not a or not answers:
        st.warning("This attempt has no stored answers.")
        return
    tot = compute_totals(answers)
    qmap = {q["qid"]: q for q in a["questions"]}
    st.markdown(f"### {a['name']}")
    st.caption(f"{a['subject']}  |  {fmt_ts(a['start_ts'])}  |  " + ("auto-submitted when time ran out" if a.get("auto_submitted") else "submitted manually"))
    if tot["final"]:
        k = st.columns(4)
        k[0].markdown(kpi("Marks obtained", fmt_marks(tot["obtained"]), f"out of {fmt_marks(tot['max_total'])}"), unsafe_allow_html=True)
        k[1].markdown(kpi("Percentage", f"{tot['percentage']:.1f}%", "final score"), unsafe_allow_html=True)
        k[2].markdown(kpi("Accuracy", f"{tot['accuracy']:.1f}%", "correct / answered"), unsafe_allow_html=True)
        k[3].markdown(kpi("Time taken", fmt_clock((a["submitted_ts"] or 0) - a["start_ts"]), f"of {fmt_marks(a['duration_min'])} min" if a["duration_min"] else "untimed"), unsafe_allow_html=True)
    else:
        checked_max = tot["max_total"] - tot["pending_marks"]
        st.warning(f"PENDING EVALUATION - this is NOT a final score. Provisional objective score: {fmt_marks(tot['obtained'])} / {fmt_marks(checked_max)}. "
                   f"{fmt_marks(tot['pending_marks'])} marks (from {tot['pending']} written answer(s)) are awaiting your marking. "
                   f"Final score = provisional + marks you award (out of {fmt_marks(tot['max_total'])}).")
        k = st.columns(3)
        k[0].markdown(kpi("Provisional score", fmt_marks(tot["obtained"]), f"of {fmt_marks(checked_max)} checked"), unsafe_allow_html=True)
        k[1].markdown(kpi("Pending marks", fmt_marks(tot["pending_marks"]), "awaiting evaluation"), unsafe_allow_html=True)
        k[2].markdown(kpi("Final score", "Pending", f"out of {fmt_marks(tot['max_total'])}"), unsafe_allow_html=True)
        st.button("Evaluate answers now", on_click=go, args=("Evaluate Answers",), key="res_eval", type="primary")
    c = st.columns(5)
    for col, (lab, key_) in zip(c, [("Correct", "correct"), ("Incorrect", "incorrect"), ("Partial", "partial"), ("Unattempted", "unattempted"), ("Pending", "pending")]):
        col.markdown(kpi(lab, tot[key_]), unsafe_allow_html=True)

    adf = pd.DataFrame(answers)
    adf["marks_pos"] = adf["marks"].fillna(0).clip(lower=0)
    if adf["section"].fillna("").ne("").any():
        st.markdown("#### Section-wise marks")
        sec = adf.groupby("section").agg(questions=("id", "count"), obtained=("marks", "sum"), maximum=("max_marks", "sum"),
                                         pending=("status", lambda s: int((s == "pending").sum()))).reset_index()
        wide(st.dataframe, sec, hide_index=True)
    st.markdown("#### Chapter-wise performance")
    cw = chapter_stats(adf)
    if cw.empty:
        st.caption("Nothing has been checked yet.")
    else:
        wide(st.dataframe, cw[["subject", "chapter", "questions", "correct", "marks", "max_marks", "score_pct"]].round(1), hide_index=True)
        st.bar_chart(cw.set_index("chapter")["score_pct"], color="#38bdf8")
    st.markdown("#### Question-wise review")
    icon = {"correct": "✅", "incorrect": "❌", "partial": "🟡", "unattempted": "⚪", "pending": "⏳"}
    for ans in answers:
        q = qmap.get(ans["question_id"])
        if not q:
            continue
        tag = "PENDING EVALUATION" if ans["status"] == "pending" else ans["status"]
        with st.expander(f"Q{ans['position']}  {icon.get(ans['status'], '')}  {tag}  -  {fmt_marks(ans['marks'])} / {fmt_marks(ans['max_marks'])}  -  {ans['chapter']}"):
            render_question(q, show_answer=False)
            st.markdown(f"**Your answer:** {md_text(ans['response']) or '*(not attempted)*'}")
            st.markdown(f"**Correct answer:** {md_text(correct_answer_text(q)) or '*(manual marking)*'}")
            for lab, k_ in (("Explanation", "explanation"), ("Model answer", "model_answer"), ("Marking scheme", "marking_scheme")):
                if clean_str(q.get(k_)):
                    st.markdown(f"**{lab}:** {md_text(q[k_])}")
            if ans.get("feedback"):
                st.markdown(f"**Your evaluation feedback:** {md_text(ans['feedback'])}")
    d1, d2, d3 = st.columns(3)
    d1.download_button("Download printable report (HTML)", build_report_html(a, answers), file_name=f"{a['name']}_result.html".replace(" ", "_"), mime="text/html", key="dl_rep")
    d2.download_button("Download answers (CSV)", df_to_csv(adf.drop(columns=["owner_id"], errors="ignore")), file_name="answers.csv", mime="text/csv", key="dl_ans")
    with d3.expander("Delete this attempt"):
        if st.checkbox("I understand this permanently deletes this attempt and its answers", key="del_att_ack"):
            if st.button("Delete attempt", key="del_att"):
                delete_attempt(aid)
                st.session_state.pop("res_sel", None)
                st.rerun()
    st.caption("Tip: open the downloaded HTML report and use your browser's Print -> Save as PDF.")


# =============================================================================
# SECTION 23 - MANUAL EVALUATION
# =============================================================================
def page_eval() -> None:
    page_title("Evaluate Answers", "Mark written answers yourself. Nothing is scored until you award marks - there is no automatic or AI marking of descriptive answers.")
    att = cached_attempts()
    show_done = st.checkbox("Also show tests that are already fully evaluated (to revise marks)", key="ev_showdone")
    sts = ["pending_eval", "completed"] if show_done else ["pending_eval"]
    cand = att[att["status"].isin(sts)] if not att.empty else att
    if cand.empty:
        st.success("No pending evaluations.")
        return
    labels = {r["id"]: f"{r['name']}  |  {fmt_ts(r['start_ts'])}  |  pending {fmt_marks(r['pending_marks'])} marks" for _, r in cand.iterrows()}
    aid = st.selectbox("Test", cand["id"].tolist(), format_func=lambda x: labels[x], key="ev_att")
    a = get_attempt(aid)
    answers = load_answers(aid)
    qmap = {q["qid"]: q for q in a["questions"]}
    todo = [x for x in answers if x["status"] == "pending" or (show_done and x.get("evaluated_at"))]
    if not todo:
        st.success("Everything in this test has been evaluated.")
        return
    st.caption(f"{len([x for x in answers if x['status'] == 'pending'])} answer(s) pending in this test.")
    for ans in todo:
        q = qmap.get(ans["question_id"], {})
        with st.form(f"ev_{aid}_{ans['question_id']}"):
            st.markdown(f"**Q{ans['position']}** - {ans['chapter']} - {ans['qtype']}  " + ("(already evaluated)" if ans.get("evaluated_at") else "(PENDING)"))
            st.markdown(md_text(q.get("question_text")))
            img = cached_image(ans["question_id"])
            if img:
                st.image(img)
            st.markdown("**Student's response:**")
            st.info(ans["response"] or "(blank)")
            st.markdown(f"**Model answer:** {md_text(q.get('model_answer')) or '-'}")
            st.markdown(f"**Marking scheme:** {md_text(q.get('marking_scheme')) or '-'}")
            mm = float(ans["max_marks"])
            m1, m2 = st.columns([1, 3])
            marks = m1.number_input(f"Marks awarded (max {fmt_marks(mm)})", 0.0, mm, float(ans["marks"]) if ans.get("marks") is not None and ans.get("evaluated_at") else 0.0, step=0.5, key=f"evm_{aid}_{ans['question_id']}")
            fb = m2.text_area("Feedback (optional)", value=ans.get("feedback") or "", key=f"evf_{aid}_{ans['question_id']}", height=90)
            if st.form_submit_button("Save evaluation", type="primary"):
                try:
                    tot = save_manual_evaluation(aid, ans["question_id"], marks, fb)
                    st.toast("Evaluation saved" + (" - test is now fully evaluated" if tot["final"] else ""))
                    st.rerun()
                except (ValueError, StoreError) as e:
                    st.error(str(e))


# =============================================================================
# SECTION 24 - QUESTION BANK
# =============================================================================
def question_form(existing: Optional[Dict[str, Any]], kp: str) -> None:
    """Add / edit form. `kp` is a key prefix so the form can be reset after saving."""
    is_new = existing is None
    ex = existing or {}
    chdf = cached_chapters()
    hindi = cached_hindi_course()
    c1, c2, c3 = st.columns(3)
    subject = c1.selectbox("Subject", SUBJECTS, index=SUBJECTS.index(ex["subject"]) if ex.get("subject") in SUBJECTS else 0,
                           format_func=lambda s: subject_label(s, hindi), key=f"{kp}subject")
    chaps = chapter_names(chdf, subject)
    if ex.get("chapter") and ex.get("subject") == subject and ex["chapter"] not in chaps:
        chaps = [ex["chapter"]] + chaps
    if not chaps:
        st.warning("This subject has no enabled chapters yet. Add chapters in Subjects & Chapters first.")
        return
    chapter = c2.selectbox("Chapter", chaps, index=chaps.index(ex["chapter"]) if ex.get("chapter") in chaps else 0, key=f"{kp}chapter_{subject}")
    topic = c3.text_input("Topic (optional)", value=clean_str(ex.get("topic")), key=f"{kp}topic")
    d1, d2, d3 = st.columns(3)
    qtype = d1.selectbox("Question type", QTYPES, index=QTYPES.index(ex["qtype"]) if ex.get("qtype") in QTYPES else 0, key=f"{kp}qtype")
    diff = d2.selectbox("Difficulty", DIFFICULTIES, index=DIFFICULTIES.index(ex["difficulty"]) if ex.get("difficulty") in DIFFICULTIES else 1, key=f"{kp}diff")
    marks = d3.number_input("Maximum marks", 0.5, 100.0, float(ex.get("max_marks") or 1.0), step=0.5, key=f"{kp}marks")
    dm = ex.get("eval_mode") if (ex.get("eval_mode") in EVAL_MODES and ex.get("qtype") == qtype) else (
        "option" if qtype in ("MCQ", "Assertion and Reason") else "numeric" if qtype == "Numerical" else "manual")
    mode = st.selectbox("How should this question be checked?", EVAL_MODES, index=EVAL_MODES.index(dm),
                        format_func=lambda m: EVAL_LABELS[m], key=f"{kp}mode_{qtype}")
    text = st.text_area("Question text", value=clean_str(ex.get("question_text")), height=140, key=f"{kp}text")
    st.caption(r"Maths: wrap LaTeX in $...$, e.g. $x^2+3x-4=0$, $\dfrac{a}{b}$, $6.02\times10^{23}$.  Chemistry: $\mathrm{H_2SO_4}$, $2H_2+O_2\rightarrow 2H_2O$.  Unicode (हिन्दी, ², ₂) works.")
    img_up = st.file_uploader("Image / diagram / map (optional, PNG or JPG)", type=["png", "jpg", "jpeg"], key=f"{kp}img")
    remove_img = False
    if not is_new:
        cur = get_image_bytes(ex["id"])
        if cur and not img_up:
            st.image(cur, caption="Current image")
            remove_img = st.checkbox("Remove current image", key=f"{kp}rmimg")
    opts: List[str] = []
    correct = ""
    tol = 0.0
    if mode == "option":
        base = ex.get("options") if (ex.get("options") and ex.get("qtype") == qtype) else (AR_OPTIONS if qtype == "Assertion and Reason" else [])
        for i, L in enumerate(LETTERS):
            opts.append(st.text_input(f"Option {L}", value=base[i] if i < len(base) else "", key=f"{kp}opt{L}_{qtype}"))
        cur_c = clean_str(ex.get("correct_answer")).upper()
        correct = st.selectbox("Correct option", LETTERS, index=LETTERS.index(cur_c) if cur_c in LETTERS else 0, key=f"{kp}correct")
    elif mode == "numeric":
        correct = st.text_input("Correct numerical answer", value=clean_str(ex.get("correct_answer")), key=f"{kp}num")
        tol = st.number_input("Tolerance (accept answers within +/- this amount)", 0.0, 1e9, float(ex.get("tolerance") or 0.0), format="%.4f", key=f"{kp}tol")
    elif mode == "text":
        correct = st.text_input("Correct answer (separate accepted alternatives with ||)", value=clean_str(ex.get("correct_answer")), key=f"{kp}txt")
    e1, e2 = st.columns(2)
    expl = e1.text_area("Explanation", value=clean_str(ex.get("explanation")), height=110, key=f"{kp}expl")
    model = e2.text_area("Model answer", value=clean_str(ex.get("model_answer")), height=110, key=f"{kp}model")
    scheme = st.text_area("Marking scheme", value=clean_str(ex.get("marking_scheme")), height=80, key=f"{kp}scheme",
                          placeholder="e.g. 1 mark for formula, 1 mark for substitution, 1 mark for answer")
    f1, f2, f3 = st.columns(3)
    source = f1.text_input("Source (book, paper, website...)", value=clean_str(ex.get("source")), key=f"{kp}source")
    pyq_exam = f2.text_input("Previous-year exam (only if verified)", value=clean_str(ex.get("pyq_exam")), key=f"{kp}pexam", placeholder="e.g. CBSE Class 10 Board")
    pyq_year = f3.text_input("Year (only if verified)", value="" if ex.get("pyq_year") in (None, "") or (isinstance(ex.get("pyq_year"), float) and math.isnan(ex.get("pyq_year"))) else str(int(ex["pyq_year"])), key=f"{kp}pyear")
    qid = st.text_input("Question ID" + (" (cannot be changed)" if not is_new else " (leave blank to auto-generate)"),
                        value=ex.get("id", ""), disabled=not is_new, key=f"{kp}qid")
    raw = {"question_id": qid if is_new else ex["id"], "subject": subject, "chapter": chapter, "topic": topic, "qtype": qtype,
           "difficulty": diff, "question_text": text, "options": opts, "correct_answer": correct, "eval_mode": mode,
           "tolerance": tol, "explanation": expl, "model_answer": model, "max_marks": marks, "marking_scheme": scheme,
           "source": source, "pyq_exam": pyq_exam, "pyq_year": pyq_year}
    row, errs = validate_question(raw, chapter_map(chdf), allow_new_chapter=not is_new)
    with st.expander("Live preview"):
        if text.strip():
            render_question(snapshot_question(dict(row, id=row["id"] or "preview")), show_answer=True)
        else:
            st.caption("Type the question to see a preview.")
    if st.button("Save question" if not is_new else "Add question", type="primary", key=f"{kp}save"):
        if errs:
            for e in errs:
                st.error(e)
        else:
            try:
                qid_saved = save_question(row, is_new, img_up.getvalue() if img_up else None, remove_img)
                st.toast(f"Saved {qid_saved}")
                if is_new:
                    st.session_state["qf_ver"] = st.session_state.get("qf_ver", 0) + 1
                else:
                    st.session_state.pop("edit_qid", None)
                st.rerun()
            except (ValueError, StoreError) as e:
                st.error(str(e))


def render_import_preview(items: List[Dict[str, Any]], key: str, create_chapters: bool) -> None:
    n_new = sum(1 for i in items if i["status"] == "new")
    n_ex = sum(1 for i in items if i["status"] == "exists")
    n_bad = sum(1 for i in items if i["status"] == "invalid")
    st.markdown(chip(f"{len(items)} rows read", "blue") + chip(f"{n_new} new", "green") + chip(f"{n_ex} ID already in bank", "amber") + chip(f"{n_bad} invalid", "red"), unsafe_allow_html=True)
    tbl = pd.DataFrame([{"Row": i["row"], "Status": i["status"], "Question ID": i["data"].get("id", ""), "Subject": i["data"].get("subject", ""),
                         "Chapter": i["data"].get("chapter", ""), "Type": i["data"].get("qtype", ""),
                         "Question": clean_str(i["data"].get("question_text"))[:70], "Problems": "; ".join(i["errors"])} for i in items])
    wide(st.dataframe, tbl, hide_index=True)
    if n_bad:
        bad = pd.DataFrame([{"row": i["row"], "problems": "; ".join(i["errors"]), **{k: v for k, v in i["data"].items() if k != "options"}} for i in items if i["status"] == "invalid"])
        st.download_button("Download list of invalid rows (CSV)", bad.to_csv(index=False), file_name="invalid_rows.csv", mime="text/csv", key=f"{key}_bad")
    overwrite = False
    if n_ex:
        overwrite = st.checkbox("Overwrite existing questions that have the same ID (otherwise they are skipped and left untouched)", key=f"{key}_ow")
    todo = n_new + (n_ex if overwrite else 0)
    if st.button(f"Import {todo} valid row(s)", type="primary", disabled=todo == 0, key=f"{key}_go"):
        try:
            res = import_questions(items, overwrite, create_chapters)
            st.success(f"Imported: {res['added']} added, {res['overwritten']} overwritten, {res['skipped']} skipped. Invalid rows were NOT imported (see the list above).")
            st.session_state.pop("bulk_items", None)
        except (ValueError, StoreError) as e:
            st.error(str(e))


def page_bank() -> None:
    page_title("Question Bank", "No limit on size - add questions through this page; the code never needs editing.")
    tabs = st.tabs(["Browse & manage", "Add question", "Bulk add (paste)", "Import CSV / JSON", "Export"])
    hindi = cached_hindi_course()
    with tabs[0]:
        qdf = cached_questions()
        st.caption(f"{len(qdf)} questions in the bank")
        if qdf.empty:
            st.info("The bank is empty. Use the other tabs to add or import questions.")
        else:
            f1, f2, f3 = st.columns(3)
            subs = f1.multiselect("Subject", SUBJECTS, format_func=lambda s: subject_label(s, hindi), key="bk_sub")
            chap_opts = sorted(set((qdf[qdf["subject"].isin(subs)] if subs else qdf)["chapter"]))
            chs = f2.multiselect("Chapter", chap_opts, key="bk_chap")
            q_ = f3.text_input("Search (ID, text, topic, source)", key="bk_search")
            g1, g2, g3 = st.columns(3)
            dfs = g1.multiselect("Difficulty", DIFFICULTIES, key="bk_diff")
            qts = g2.multiselect("Question type", QTYPES, key="bk_qt")
            mks = g3.multiselect("Marks", sorted(qdf["max_marks"].unique().tolist()), format_func=fmt_marks, key="bk_marks")
            view = filter_pool(qdf, subs, chs, qts, dfs)
            if mks:
                view = view[view["max_marks"].isin(mks)]
            if q_.strip():
                s = q_.strip()
                mask = False
                for col in ("id", "question_text", "topic", "source", "chapter"):
                    mask = mask | view[col].fillna("").astype(str).str.contains(s, case=False, regex=False)
                view = view[mask]
            st.markdown(chip(f"{len(view)} match", "blue"), unsafe_allow_html=True)
            size = st.selectbox("Rows per page", [25, 50, 100, 200], index=1, key="bk_size")
            pages = max(1, math.ceil(len(view) / size))
            page_no = st.number_input("Page", 1, pages, 1, key="bk_page") if pages > 1 else 1
            pg = view.iloc[(page_no - 1) * size: page_no * size][["id", "subject", "chapter", "topic", "qtype", "difficulty", "max_marks", "eval_mode", "question_text"]]
            ev = wide(st.dataframe, pg, hide_index=True, on_select="rerun", selection_mode="multi-row", key="bk_table")
            sel_ids: List[str] = []
            try:
                sel_ids = pg.iloc[list(ev.selection.rows)]["id"].tolist()
            except Exception:
                sel_ids = []
            pick = st.selectbox("...or pick one question by ID", [""] + pg["id"].tolist(), key="bk_pick")
            if pick and not sel_ids:
                sel_ids = [pick]
            if sel_ids:
                st.markdown(f"**{len(sel_ids)} selected**")
                a1, a2 = st.columns(2)
                if len(sel_ids) == 1 and a1.button("Edit this question", key="bk_edit"):
                    st.session_state["edit_qid"] = sel_ids[0]
                    st.rerun()
                with a2.expander("Delete selected"):
                    if st.checkbox(f"Yes, permanently delete {len(sel_ids)} question(s)", key="bk_delack"):
                        if st.button("Delete", key="bk_del"):
                            delete_questions(sel_ids)
                            st.success("Deleted. (Past results keep their own copy of the question.)")
                            st.rerun()
                for qid in sel_ids[:5]:
                    full = get_question(qid)
                    if full:
                        with st.expander(f"Preview {qid}", expanded=len(sel_ids) == 1):
                            render_question(snapshot_question(full))
                if len(sel_ids) > 5:
                    st.caption("Previewing the first 5 selected questions.")
            if st.session_state.get("edit_qid"):
                st.divider()
                st.markdown(f"### Editing {st.session_state['edit_qid']}")
                full = get_question(st.session_state["edit_qid"])
                if full:
                    question_form(full, kp=f"qe_{st.session_state['edit_qid']}_")
                    if st.button("Cancel editing", key="bk_canceledit"):
                        st.session_state.pop("edit_qid", None)
                        st.rerun()
                else:
                    st.session_state.pop("edit_qid", None)
    with tabs[1]:
        question_form(None, kp=f"qn{st.session_state.get('qf_ver', 0)}_")
    with tabs[2]:
        chdf = cached_chapters()
        st.markdown("Paste many questions at once. Separate questions with a line containing only `---`.")
        with st.expander("Format"):
            st.code("Q: What is the HCF of 12 and 18?\nA: 3\nB: 6\nC: 9\nD: 12\nAns: B\nExp: 12 = 2*2*3, 18 = 2*3*3, common = 2*3\n---\nQ: Next question text...\nAns: C", language="text")
            st.caption("Keys: Q, A-D, Ans, Exp, Model, Topic, Scheme, Marks, Source, Mode (option/numeric/text/manual), Tol, ID. Extra lines continue the previous key.")
        b1, b2, b3, b4, b5 = st.columns(5)
        bs = b1.selectbox("Subject", SUBJECTS, format_func=lambda s: subject_label(s, hindi), key="bulk_sub")
        bc_opts = chapter_names(chdf, bs)
        bc = b2.selectbox("Chapter", bc_opts, key=f"bulk_chap_{bs}") if bc_opts else None
        bt = b3.selectbox("Type", QTYPES, key="bulk_type")
        bd = b4.selectbox("Difficulty", DIFFICULTIES, index=1, key="bulk_diff")
        bm = b5.number_input("Default marks", 0.5, 100.0, 1.0, step=0.5, key="bulk_marks")
        txt = st.text_area("Questions", height=300, key="bulk_text")
        if bc is None:
            st.warning("Add chapters for this subject first.")
        elif st.button("Parse & preview", key="bulk_parse"):
            raws = parse_bulk_text(txt, {"subject": bs, "chapter": bc, "qtype": bt, "difficulty": bd, "max_marks": bm})
            st.session_state["bulk_items"] = prepare_import(raws, chapter_map(chdf), load_question_ids())
        if st.session_state.get("bulk_items"):
            render_import_preview(st.session_state["bulk_items"], "bulk", False)
    with tabs[3]:
        st.download_button("Download CSV template", question_template_csv(), file_name="questions_template.csv", mime="text/csv", key="tpl")
        st.caption("Required columns: " + ", ".join(CSV_REQUIRED) + ". Save as CSV UTF-8. Sample rows in the template are placeholders - replace them. "
                   "Images are added later through the Edit form.")
        up = st.file_uploader("Upload a CSV or JSON file", type=["csv", "json"], key="imp_file")
        create_ch = st.checkbox("Create chapters that do not exist yet (otherwise such rows are rejected)", key="imp_create")
        if up is not None:
            data = up.getvalue()
            raws, fatal, warns = (parse_json_bytes(data) if up.name.lower().endswith(".json") else parse_csv_bytes(data))
            for w in warns:
                st.warning(w)
            if fatal:
                for f in fatal:
                    st.error(f)
            elif not raws:
                st.warning("The file contains no question rows.")
            else:
                items = prepare_import(raws, chapter_map(cached_chapters()), load_question_ids(), allow_new_chapter=create_ch)
                render_import_preview(items, "imp", create_ch)
    with tabs[4]:
        qdf = cached_questions()
        st.caption("Exports cover every question in the bank (no limit). Images are not included in CSV; use the full backup in Settings & Backup.")
        if qdf.empty:
            st.info("Nothing to export yet.")
        else:
            st.download_button(f"Download all {len(qdf)} questions (CSV)", questions_to_csv(qdf), file_name="as22122_questions.csv", mime="text/csv", key="exp_csv")
            st.download_button("Download all questions (JSON)", json.dumps(json.loads(qdf.to_json(orient="records", force_ascii=False)), ensure_ascii=False, indent=1),
                               file_name="as22122_questions.json", mime="application/json", key="exp_json")


# =============================================================================
# SECTION 25 - PREVIOUS-YEAR QUESTIONS
# =============================================================================
def page_pyq() -> None:
    page_title("Previous-Year Questions", "Only questions that you recorded with exam, year AND source are shown as verified. Nothing here is invented.")
    qdf, hindi = cached_questions(), cached_hindi_course()
    if qdf.empty:
        st.info("Add PYQs in the Question Bank (fill the 'Previous-year exam', 'Year' and 'Source' fields) or import a CSV with pyq_exam, pyq_year and source columns.")
        st.button("Open Question Bank", on_click=go, args=("Question Bank",), key="pyq_goqb")
        return
    py = qdf[qdf["pyq_year"].notna() | (qdf["qtype"] == "Previous-Year Question")].copy()
    if py.empty:
        st.info("No PYQs recorded yet.")
        st.button("Add PYQs", on_click=go, args=("Question Bank",), key="pyq_goqb2")
        return
    py["verified"] = py.apply(lambda r: is_verified_pyq(r.to_dict()), axis=1)
    f1, f2, f3, f4, f5 = st.columns(5)
    subs = f1.multiselect("Subject", SUBJECTS, format_func=lambda s: subject_label(s, hindi), key="py_sub")
    chs = f2.multiselect("Chapter", sorted(set(py["chapter"])), key="py_chap")
    yrs = f3.multiselect("Year", sorted({int(y) for y in py["pyq_year"].dropna()}, reverse=True), key="py_year")
    mks = f4.multiselect("Marks", sorted(py["max_marks"].unique().tolist()), format_func=fmt_marks, key="py_marks")
    qts = f5.multiselect("Type", QTYPES, key="py_qt")
    v = filter_pool(py, subs, chs, qts)
    if yrs:
        v = v[v["pyq_year"].isin(yrs)]
    if mks:
        v = v[v["max_marks"].isin(mks)]
    st.markdown(chip(f"{len(v)} PYQs", "blue") + chip(f"{int(v['verified'].sum())} verified", "green") + chip(f"{int((~v['verified']).sum())} NOT verified (missing exam/year/source)", "red"), unsafe_allow_html=True)
    show = v[["id", "pyq_exam", "pyq_year", "subject", "chapter", "qtype", "max_marks", "verified", "source", "question_text"]].copy()
    show["pyq_year"] = show["pyq_year"].map(lambda y: "" if pd.isna(y) else int(y))
    wide(st.dataframe, show, hide_index=True)
    if not v.empty:
        rows = {r["id"]: r for r in v.to_dict("records")}
        pid = st.selectbox("Preview", list(rows), format_func=lambda i: question_label(rows[i]), key="py_prev")
        full = get_question(pid)
        if full:
            render_question(snapshot_question(full))
        vv = v[v["verified"]]
        if not vv.empty:
            st.markdown("#### Practise these verified PYQs")
            p1, p2, p3 = st.columns(3)
            dur = p1.number_input("Duration (minutes, 0 = untimed)", 0, 600, 60, key="py_dur")
            cap = p2.number_input("Max questions", 1, 500, min(20, len(vv)), key="py_cap")
            if p3.button("Start practice test", type="primary", key="py_start"):
                try:
                    picked = pick_questions(vv, int(cap))
                    aid = start_attempt("PYQ practice - " + datetime.now(IST).strftime("%d %b %H:%M"), subs[0] if len(subs) == 1 else "Multiple subjects",
                                        "pyq", picked, float(dur), False, 0.0, chs, {"preset": "PYQ"})
                    begin_exam(aid)
                    st.rerun()
                except (ValueError, StoreError) as e:
                    st.error(str(e))


# =============================================================================
# SECTION 26 - SUBJECTS & CHAPTERS MANAGEMENT
# =============================================================================
def page_chapters() -> None:
    page_title("Subjects & Chapters", "Edit the chapter list any time - no code changes needed.")
    st.warning("Chapter names are yours to maintain. The starter list only reuses the units you supplied; it is NOT an official syllabus. "
               "Check every subject against the official CBSE curriculum published for your examination session, and rename / add / disable chapters here to match.")
    chdf, qdf, hindi = cached_chapters(), cached_questions(), cached_hindi_course()
    if chdf.empty:
        if st.button("Load starter chapters", type="primary", key="seed_ch"):
            st.success(f"Added {seed_starter_chapters()} chapters.")
            st.rerun()
    tabs = st.tabs([subject_label(s, hindi) for s in SUBJECTS])
    for tab, subject in zip(tabs, SUBJECTS):
        with tab:
            d = chdf[chdf["subject"] == subject].sort_values(["sort_order", "name"]) if not chdf.empty else chdf
            counts = qdf[qdf["subject"] == subject].groupby("chapter").size() if not qdf.empty else pd.Series(dtype=int)
            if d is not None and not d.empty:
                tbl = pd.DataFrame({"Order": d["sort_order"], "Chapter / unit": d["name"], "Enabled": d["enabled"],
                                    "Questions": [int(counts.get(n, 0)) for n in d["name"]]})
                wide(st.dataframe, tbl, hide_index=True)
            else:
                st.caption("No chapters yet for this subject.")
            a1, a2 = st.columns([4, 1])
            newname = a1.text_input("Add a chapter / topic", key=f"ch_new_{subject}", placeholder="Chapter name")
            if a2.button("Add", key=f"ch_add_{subject}"):
                try:
                    add_chapter(subject, newname)
                    st.rerun()
                except (ValueError, StoreError) as e:
                    st.error(str(e))
            if d is not None and not d.empty:
                st.markdown("**Change an existing chapter**")
                rows = {r["id"]: r for r in d.to_dict("records")}
                cid = st.selectbox("Chapter", list(rows), format_func=lambda i: rows[i]["name"], key=f"ch_sel_{subject}")
                cur = rows[cid]
                r1, r2 = st.columns([4, 1])
                nn = r1.text_input("New name", value=cur["name"], key=f"ch_ren_{subject}_{cid}")
                if r2.button("Rename", key=f"ch_renb_{subject}"):
                    try:
                        n = rename_chapter(cid, nn)
                        st.success(f"Renamed. {n} question(s) updated.")
                        st.rerun()
                    except (ValueError, StoreError) as e:
                        st.error(str(e))
                o1, o2, o3 = st.columns(3)
                order = o1.number_input("Order", 0, 999, int(cur["sort_order"] or 0), key=f"ch_ord_{subject}_{cid}")
                if o1.button("Set order", key=f"ch_ordb_{subject}"):
                    set_chapter_fields(cid, sort_order=int(order))
                    st.rerun()
                if o2.button("Disable" if cur["enabled"] else "Enable", key=f"ch_tog_{subject}"):
                    set_chapter_fields(cid, enabled=not cur["enabled"])
                    st.rerun()
                if o3.button("Delete (only if unused)", key=f"ch_del_{subject}"):
                    try:
                        delete_chapter(cid)
                        st.rerun()
                    except (ValueError, StoreError) as e:
                        st.error(str(e))


# =============================================================================
# SECTION 27 - MISTAKE NOTEBOOK
# =============================================================================
def mistake_reattempt_form(m: Dict[str, Any]) -> None:
    full = get_question(m["question_id"])
    if not full:
        st.caption("This question was deleted from the bank, so it cannot be reattempted.")
        return
    q = snapshot_question(full)
    with st.form(f"mre_{m['id']}"):
        mode = q["eval_mode"]
        if mode == "option":
            letters = LETTERS[:len(q["options"])]
            ans = st.radio("Your answer", letters, index=None, format_func=lambda L: f"{L}. {q['options'][letters.index(L)]}", key=f"mra_{m['id']}")
        else:
            ans = st.text_area("Your answer", key=f"mra_{m['id']}", height=100)
        self_ok = None
        if mode == "manual":
            self_ok = st.radio("After reading the model answer, how did you do?", ["Not yet", "I got it right"], horizontal=True, key=f"mrs_{m['id']}")
        if st.form_submit_button("Check"):
            if mode == "manual":
                st.markdown(f"**Model answer:** {md_text(q.get('model_answer')) or '-'}")
                st.markdown(f"**Marking scheme:** {md_text(q.get('marking_scheme')) or '-'}")
                if clean_str(ans):
                    record_mistake_result(q, self_ok == "I got it right", clean_str(ans))
                    invalidate_cache()
                    st.info("Result recorded. Re-open this page to see updated counters.")
                else:
                    st.warning("Write an answer first.")
            elif not clean_str(ans):
                st.warning("Choose or type an answer first.")
            else:
                ev = evaluate_response(q, ans)
                ok = ev["status"] == "correct"
                record_mistake_result(q, ok, clean_str(ans))
                invalidate_cache()
                (st.success if ok else st.error)(("Correct! " if ok else "Not correct. ") + f"Answer: {correct_answer_text(q)}")
                if q.get("explanation"):
                    st.markdown(f"**Explanation:** {md_text(q['explanation'])}")


def page_mistakes() -> None:
    page_title("Mistake Notebook", "Incorrect and unanswered questions are saved automatically - one entry per question, with a count of repeated failures.")
    mk, hindi = cached_mistakes(), cached_hindi_course()
    if mk.empty:
        st.info("No mistakes recorded yet. They appear here after you submit a test.")
        return
    f1, f2, f3, f4 = st.columns(4)
    subs = f1.multiselect("Subject", SUBJECTS, format_func=lambda s: subject_label(s, hindi), key="mk_sub")
    chs = f2.multiselect("Chapter", sorted(set((mk[mk["subject"].isin(subs)] if subs else mk)["chapter"])), key="mk_chap")
    stat = f3.selectbox("Status", ["Needs revision", "Revised", "All"], key="mk_stat")
    term = f4.text_input("Search", key="mk_search")
    v = mk
    if subs:
        v = v[v["subject"].isin(subs)]
    if chs:
        v = v[v["chapter"].isin(chs)]
    if stat == "Needs revision":
        v = v[~v["revised"]]
    elif stat == "Revised":
        v = v[v["revised"]]
    if term.strip():
        v = v[v["question_text"].fillna("").str.contains(term.strip(), case=False, regex=False)]
    improved = int((v["last_result"] == "correct").sum())
    st.markdown(chip(f"{len(v)} shown", "blue") + chip(f"{improved} improved (last reattempt correct)", "green") +
                chip(f"{int((v['fail_count'] >= 2).sum())} failed 2+ times", "red"), unsafe_allow_html=True)
    if not v.empty:
        if st.button("Generate a test from these mistakes", type="primary", key="mk_gen"):
            qdf = cached_questions()
            pool = qdf[qdf["id"].isin(v["question_id"])]
            if pool.empty:
                st.error("None of these questions exist in the bank any more.")
            else:
                try:
                    aid = start_attempt("Mistake reattempt - " + datetime.now(IST).strftime("%d %b %H:%M"), "Multiple subjects" if pool["subject"].nunique() > 1 else pool["subject"].iloc[0],
                                        "mistakes", pool.to_dict("records"), 0.0, False, 0.0, sorted(set(pool["chapter"])), {"preset": "Mistake Reattempt"})
                    begin_exam(aid)
                    st.rerun()
                except (ValueError, StoreError) as e:
                    st.error(str(e))
    for m in v.to_dict("records")[:100]:
        with st.expander(f"{'✅' if m['revised'] else '📌'} {m['chapter']} - failed {m['fail_count']}x - last: {m['last_result']} - {clean_str(m['question_text'])[:70]}"):
            st.markdown(md_text(m["question_text"]))
            st.markdown(f"**My last answer:** {md_text(m['my_answer']) or '(unanswered)'}")
            st.markdown(f"**Correct answer:** {md_text(m['correct_answer'])}")
            if clean_str(m["explanation"]):
                st.markdown(f"**Explanation:** {md_text(m['explanation'])}")
            st.caption(f"First missed {fmt_iso(m['first_date'])} | last attempt {fmt_iso(m['last_date'])} | reattempts {m['reattempt_count']} (correct {m['correct_count']})")
            if st.button("Mark as not revised" if m["revised"] else "Mark as revised", key=f"mkrev_{m['id']}"):
                q_update("mistakes", {"revised": not m["revised"]}, {"id": m["id"]})
                st.rerun()
            st.markdown("**Reattempt**")
            mistake_reattempt_form(m)
    if len(v) > 100:
        st.caption("Showing the first 100; use the filters to narrow down.")


# =============================================================================
# SECTION 28 - ANALYTICS
# =============================================================================
def page_analytics() -> None:
    page_title("Performance Analytics", "Every number comes from your submitted answers - nothing is estimated or made up.")
    att, ans, mk = cached_attempts(), cached_answers(), cached_mistakes()
    done = att[att["status"].isin(["completed", "pending_eval"])] if not att.empty else att
    if done.empty:
        st.info("Take and submit a test to unlock analytics.")
        return
    finals = att[att["status"] == "completed"]
    if len(done) > len(finals):
        st.caption(f"{len(done) - len(finals)} test(s) awaiting manual evaluation are excluded from percentage statistics.")
    cs = chapter_stats(ans)
    tabs = st.tabs(["Overview", "Subjects", "Chapters", "Progress over time", "Question types", "Repeated mistakes", "Time spent", "What to practise"])
    with tabs[0]:
        k = st.columns(4)
        k[0].markdown(kpi("Total tests", len(done)), unsafe_allow_html=True)
        k[1].markdown(kpi("Average", f"{finals['percentage'].mean():.1f}%" if not finals.empty else "-"), unsafe_allow_html=True)
        k[2].markdown(kpi("Best test", f"{finals['percentage'].max():.1f}%" if not finals.empty else "-"), unsafe_allow_html=True)
        k[3].markdown(kpi("Highest marks", f"{fmt_marks(finals['obtained'].max())}" if not finals.empty else "-"), unsafe_allow_html=True)
    with tabs[1]:
        if finals.empty:
            st.caption("Needs at least one fully evaluated test.")
        else:
            sp = finals.groupby("subject").agg(tests=("id", "count"), average=("percentage", "mean"), best=("percentage", "max")).reset_index().round(1)
            wide(st.dataframe, sp, hide_index=True)
            st.bar_chart(sp.set_index("subject")["average"], color="#8b5cf6")
    with tabs[2]:
        if cs.empty:
            st.caption("No checked answers yet.")
        else:
            wide(st.dataframe, cs.sort_values("accuracy").round(1), hide_index=True)
            st.bar_chart(cs.assign(label=cs["subject"].str[:4] + " - " + cs["chapter"]).set_index("label")["accuracy"], color="#38bdf8")
    with tabs[3]:
        if finals.empty:
            st.caption("Needs fully evaluated tests.")
        else:
            tr = finals.sort_values("start_ts")
            tr = tr.assign(Date=tr["start_ts"].map(lambda t: datetime.fromtimestamp(t, tz=IST).strftime("%d %b %H:%M")))
            st.line_chart(tr.set_index("Date")["percentage"], color="#8b5cf6")
    with tabs[4]:
        ts = type_stats(ans)
        if ts.empty:
            st.caption("No checked answers yet.")
        else:
            wide(st.dataframe, ts.round(1), hide_index=True)
            st.bar_chart(ts.set_index("qtype")["accuracy"], color="#34d399")
    with tabs[5]:
        rep = mk[mk["fail_count"] >= 2].sort_values("fail_count", ascending=False) if not mk.empty else mk
        if rep is None or rep.empty:
            st.caption("No question has been missed more than once.")
        else:
            wide(st.dataframe, rep[["subject", "chapter", "fail_count", "reattempt_count", "last_result", "question_text"]], hide_index=True)
    with tabs[6]:
        t = done.copy()
        st.markdown(chip(f"Total time in tests: {fmt_clock(t['time_taken'].sum())}", "blue"), unsafe_allow_html=True)
        by = t.groupby("subject")["time_taken"].sum().div(60).round(1).reset_index().rename(columns={"time_taken": "minutes"})
        wide(st.dataframe, by, hide_index=True)
    with tabs[7]:
        wk = ranked_chapters(cs, True, 5)
        if wk is None or wk.empty:
            st.caption(f"Recommendations appear once a chapter has at least {MIN_Q_FOR_RANK} checked questions.")
        else:
            for r in wk.itertuples():
                st.markdown(f"- **{r.chapter}** ({r.subject}): score {r.score_pct:.0f}% over {r.questions} questions - practise this next.")
            st.button("Build a weak-chapter test", on_click=go, args=("Start a Test",), key="an_weak")
        if not mk.empty:
            top = mk[~mk["revised"]].groupby(["subject", "chapter"]).size().reset_index(name="open mistakes").sort_values("open mistakes", ascending=False).head(5)
            if not top.empty:
                st.markdown("**Chapters with most unrevised mistakes**")
                wide(st.dataframe, top, hide_index=True)


# =============================================================================
# SECTION 29 - SETTINGS & BACKUP
# =============================================================================
def page_settings() -> None:
    page_title("Settings & Backup")
    ok, msg = DB.check()
    st.success(msg)
    st.warning("**Please read:** your data lives in local JSON files on the Streamlit server. Streamlit Community Cloud does NOT guarantee "
               "that local files persist - they can be erased when the app restarts, sleeps for a long time, or is redeployed (e.g. after you push to GitHub). "
               "Download a backup after every study session that matters.")
    st.markdown("#### Backup (recommended habit)")
    st.download_button("Download full backup (JSON, includes images)", build_backup(DB, OWNER),
                       file_name=f"as22122_backup_{datetime.now(IST).strftime('%Y-%m-%d')}.json", mime="application/json", key="bk_full", type="primary")
    st.caption(f"To make your data survive redeploys: rename the downloaded file to **{BACKUP_FILENAME}** and upload it to your GitHub repository next to app.py "
               "(use a PRIVATE repository - it contains your question bank and results). When the app starts with no data, it loads that file automatically.")
    st.markdown("#### Restore from a backup")
    up = st.file_uploader("Upload an as22122 backup (JSON)", type=["json"], key="restore_file")
    if up is not None:
        if st.checkbox("I understand this REPLACES everything currently in the app with the backup contents", key="restore_ack"):
            if st.button("Restore now", type="primary", key="restore_go"):
                try:
                    counts = restore_into_store(DB, up.getvalue(), OWNER)
                    invalidate_cache()
                    st.success("Restored: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
                except (ValueError, StoreError) as e:
                    st.error(str(e))
    st.markdown("#### Export tables (CSV)")
    tables = {"questions": load_questions_df, "chapters": load_chapters_df, "attempts": load_attempts_df, "attempt_answers": load_answers_df, "mistakes": load_mistakes_df}
    cols = st.columns(len(tables))
    for c, (name, fn) in zip(cols, tables.items()):
        df = fn()
        c.download_button(f"{name} ({len(df)})", df_to_csv(df), file_name=f"as22122_{name}.csv", mime="text/csv", key=f"bk_{name}")
    st.markdown("#### Hindi course")
    cur = get_setting("hindi_course", "")
    hc = st.text_input("Course label shown next to Hindi (e.g. Course A / Course B)", value=cur, key="set_hindi")
    if st.button("Save course label", key="set_hindi_save"):
        set_setting("hindi_course", hc.strip())
        st.rerun()
    st.markdown("#### Who can open this app?")
    if password_enabled():
        st.success("A password is set (APP_PASSWORD secret).")
    else:
        st.info("Open access: anyone who has the link can use and change this app's data. Two easy ways to lock it, both inside Streamlit Cloud (no code, no terminal): "
                "(1) App settings -> Sharing -> make it private / invite only your email; or (2) App settings -> Secrets -> add the line `APP_PASSWORD = \"your password\"` and reboot the app.")


# =============================================================================
# SECTION 30 - MAIN ROUTER
# =============================================================================
PAGES = {
    "Dashboard": page_dashboard, "Start a Test": page_builder, "Board Exam Mode": page_board,
    "Results": page_results, "Evaluate Answers": page_eval, "Question Bank": page_bank,
    "Previous-Year Questions": page_pyq, "Subjects & Chapters": page_chapters,
    "Mistake Notebook": page_mistakes, "Analytics": page_analytics, "Settings & Backup": page_settings,
}


def _cached_store():
    return make_store()


if st is not None:
    _cached_store = st.cache_resource(show_spinner=False)(_cached_store)


def main() -> None:
    global DB, OWNER
    st.set_page_config(page_title=f"{APP_NAME} - {TAGLINE}", page_icon="🎓", layout="wide", initial_sidebar_state="expanded")
    inject_css()
    if not auth_gate():
        return
    try:
        OWNER = str(get_secret("OWNER_ID", "owner"))
        DB = _cached_store()
        resume_session_once()
        if st.session_state.get("active_attempt"):
            render_exam()
            return
        render_sidebar()
        PAGES.get(st.session_state.get("page", "Dashboard"), page_dashboard)()
    except StoreError as e:
        st.error(f"Storage problem: {e}")
        st.caption("The app could not read or write its data folder. Try rebooting the app; you can restore from a backup in Settings & Backup.")


# =============================================================================
# SECTION 31 - OPTIONAL SELF-TEST & ENTRY POINT
# =============================================================================
def _cli() -> bool:
    if "--selftest" in sys.argv:
        res = run_selftests()
        for n, ok, d in res:
            print(("PASS  " if ok else "FAIL  ") + n + (f"  -> {d}" if d else ""))
        bad = [r for r in res if not r[1]]
        print(f"\n{len(res) - len(bad)}/{len(res)} tests passed")
        sys.exit(1 if bad else 0)
    return False


if __name__ == "__main__":
    if not _cli() and st is not None:
        main()
