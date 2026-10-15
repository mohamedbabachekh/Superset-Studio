"""Scheduled data refresh: a dashboard opens on data that is already in the cache.

THE PROBLEM IT SOLVES (owner, 2026-10-15: « the dashboard reads first time from the cached data and not each time
pull from the db »). Superset fills its chart cache only when somebody asks: the first person to open a dashboard
after the cache expired — or after a restart, since the kit's Redis keeps nothing on disk — waits for every query,
the CEM's included. Superset's own « cache warm-up » replays a chart's SAVED query, which is not the query a
dashboard sends (its filters, its time range, the native filters' defaults), so it warms entries nobody reads.

HOW. Two halves in one file:

  · record (inside Superset, `install(app)` from FLASK_APP_MUTATOR): every chart request a page sends and Superset
    ANSWERED (200) is written down in Redis exactly as it arrived — the body of /api/v1/chart/data, the form of the
    legacy /superset/explore_json. Same request, same cache key: that is what makes a replay warm the entry the
    page will read. A request for a CSV, the samples or the SQL is not recorded; neither is the refresher's own.

  · replay (the `devkit-refresh` container, `python devkit_refresh.py`): signs in as its own account and, for every
    request recorded in the last DEVKIT_REFRESH_KEEP_DAYS days, asks again with `force` — new data, written over
    the same cache entry — when the data is older than DEVKIT_REFRESH_EVERY_MIN minutes (or at the times of
    DEVKIT_REFRESH_AT), and ALSO before the entry expires, so it never falls out of the cache between two
    refreshes. A dataset may carry a cache timeout shorter than the schedule (the product's datasets carry 900 s):
    those entries are kept alive at their own pace, and the log says which.

The list survives a restart: the refresher copies it to the kit's home volume after every pass and puts it back
in Redis when Redis comes up empty — the very moment the cache is empty too, and the refresh most worth doing.

What it does NOT do: a user with a row-level-security rule has a cache key of their own, and the refresher (an
admin, without rules) cannot warm it; such a user's dashboard is refreshed for everybody else and read live for
them, as before.
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

REQUESTS_KEY = "devkit_refresh:requests"     # hash: fingerprint -> the recorded request (JSON)
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


def install(app) -> None:  # noqa: ANN001
    """Record every answered chart request (see the module's docstring). Never fails the request it looks at."""
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
                entry = {"kind": "data", "path": request.path, "args": args, "body": body}
            elif path.startswith("/superset/explore_json") and "/data/" not in path:
                if any(request.args.get(k) in ("true", "1") for k in ("csv", "query", "results", "samples")):
                    return response
                form = {k: v for k, v in request.form.items() if k != "force"}
                if sum(len(v) for v in form.values()) > MAX_BODY:
                    return response
                entry = {"kind": "legacy", "path": request.path, "args": args, "form": form}
            else:
                return response
            fp = _fingerprint(entry["kind"], [entry["path"], entry.get("body"), entry.get("form")])
            entry["seen"] = int(time.time())
            store.hset(REQUESTS_KEY, fp, json.dumps(entry))
        except Exception as exc:  # noqa: BLE001 — recording is a convenience; the page's answer is not at stake
            app.logger.warning("devkit refresh: request not recorded: %s", exc)
        return response


# ── replay: the devkit-refresh container ─────────────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _log(msg: str) -> None:
    print(f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}", flush=True)


class Refresher:
    TICK = 60          # seconds between two looks at the list
    MARGIN = 300       # an entry is refreshed when it has less than this left to live

    def __init__(self) -> None:
        import redis
        import requests

        self.base = os.environ.get("DEVKIT_SUPERSET_URL", "http://superset-devkit:8088").rstrip("/")
        self.store = redis.Redis.from_url(os.environ["SUPERSET_REDIS_URL"], decode_responses=True)
        self.password = refresh_password(os.environ["SUPERSET_SECRET_KEY"])
        self.every = _env_int("DEVKIT_REFRESH_EVERY_MIN", 60) * 60
        self.at = sorted({t.strip() for t in os.environ.get("DEVKIT_REFRESH_AT", "").split(",") if t.strip()})
        bad = [t for t in self.at if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", t)]
        if bad:
            sys.exit(f"DEVKIT_REFRESH_AT: not a time of day (HH:MM): {', '.join(bad)}")
        self.keep_days = _env_int("DEVKIT_REFRESH_KEEP_DAYS", 7)
        self.parallel = max(1, _env_int("DEVKIT_REFRESH_PARALLEL", 2))
        self.max_entries = _env_int("DEVKIT_REFRESH_MAX", 1000)
        self.timeout = _env_int("DEVKIT_SQLLAB_TIMEOUT", 300) + 30
        self.saved = os.environ.get("DEVKIT_REFRESH_FILE", "/app/superset_home/devkit-refresh/requests.json")
        self.session = requests.Session()
        self.session.headers[REFRESH_HEADER] = "1"
        # what this process knows about each entry: its cache keys, when its data was read, when it last failed
        self.state: dict[str, dict] = {}
        self.short_said = 0       # said once, and again only when the number changes

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
                entries[fp] = json.loads(v)
            except ValueError:
                self.store.hdel(REQUESTS_KEY, fp)
        # forget what nobody opened for KEEP_DAYS, and the oldest beyond MAX
        horizon = time.time() - self.keep_days * 86400
        old = [fp for fp, e in entries.items() if e.get("seen", 0) < horizon]
        ranked = sorted((fp for fp in entries if fp not in old), key=lambda fp: -entries[fp].get("seen", 0))
        old += ranked[self.max_entries:]
        if old:
            self.store.hdel(REQUESTS_KEY, *old)
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
    def ask(self, entry: dict, force: bool) -> tuple[list[str], float | None]:
        """Send the recorded request; return its cache keys and the age of the data it answered with (seconds)."""
        url = f"{self.base}{entry['path']}"
        params = dict(entry.get("args") or {})
        if entry["kind"] == "data":
            body = dict(entry["body"], force=force)
            r = self.session.post(url, params=params, json=body, timeout=self.timeout)
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
            elif res.get("error"):
                raise RuntimeError(str(res["error"])[:200])
        return keys, (max(ages) if ages else 0.0)

    # ── when is an entry due ──
    def last_slot(self, now: dt.datetime) -> float | None:
        """The most recent DEVKIT_REFRESH_AT time already passed (epoch), or None when no times are set."""
        if not self.at:
            return None
        best = None
        for day in (now.date(), now.date() - dt.timedelta(days=1)):
            for t in self.at:
                h, m = map(int, t.split(":"))
                when = dt.datetime.combine(day, dt.time(h, m)).astimezone()
                if when <= now and (best is None or when > best):
                    best = when
        return best.timestamp() if best else None

    def ttl_left(self, keys: list[str]) -> int:
        """Seconds the shortest-lived of these cache entries has left; -2 when one is gone, -1 when it never ends."""
        lefts = [self.store.ttl(DATA_PREFIX + k) for k in keys]
        if not lefts or any(t == -2 for t in lefts):
            return -2
        finite = [t for t in lefts if t >= 0]
        return min(finite) if finite else -1

    def due(self, fp: str, now: float, slot: float | None) -> str | None:
        st = self.state.get(fp)
        if st is None:
            return "first look"
        if st.get("failed_at") and now - st["failed_at"] < 600:
            return None                                   # failed less than ten minutes ago: leave it a while
        left = self.ttl_left(st.get("keys", []))
        if left == -2:
            return "gone from the cache"
        if 0 <= left < self.MARGIN + self.TICK:
            return "about to expire"
        read_at = st.get("read_at", 0)
        if slot is not None:
            return "scheduled time" if read_at < slot else None
        if self.every > 0 and now - read_at >= self.every:
            return "schedule"
        return None

    def run_one(self, fp: str, entry: dict, why: str) -> tuple[str, str, float]:
        t0 = time.time()
        st = self.state.setdefault(fp, {})
        try:
            if why == "first look":
                # the cache may already hold it (the refresher restarted, Superset did not): look without forcing,
                # and refresh only if what is there is older than the schedule allows
                keys, age = self.ask(entry, force=False)
                st.update(keys=keys, read_at=time.time() - (age or 0), failed_at=None)
                slot = self.last_slot(dt.datetime.now().astimezone())
                stale = (st["read_at"] < slot) if slot is not None else (self.every > 0 and age and age >= self.every)
                if not stale:
                    return fp, "read", time.time() - t0
            keys, _ = self.ask(entry, force=True)
            st.update(keys=keys, read_at=time.time(), failed_at=None)
            return fp, "refreshed", time.time() - t0
        except PermissionError:
            raise
        except Exception as exc:  # noqa: BLE001
            st["failed_at"] = time.time()
            return fp, f"failed: {str(exc)[:160]}", time.time() - t0

    def pass_once(self) -> None:
        entries = self.load()
        now = time.time()
        slot = self.last_slot(dt.datetime.now().astimezone())
        todo = [(fp, e, why) for fp, e in entries.items() if (why := self.due(fp, now, slot))]
        if todo:
            done, failed, slow = 0, [], 0.0
            with concurrent.futures.ThreadPoolExecutor(self.parallel) as pool:
                for fp, outcome, took in pool.map(lambda a: self.run_one(*a), todo):
                    slow = max(slow, took)
                    if outcome.startswith("failed"):
                        failed.append(outcome)
                    elif outcome == "refreshed":
                        done += 1
            reasons = sorted({why for _, _, why in todo})
            _log(f"{len(todo)} of {len(entries)} recorded request(s) due ({', '.join(reasons)}): {done} refreshed, "
                 f"{len(todo) - done - len(failed)} already fresh, {len(failed)} failed; slowest {slow:.1f} s")
            for f in failed[:5]:
                _log(f"  {f}")
            short = [fp for fp in entries if 0 <= self.ttl_left(self.state.get(fp, {}).get("keys", [])) <
                     min(self.every or 10**9, 86400) - self.MARGIN]
            if short and (self.every or self.at) and len(short) != self.short_said:
                _log(f"  {len(short)} cached result(s) expire sooner than the schedule — their dataset (or connection) "
                     "has its own, shorter cache timeout: they are refreshed at that pace, before they expire")
            self.short_said = len(short)
        self.save(entries)

    def run(self, once: bool = False) -> None:
        sched = (f"at {', '.join(self.at)}" if self.at else
                 f"every {self.every // 60} min" if self.every else "never (DEVKIT_REFRESH_EVERY_MIN=0)")
        _log(f"devkit refresh: data refreshed {sched}, cache kept alive in between; requests opened in the last "
             f"{self.keep_days} day(s) are kept; {self.parallel} at a time")
        while True:
            try:
                self.sign_in()
                break
            except Exception as exc:  # noqa: BLE001 — Superset may still be starting
                _log(f"waiting for Superset: {exc}")
                if once:
                    raise
                time.sleep(30)
        while True:
            try:
                self.pass_once()
            except PermissionError:
                self.sign_in()
                continue
            except Exception as exc:  # noqa: BLE001
                _log(f"pass failed: {exc}")
            if once:
                return
            time.sleep(self.TICK)


if __name__ == "__main__":
    if os.environ.get("DEVKIT_REFRESH_EVERY_MIN", "").strip() == "0" and not os.environ.get("DEVKIT_REFRESH_AT"):
        _log("devkit refresh is OFF (DEVKIT_REFRESH_EVERY_MIN=0 and no DEVKIT_REFRESH_AT): nothing to do")
        while True:
            time.sleep(3600)
    Refresher().run(once="--once" in sys.argv)
