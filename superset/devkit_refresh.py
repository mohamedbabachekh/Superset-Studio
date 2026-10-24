"""Scheduled data refresh: a dashboard opens on data that is already in the cache, refreshed when YOU decide.

THE PROBLEM IT SOLVES (owner, 2026-10-15: « if I click on the dashboard it opens and shows the data, no waiting,
no loading … schedule the caching for each dashboard, and at the right time re-query the database and reload the
new data … easy to configure »). Superset fills its chart cache only when somebody asks: the first person to open
a dashboard after the cache expired — or after a restart, since the kit's Redis keeps nothing on disk — waits for
every query, the CEM's included. Superset's own « cache warm-up » replays a chart's SAVED query, which is not the
query a dashboard sends (its filters, its time range, the native filters' defaults): it warms entries nobody reads.

HOW. Two halves in one file:

  · record (inside Superset, `install(app)` from FLASK_APP_MUTATOR): every chart request a DASHBOARD sends and
    Superset ANSWERED (200) is written down in Redis exactly as it arrived — the body of /api/v1/chart/data, the
    form of the legacy /superset/explore_json — with the dashboard it came from. Same request, same cache key: that
    is what makes a replay fill the entry the page will read. A request for a CSV, the samples or the SQL is not
    recorded; neither is a chart edited in Explore, nor the refresher's own.

  · replay (the `devkit-refresh` container): signs in as its own account and, dashboard by dashboard, follows that
    dashboard's SCHEDULE — a tag set in Superset itself, on the dashboard (Edit properties → Tags):

        refresh:06:30     re-query at 06:30 every day (one tag per time: add refresh:13:00 for a second one;
                          06h30 is understood too); the times are in DEVKIT_TZ
        refresh:30m       re-query every 30 minutes (refresh:2h — every two hours)
        refresh:off       never re-query this one on a schedule

    A dashboard without such a tag follows the kit's default, DEVKIT_REFRESH_EVERY_MIN / DEVKIT_REFRESH_AT in .env.

    BETWEEN TWO REFRESHES THE DATABASE IS NOT ASKED. A cached result would expire on its own after the cache
    timeout (the kit's hour, or a dataset's own — the product's datasets carry 900 s); the refresher instead
    stretches its life in Redis to the next scheduled refresh. The data a reader sees is the one the schedule
    fetched, and the cluster is queried at the times you chose and no others. Only an entry that is GONE (Redis
    full, or restarted) is fetched again at once, since the next reader would otherwise wait for it.

The list survives a restart: the refresher copies it to the kit's home volume after every pass and puts it back
in Redis when Redis comes up empty — the very moment the cache is empty too, and the refresh most worth doing.

    python devkit_refresh.py --status         each dashboard: its schedule, its charts, last and next refresh
    python devkit_refresh.py --now 12         re-query dashboard 12 now (--now all: every dashboard)

What it does NOT do:
  · warm a dashboard nobody has opened yet: what a page asks is learnt by watching it ask. Open a newly scheduled
    dashboard once — and each of its tabs, since a tab's charts are asked only when it is shown;
  · warm the cache of a user with a row-level-security rule: their cache key is their own, and the refresher (an
    admin, without rules) cannot fill it; such a user reads live, as before.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import time
from typing import NamedTuple

REQUESTS_KEY = "devkit_refresh:requests"     # hash: fingerprint -> the recorded request (JSON)
STATE_KEY = "devkit_refresh:state"           # hash: fingerprint -> its cache keys, when its data was read
DASHBOARDS_KEY = "devkit_refresh:dashboards" # hash: dashboard id -> what --status prints
NOW_KEY = "devkit_refresh:now"               # set: dashboard ids (or « all ») to re-query at the next look
DATA_PREFIX = "devkit_data_"                 # superset_config.py's DATA_CACHE_CONFIG prefix
REFRESH_HEADER = "X-Devkit-Refresh"          # the refresher's own requests are not recorded
REFRESH_USER = "devkit-refresh"
MAX_BODY = 512 * 1024                        # a recorded request larger than this is not kept


def refresh_password(secret_key: str) -> str:
    """The refresher's password, derived from the kit's secret: written nowhere, and the same in devkit-init (which
    sets it) and in the refresher (which signs in with it). Changing the admin's password does not touch it."""
    return hmac.new(secret_key.encode(), b"devkit-refresh-account", hashlib.sha256).hexdigest()


# ── record: inside Superset ──────────────────────────────────────────────────────────────────────────────────────

def _fingerprint(kind: str, payload: object) -> str:
    return hashlib.sha1(f"{kind}\n{json.dumps(payload, sort_keys=True)}".encode()).hexdigest()


def _dashboard_of(args: dict, form_data: object) -> int | None:
    """The dashboard a chart request came from: the page puts it in the address, and in the form data."""
    for value in (args.get("dashboard_id"), (form_data or {}).get("dashboardId") if isinstance(form_data, dict) else None):
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def install(app) -> None:  # noqa: ANN001
    """Record every answered dashboard chart request (see the module's docstring). Never fails the request."""
    import redis
    from flask import request

    store = redis.Redis.from_url(os.environ["SUPERSET_REDIS_URL"])

    @app.after_request
    def _record(response):  # noqa: ANN001, ANN202
        try:
            if request.method != "POST" or response.status_code != 200 or request.headers.get(REFRESH_HEADER):
                return response
            path = request.path.rstrip("/")
            args = {k: v for k, v in request.args.items() if k != "force"}
            if path == "/api/v1/chart/data":
                raw = request.get_data(cache=True)
                if not raw or len(raw) > MAX_BODY:
                    return response
                body = json.loads(raw)
                if body.get("result_format", "json") != "json" or body.get("result_type", "full") != "full":
                    return response
                body.pop("force", None)
                dash = _dashboard_of(args, body.get("form_data"))
                entry = {"kind": "data", "path": request.path, "args": args, "body": body}
            elif path.startswith("/superset/explore_json") and "/data/" not in path:
                if any(request.args.get(k) in ("true", "1") for k in ("csv", "query", "results", "samples")):
                    return response
                form = {k: v for k, v in request.form.items() if k != "force"}
                if sum(len(v) for v in form.values()) > MAX_BODY:
                    return response
                try:
                    fd = json.loads(form.get("form_data") or request.args.get("form_data") or "{}")
                except ValueError:
                    fd = {}
                dash = _dashboard_of(args, fd)
                entry = {"kind": "legacy", "path": request.path, "args": args, "form": form}
            else:
                return response
            if dash is None:
                return response          # Explore, SQL Lab's charts: somebody is drawing, not reading
            fp = _fingerprint(entry["kind"], [entry["path"], entry.get("body"), entry.get("form")])
            entry.update(seen=int(time.time()), dash=dash)
            store.hset(REQUESTS_KEY, fp, json.dumps(entry))
        except Exception as exc:  # noqa: BLE001 — recording is a convenience; the page's answer is not at stake
            app.logger.warning("devkit refresh: request not recorded: %s", exc)
        return response


# ── schedules ────────────────────────────────────────────────────────────────────────────────────────────────────

class Schedule(NamedTuple):
    every: int                 # seconds between two refreshes, 0 for none
    at: tuple[str, ...]        # times of day, HH:MM
    source: str                # « tag » or « default »

    @property
    def off(self) -> bool:
        return not self.every and not self.at

    def text(self) -> str:
        if self.at:
            return "at " + ", ".join(self.at)
        if self.every:
            return f"every {self.every // 3600} h" if self.every % 3600 == 0 else f"every {self.every // 60} min"
        return "off"


_TIME = re.compile(r"([01]?\d|2[0-3])\s*[:hH]\s*([0-5]\d)")
_EVERY = re.compile(r"(\d+)\s*(m|min|mn|minutes?|h|hours?|heures?)")


def parse_schedule(values: list[str], source: str) -> tuple[Schedule, list[str]]:
    """« 06:30 », « 06h30 », « 30m », « 2h », « off » → a schedule, and the values it could not read."""
    every, at, bad = 0, set(), []
    for v in values:
        v = v.strip().lower()
        if not v:
            continue
        if v in ("off", "none", "non", "0"):
            return Schedule(0, (), source), bad
        if m := _TIME.fullmatch(v):
            at.add(f"{int(m.group(1)):02d}:{m.group(2)}")
        elif m := _EVERY.fullmatch(v):
            n = int(m.group(1)) * (3600 if m.group(2).startswith("h") else 60)
            every = min(every, n) if every else n
        else:
            bad.append(v)
    return Schedule(every if not at else 0, tuple(sorted(at)), source), bad


def default_schedule() -> Schedule:
    at = [t for t in os.environ.get("DEVKIT_REFRESH_AT", "").split(",") if t.strip()]
    if at:
        sched, bad = parse_schedule(at, "default")
        if bad:
            sys.exit(f"DEVKIT_REFRESH_AT: not a time of day (HH:MM): {', '.join(bad)}")
        return sched
    return Schedule(_env_int("DEVKIT_REFRESH_EVERY_MIN", 60) * 60, (), "default")


def _slot(at: tuple[str, ...], now: dt.datetime, forward: bool) -> dt.datetime | None:
    """The last scheduled time already passed (forward=False), or the next one to come (forward=True)."""
    if not at:
        return None
    days = (now.date(), now.date() + dt.timedelta(days=1)) if forward else (now.date(), now.date() - dt.timedelta(days=1))
    best = None
    for day in days:
        for t in at:
            h, m = map(int, t.split(":"))
            when = dt.datetime.combine(day, dt.time(h, m)).astimezone()
            if (when > now if forward else when <= now) and (best is None or (when < best if forward else when > best)):
                best = when
    return best


def next_refresh(sched: Schedule, read_at: float, now: float) -> float | None:
    if sched.at:
        return _slot(sched.at, dt.datetime.fromtimestamp(now).astimezone(), forward=True).timestamp()
    if sched.every:
        return max(read_at + sched.every, now)
    return None


# ── replay: the devkit-refresh container ─────────────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _log(msg: str) -> None:
    print(f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


def _when(ts: float | None) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M") if ts else "—"


class Refresher:
    TICK = 60          # seconds between two passes
    SCHEDULES_EVERY = 120  # seconds between two readings of the dashboards' tags
    MARGIN = 900       # a stretched entry lives this long past its next refresh, in case that refresh is late

    def __init__(self) -> None:
        import redis
        import requests

        self.base = os.environ.get("DEVKIT_SUPERSET_URL", "http://superset-devkit:8088").rstrip("/")
        self.store = redis.Redis.from_url(os.environ["SUPERSET_REDIS_URL"], decode_responses=True)
        self.password = refresh_password(os.environ["SUPERSET_SECRET_KEY"])
        self.default = default_schedule()
        self.keep_days = _env_int("DEVKIT_REFRESH_KEEP_DAYS", 7)
        self.keep_days_tagged = _env_int("DEVKIT_REFRESH_KEEP_DAYS_SCHEDULED", 30)
        self.parallel = max(1, _env_int("DEVKIT_REFRESH_PARALLEL", 2))
        self.max_entries = _env_int("DEVKIT_REFRESH_MAX", 2000)
        self.timeout = _env_int("DEVKIT_SQLLAB_TIMEOUT", 300) + 30
        self.saved = os.environ.get("DEVKIT_REFRESH_FILE", "/app/superset_home/devkit-refresh/requests.json")
        self.session = requests.Session()
        self.session.headers[REFRESH_HEADER] = "1"
        # per recorded request: its cache keys, when its data was read, when it last failed (kept in Redis too, so
        # a restart of this container does not ask the database again for what is already in the cache)
        self.state: dict[str, dict] = {}
        for fp, v in self.store.hgetall(STATE_KEY).items():
            try:
                self.state[fp] = json.loads(v)
            except ValueError:
                pass
        self.dashboards: dict[int, dict] = {}   # id -> {title, schedule}
        self.schedules_read = 0.0

    # ── signing in ──
    def sign_in(self) -> None:
        s = self.session
        s.cookies.clear()
        page = s.get(f"{self.base}/login/", timeout=30)
        m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text) or \
            re.search(r'id="csrf_token"[^>]*value="([^"]+)"', page.text)
        r = s.post(f"{self.base}/login/", timeout=30, allow_redirects=False,
                   data={"username": REFRESH_USER, "password": self.password, "csrf_token": m.group(1) if m else ""})
        me = s.get(f"{self.base}/api/v1/me/", timeout=30)
        if me.status_code != 200:
            raise RuntimeError(f"cannot sign in as {REFRESH_USER} (login {r.status_code}, /me {me.status_code}) — "
                               "devkit-init creates the account: did it run with this DEVKIT_SECRET_KEY?")

    # ── the dashboards and their tags ──
    def read_schedules(self) -> None:
        found, page = {}, 0
        while True:
            q = f"(columns:!(id,dashboard_title,tags.name),page:{page},page_size:100)"
            r = self.session.get(f"{self.base}/api/v1/dashboard/", params={"q": q}, timeout=60)
            if r.status_code in (401, 403):
                raise PermissionError("signed out")
            r.raise_for_status()
            rows = r.json().get("result", [])
            for d in rows:
                values = [t["name"].split(":", 1)[1] for t in d.get("tags") or []
                          if t.get("name", "").lower().replace(" ", "").startswith("refresh:")]
                if values:
                    sched, bad = parse_schedule(values, "tag")
                    if bad:
                        _log(f"dashboard {d['id']} « {d['dashboard_title']} »: tag(s) not understood, ignored: "
                             + ", ".join(f"refresh:{b}" for b in bad) + " — write refresh:06:30, refresh:30m or refresh:off")
                    if not sched.every and not sched.at and bad:
                        sched = self.default
                else:
                    sched = self.default
                found[d["id"]] = {"title": d["dashboard_title"], "schedule": sched}
            if len(rows) < 100:
                break
            page += 1
        changed = [i for i in found if i in self.dashboards and found[i]["schedule"] != self.dashboards[i]["schedule"]]
        for i in changed:
            _log(f"dashboard {i} « {found[i]['title']} »: schedule now {found[i]['schedule'].text()}")
        self.dashboards = found
        self.schedules_read = time.time()

    def schedule_of(self, entry: dict) -> Schedule:
        d = self.dashboards.get(entry.get("dash"))
        return d["schedule"] if d else self.default

    # ── the list ──
    def load(self) -> dict[str, dict]:
        raw = self.store.hgetall(REQUESTS_KEY)
        if not raw and os.path.exists(self.saved):
            with open(self.saved, encoding="utf-8") as f:
                kept = json.load(f)
            if kept:
                self.store.hset(REQUESTS_KEY, mapping={k: json.dumps(v) for k, v in kept.items()})
                _log(f"the cache was empty: {len(kept)} recorded request(s) put back from {self.saved}")
                raw = self.store.hgetall(REQUESTS_KEY)
        entries = {}
        for fp, v in raw.items():
            try:
                e = json.loads(v)
            except ValueError:
                e = None
            if not isinstance(e, dict) or e.get("dash") is None:
                self.store.hdel(REQUESTS_KEY, fp)   # unreadable, or recorded before requests named their dashboard
                continue
            entries[fp] = e
        # forget what nobody opened for a while (longer for a dashboard somebody scheduled), what belongs to a
        # dashboard that no longer exists, and the oldest beyond MAX
        now = time.time()
        old = []
        for fp, e in entries.items():
            sched = self.schedule_of(e)
            days = self.keep_days_tagged if sched.source == "tag" else self.keep_days
            if e.get("seen", 0) < now - days * 86400 or (self.dashboards and e["dash"] not in self.dashboards):
                old.append(fp)
        ranked = sorted((fp for fp in entries if fp not in old), key=lambda fp: -entries[fp].get("seen", 0))
        old += ranked[self.max_entries:]
        if old:
            self.store.hdel(REQUESTS_KEY, *old)
            self.store.hdel(STATE_KEY, *old)
            for fp in old:
                entries.pop(fp, None)
                self.state.pop(fp, None)
        return entries

    def save(self, entries: dict[str, dict]) -> None:
        os.makedirs(os.path.dirname(self.saved), exist_ok=True)
        tmp = f"{self.saved}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        os.replace(tmp, self.saved)

    # ── one request ──
    def ask(self, entry: dict, force: bool) -> tuple[list[str], float]:
        """Send the recorded request; return its cache keys and the age of the data it answered with (seconds)."""
        url = f"{self.base}{entry['path']}"
        params = dict(entry.get("args") or {})
        if entry["kind"] == "data":
            r = self.session.post(url, params=params, json=dict(entry["body"], force=force), timeout=self.timeout)
        else:
            if force:
                params["force"] = "true"
            r = self.session.post(url, params=params, data=entry["form"], timeout=self.timeout)
        if r.status_code in (401, 403) and "login" in r.url:
            raise PermissionError("signed out")
        r.raise_for_status()
        out = r.json()
        results = out.get("result") if entry["kind"] == "data" else [out]
        keys, ages = [], []
        for res in results or []:
            if res.get("error"):
                raise RuntimeError(str(res["error"])[:200])
            if res.get("cache_key"):
                keys.append(res["cache_key"])
            if res.get("cached_dttm"):
                try:
                    when = dt.datetime.fromisoformat(str(res["cached_dttm"]).replace("Z", "+00:00"))
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=dt.timezone.utc)
                    ages.append((dt.datetime.now(dt.timezone.utc) - when).total_seconds())
                except ValueError:
                    pass
        return keys, (max(ages) if ages else 0.0)

    def ttl_left(self, keys: list[str]) -> int:
        """Seconds the shortest-lived of these cache entries has left; -2 when one is gone, -1 when it never ends."""
        lefts = [self.store.ttl(DATA_PREFIX + k) for k in keys]
        if not lefts or any(t == -2 for t in lefts):
            return -2
        finite = [t for t in lefts if t >= 0]
        return min(finite) if finite else -1

    def stretch(self, fp: str, sched: Schedule, now: float) -> None:
        """Keep the entry in the cache until its next refresh (and MARGIN beyond): no query in between."""
        st = self.state.get(fp) or {}
        nxt = next_refresh(sched, st.get("read_at", now), now)
        if nxt is None:
            return
        want = int(nxt - now + self.MARGIN)
        for k in st.get("keys", []):
            left = self.store.ttl(DATA_PREFIX + k)
            if 0 <= left < want:
                self.store.expire(DATA_PREFIX + k, want)

    # ── when is an entry due ──
    def due(self, fp: str, sched: Schedule, now: float) -> str | None:
        if sched.off:
            return None                                   # refresh:off — the refresher leaves it entirely alone
        st = self.state.get(fp)
        if st is None:
            return "first look"
        if st.get("failed_at") and now - st["failed_at"] < 600:
            return None                                   # failed less than ten minutes ago: leave it a while
        if self.ttl_left(st.get("keys", [])) == -2:
            return "gone from the cache"
        read_at = st.get("read_at", 0)
        if sched.at:
            last = _slot(sched.at, dt.datetime.fromtimestamp(now).astimezone(), forward=False)
            return "scheduled time" if last and read_at < last.timestamp() else None
        if sched.every and now - read_at >= sched.every:
            return "schedule"
        return None

    def run_one(self, fp: str, entry: dict, why: str, sched: Schedule) -> tuple[str, str, float]:
        t0 = time.time()
        st = self.state.setdefault(fp, {})
        try:
            if why == "first look":
                # usually a reader has just filled it: look without forcing, refresh only if it is out of date
                keys, age = self.ask(entry, force=False)
                st.update(keys=keys, read_at=time.time() - age, failed_at=None)
                if not self.due(fp, sched, time.time()):
                    return fp, "read", time.time() - t0
            keys, _ = self.ask(entry, force=True)
            st.update(keys=keys, read_at=time.time(), failed_at=None, error=None)
            return fp, "refreshed", time.time() - t0
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001
            st.update(failed_at=time.time(), error=str(exc)[:160])
            return fp, f"failed: {str(exc)[:160]}", time.time() - t0
        finally:
            self.store.hset(STATE_KEY, fp, json.dumps(st))

    def pass_once(self) -> None:
        if time.time() - self.schedules_read > self.SCHEDULES_EVERY:
            self.read_schedules()
        entries = self.load()
        now = time.time()
        asked = set(self.store.smembers(NOW_KEY))
        if asked:
            self.store.srem(NOW_KEY, *asked)
            _log(f"asked to re-query now: {', '.join(sorted(asked))}")
        todo = []
        for fp, e in entries.items():
            sched = self.schedule_of(e)
            why = "asked now" if ("all" in asked or str(e["dash"]) in asked) else self.due(fp, sched, now)
            if why:
                todo.append((fp, e, why, sched))
        if todo:
            done, failed, slow = 0, [], 0.0
            with concurrent.futures.ThreadPoolExecutor(self.parallel) as pool:
                for fp, outcome, took in pool.map(lambda a: self.run_one(*a), todo):
                    slow = max(slow, took)
                    if outcome.startswith("failed"):
                        failed.append(outcome)
                    elif outcome == "refreshed":
                        done += 1
            dashes = sorted({e["dash"] for _, e, _, _ in todo})
            reasons = sorted({why for _, _, why, _ in todo})
            _log(f"{len(todo)} chart request(s) of dashboard(s) {', '.join(map(str, dashes))} ({', '.join(reasons)}): "
                 f"{done} re-queried, {len(todo) - done - len(failed)} already in the cache, {len(failed)} failed; "
                 f"slowest {slow:.1f} s")
            for f in failed[:5]:
                _log(f"  {f}")
        now = time.time()
        for fp, e in entries.items():
            self.stretch(fp, self.schedule_of(e), now)
        self.summarise(entries, now)
        self.save(entries)

    def summarise(self, entries: dict[str, dict], now: float) -> None:
        per: dict[int, dict] = {}
        for fp, e in entries.items():
            st = self.state.get(fp, {})
            d = per.setdefault(e["dash"], {"charts": 0, "cached": 0, "oldest": None, "failed": 0, "error": None})
            d["charts"] += 1
            if self.ttl_left(st.get("keys", [])) != -2:
                d["cached"] += 1
            if st.get("read_at"):
                d["oldest"] = min(d["oldest"] or st["read_at"], st["read_at"])
            if st.get("failed_at"):
                d["failed"] += 1
                d["error"] = st.get("error")
        rows = {}
        for i, info in self.dashboards.items():
            sched = info["schedule"]
            d = per.get(i, {"charts": 0, "cached": 0, "oldest": None, "failed": 0, "error": None})
            nxt = next_refresh(sched, d["oldest"] or now, now) if d["charts"] else None
            rows[str(i)] = json.dumps({"title": info["title"], "schedule": sched.text(), "source": sched.source,
                                       "charts": d["charts"], "cached": d["cached"], "data_from": d["oldest"],
                                       "next": nxt, "failed": d["failed"], "error": d["error"]})
        pipe = self.store.pipeline()
        pipe.delete(DASHBOARDS_KEY)
        if rows:
            pipe.hset(DASHBOARDS_KEY, mapping=rows)
        pipe.hset(DASHBOARDS_KEY, "_at", json.dumps({"at": now, "default": self.default.text()}))
        pipe.execute()

    def run(self) -> None:
        _log(f"devkit refresh: a dashboard follows its tag (refresh:06:30, refresh:30m, refresh:off), otherwise the "
             f"default — {self.default.text()}; between two refreshes the cache is kept and the database is not "
             f"asked; {self.parallel} queries at a time")
        while True:
            try:
                self.sign_in()
                break
            except Exception as exc:  # noqa: BLE001 — Superset may still be starting
                _log(f"waiting for Superset: {exc}")
                time.sleep(30)
        while True:
            try:
                self.pass_once()
            except PermissionError:
                self.sign_in()
                continue
            except Exception as exc:  # noqa: BLE001
                _log(f"pass failed: {exc}")
            # wake early when somebody asks for a refresh now
            for _ in range(self.TICK // 5):
                if self.store.scard(NOW_KEY):
                    break
                time.sleep(5)


# ── the two commands a person types ──────────────────────────────────────────────────────────────────────────────

def status() -> None:
    import redis

    store = redis.Redis.from_url(os.environ["SUPERSET_REDIS_URL"], decode_responses=True)
    raw = store.hgetall(DASHBOARDS_KEY)
    meta = json.loads(raw.pop("_at", "{}") or "{}")
    if not raw:
        print("no status yet — the refresher writes it after its first pass (a minute after it starts)")
        return
    print(f"default schedule (.env): {meta.get('default', '?')}    status of {_when(meta.get('at'))}")
    print(f"{'id':>4}  {'dashboard':<40} {'schedule':<22} {'charts':>6} {'cached':>6}  {'data from':<11} {'next':<11} problems")
    for i, v in sorted(raw.items(), key=lambda kv: int(kv[0])):
        d = json.loads(v)
        sched = d["schedule"] + (" (tag)" if d["source"] == "tag" else "")
        charts = d["charts"] or "—"
        problems = f"{d['failed']} failed: {d['error']}" if d["failed"] else ("open it once" if not d["charts"] else "")
        print(f"{i:>4}  {d['title'][:40]:<40} {sched:<22} {charts!s:>6} {d['cached']:>6}  {_when(d['data_from']):<11} "
              f"{_when(d['next']):<11} {problems}")


def ask_now(which: str) -> None:
    import redis

    store = redis.Redis.from_url(os.environ["SUPERSET_REDIS_URL"], decode_responses=True)
    if which != "all" and not which.isdigit():
        sys.exit("--now takes a dashboard id (see --status) or « all »")
    store.sadd(NOW_KEY, which)
    print(f"asked: dashboard {which} is re-queried within a few seconds — "
          "docker compose -p sharpsight-superset-devkit logs -f devkit-refresh shows it")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    elif "--now" in sys.argv:
        i = sys.argv.index("--now")
        ask_now(sys.argv[i + 1] if len(sys.argv) > i + 1 else "all")
    else:
        Refresher().run()
