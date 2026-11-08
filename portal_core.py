"""Analysis, snapshot archive and mover tracking for Business Scrying Portal (standard library only).

A "row" is one company: [id, rating, days_old, hired, capacity, daily_income, weekly_income,
daily_customers, weekly_customers, applications_open, type_id].
"""
import bisect
import gzip
import json
import math
import os
import time
from pathlib import Path

ID, RAT, AGE, HIRED, CAP, DINC, WINC, DCUS, WCUS, APPS, TYPE = range(11)
FIELDS = ["id", "rating", "days_old", "hired", "cap", "dInc", "wInc", "dCust", "wCust", "apps", "type"]
P5 = (.05, .25, .5, .75, .95)


def jsr(x):
    """JavaScript Math.round (ties toward +infinity), so output matches the first release exactly."""
    return int(math.floor(x + 0.5))


def q(vals, p):
    if not vals:
        return 0
    a = sorted(vals)
    i = (len(a) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    return a[lo] + (a[hi] - a[lo]) * (i - lo)


def best(A, B, i):
    """Cut that best separates higher group A from lower group B (balanced accuracy) plus AUC."""
    a = sorted(r[i] for r in A)
    b = sorted(r[i] for r in B)
    seen = dict.fromkeys(a)
    for v in b:
        seen.setdefault(v)
    bt, bb = 0, -1.0
    na, nb = len(a), len(b)
    for t in seen:
        ba = ((na - bisect.bisect_left(a, t)) / na + bisect.bisect_left(b, t) / nb) / 2
        if ba > bb:
            bb, bt = ba, t
    gt = eq = 0
    for x in a:
        lo = bisect.bisect_left(b, x)
        hi = bisect.bisect_right(b, x)
        gt += lo
        eq += hi - lo
    return bt, (gt + 0.5 * eq) / (na * nb)


def K(v):
    return jsr(v / 100) / 10 if v < 10000 else jsr(v / 1000)


def _ranks(a):
    idx = sorted(range(len(a)), key=lambda i: a[i])
    r = [0.0] * len(a)
    i = 0
    while i < len(idx):
        j = i
        while j + 1 < len(idx) and a[idx[j + 1]] == a[idx[i]]:
            j += 1
        av = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[idx[k]] = av
        i = j + 1
    return r


def spear(x, y):
    if len(x) < 8:
        return None
    rx, ry = _ranks(x), _ranks(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sxx = sum((v - mx) ** 2 for v in rx)
    syy = sum((v - my) ** 2 for v in ry)
    return jsr(100 * sxy / math.sqrt(sxx * syy)) if sxx and syy else None


def analyze_type(tid, name, rows, ts):
    """Return everything the page needs for one company type."""
    by = {s: [] for s in range(11)}
    for r in rows:
        if 0 <= r[RAT] <= 10:
            by[r[RAT]].append(r)
    rated_rows = [r for r in rows if r[RAT] > 0]
    sel = {}

    def add(r, t):
        if r[ID] not in sel:
            sel[r[ID]] = (r, t)

    packed = [[len(by[0])]]
    n_arr, cw_arr, xd = [], [], []
    for s in range(1, 11):
        R = by[s]
        if not R:
            packed.append([0]); n_arr.append(0); cw_arr.append(0); xd.append([0])
            continue
        lower = by[s - 1]
        cw = best(R, lower, WINC) if lower else (0, 0)
        cc = best(R, lower, WCUS) if lower else (0, 0)
        has_next = s < 10 and by[s + 1]
        nxt = best(by[s + 1], R, WINC)[0] if has_next else math.inf
        gate = q([x[AGE] for x in by[s + 1]], .05) if has_next else 0
        f = lambda i: [x[i] for x in R]
        rk = [sum(1 for r in R if r[WINC] < cw[0]), sum(1 for r in R if r[WINC] < cw[0] * .8),
              sum(1 for r in R if r[WINC] >= nxt and r[AGE] >= gate), sum(1 for r in R if r[WINC] >= nxt and r[AGE] < gate)]
        cw_k = jsr(cw[0] / 1000)
        packed.append([len(R), [jsr(q(f(WINC), p) / 1000) for p in P5], [jsr(q(f(WCUS), p)) for p in P5],
                       jsr(q(f(AGE), 0)), [cw_k, jsr(cw[1] * 1000)], [jsr(cc[0]), jsr(cc[1] * 1000)], rk])
        n_arr.append(len(R)); cw_arr.append(cw_k)
        if s >= 2 and cw[0] > 0:
            for r in sorted([r for r in R if r[WINC] < cw[0]], key=lambda r: r[WINC] / cw[0])[:2]:
                add(r, "R")
        if s < 10 and nxt < math.inf:
            for r in sorted([r for r in R if r[WINC] >= nxt and r[AGE] >= gate], key=lambda r: -r[WINC])[:2]:
                add(r, "U")
        srt = sorted(R, key=lambda r: r[ID])
        for i in (len(srt) // 3, (2 * len(srt)) // 3):
            if len(R) >= 3 or i == 0:
                add(srt[min(i, len(srt) - 1)], "S")
        # extras: daily lines, age, staffing, hiring, income per customer, pace
        li = best(R, lower, DINC) if lower else (0, 0)
        lc = best(R, lower, DCUS) if lower else (0, 0)
        act = [r for r in R if r[WINC] > 0]
        fill = lambda r: 100 * r[HIRED] / r[CAP] if r[CAP] else 0
        ipc = lambda r: r[WINC] / r[WCUS] if r[WCUS] > 0 else 0
        mom = lambda r: 100 * r[DINC] * 7 / r[WINC] if r[WINC] > 0 else 0
        xd.append([[K(q(f(DINC), p)) for p in P5], [jsr(q(f(DCUS), p)) for p in P5],
                   [K(li[0]), jsr(li[1] * 1000)], [jsr(lc[0]), jsr(lc[1] * 1000)], sum(1 for r in R if r[DINC] < li[0]),
                   [jsr(q(f(AGE), p)) for p in (.25, .5, .75)], jsr(q([fill(r) for r in R], .5)),
                   jsr(100 * sum(1 for r in R if r[APPS]) / len(R)), jsr(q([ipc(r) for r in act], .5)),
                   jsr(q([mom(r) for r in act], .5)) if act else 0])
    curated = [(r, t) for r, t in sel.values()]
    c = [[r[ID], r[RAT], r[AGE], r[HIRED], jsr(r[WINC] / 1000), r[WCUS], t] for r, t in curated]
    xc = [[r[ID], K(r[DINC]), r[DCUS]] for r, t in curated]
    st = [r[RAT] for r in rated_rows]
    fill = lambda r: 100 * r[HIRED] / r[CAP] if r[CAP] else 0
    ipc = lambda r: r[WINC] / r[WCUS] if r[WCUS] > 0 else 0
    mom = lambda r: 100 * r[DINC] * 7 / r[WINC] if r[WINC] > 0 else 0
    cols = [lambda r: r[WINC], lambda r: r[DINC], lambda r: r[WCUS], lambda r: r[DCUS], lambda r: r[AGE], fill, ipc, mom]
    rho = [spear([f(r) for r in rated_rows], st) for f in cols]
    return {"base": [tid, name, ts, len(rows), len(rated_rows), packed, c], "index": [tid, name, len(rated_rows), n_arr, cw_arr],
            "xd": xd, "xc": xc, "rho": rho}


# ------------------------------------------------------------------ storage
def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), "utf-8")
    os.replace(tmp, path)


def merge_outputs(user_data: Path, bundled: Path, results, ts):
    """Write refreshed per-type files and merge them into index.json / x.json."""
    index = read_json(user_data / "index.json") or read_json(bundled / "index.json") or {"ts": ts, "types": []}
    x = read_json(user_data / "x.json") or read_json(bundled / "x.json") or {"d": {}, "c": {}, "rho": {}}
    types = {t[0]: t for t in index["types"]}
    for tid, res in results.items():
        write_json(user_data / ("%d.json" % tid), res["base"])
        types[tid] = res["index"]
        x["d"][str(tid)] = res["xd"]
        x["c"][str(tid)] = res["xc"]
        x["rho"][str(tid)] = res["rho"]
    index["types"] = [types[k] for k in sorted(types)]
    index["ts"] = ts
    write_json(user_data / "x.json", x)
    write_json(user_data / "index.json", index)


def save_snapshot(hist: Path, run_id, ts, tid, name, rows):
    d = hist / "snap" / run_id
    d.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"ts": ts, "type": tid, "name": name, "fields": FIELDS, "rows": rows}, separators=(",", ":"))
    with gzip.open(d / ("%d.json.gz" % tid), "wt", encoding="utf-8") as f:
        f.write(payload)


def load_snapshot(hist: Path, run_id, tid):
    try:
        with gzip.open(hist / "snap" / run_id / ("%d.json.gz" % tid), "rt", encoding="utf-8") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


def log_lines(hist: Path, run_id, ts, tid, res):
    """One compact line per star per run so the trend of every earn line can be charted later."""
    out = []
    for s, p in enumerate(res["base"][5][1:], 1):
        if len(p) < 2:
            continue
        xd = res["xd"][s - 1]
        out.append({"run": run_id, "ts": ts, "type": tid, "star": s, "n": p[0], "wl": p[4][0], "wa": p[4][1], "wc": p[5][0],
                    "p25": p[1][1], "p50": p[1][2], "dl": xd[2][0] if len(xd) > 1 else 0})
    hist.mkdir(parents=True, exist_ok=True)
    with open(hist / "lines.jsonl", "a", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, separators=(",", ":")) + "\n")


def read_lines(hist: Path, tid):
    out = []
    try:
        with open(hist / "lines.jsonl", encoding="utf-8") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") == tid:
                    out.append(o)
    except OSError:
        pass
    return out


def _sunday_between(t0, t1):
    """True if a Sunday 00:00 UTC falls in (t0, t1]."""
    day = 86400
    first = (math.floor(t0 / day) + 1) * day
    for t in range(first, int(t1) + 1, day):
        if time.gmtime(t).tm_wday == 6:
            return True
    return False


def update_moves(hist: Path, tid, old_snap, new_snap):
    """Compare two snapshots of one type and add what actually moved to moves.json.

    Records, per star at the older snapshot: how many stayed, and for each company that went up or
    down its weekly income (K), weekly customers and age at the older snapshot.
    """
    if not old_snap or not new_snap:
        return 0
    old = {r[0]: r for r in old_snap["rows"] if r[1] > 0}
    new = {r[0]: r for r in new_snap["rows"]}
    changes = [(o, new[i]) for i, o in old.items() if i in new and new[i][1] != o[1]]
    if not changes and not _sunday_between(old_snap["ts"], new_snap["ts"]):
        return 0
    moves = read_json(hist / "moves.json", {}) or {}
    mt = moves.setdefault(str(tid), {})
    dt = round((new_snap["ts"] - old_snap["ts"]) / 86400, 1)
    stay_by = {}
    for i, o in old.items():
        if i in new and new[i][1] == o[1]:
            stay_by.setdefault(o[1], []).append(o)
    for s in range(1, 11):
        e = mt.setdefault(str(s), {"stay": 0, "down": [], "up": [], "stayq": []})
        st = stay_by.get(s, [])
        e["stay"] += len(st)
        if st:
            w = [r[WINC] / 1000 for r in st]
            e["stayq"] = [jsr(q(w, p)) for p in (.05, .1, .25, .5)]
        for o, n in changes:
            if o[1] != s:
                continue
            rec = [jsr(o[WINC] / 1000), o[WCUS], o[AGE], n[RAT], dt]
            (e["down"] if n[RAT] < s else e["up"]).append(rec)
        e["down"], e["up"] = e["down"][-400:], e["up"][-400:]
    write_json(hist / "moves.json", moves)
    return len(changes)
