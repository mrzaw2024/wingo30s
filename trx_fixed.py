#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WINGO 30s — Telegram bot · Formula Engine + Auto-Switch + Demo
"""

import hashlib
import itertools
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta

def script_dir():
    try:
        d = os.path.dirname(os.path.abspath(__file__))
        if d and os.path.isdir(d):
            return d
    except NameError:
        pass
    return os.getcwd()

SETTINGS_FILE = os.path.join(script_dir(), "wingo_settings.json")
EVENT_LOG_FILE = os.path.join(script_dir(), "wingo_bot_log.jsonl")
FORMULA_FILE = os.path.join(script_dir(), "trx_formulas.json")
FORMULA_LOCK = threading.Lock()

VERSION = "5.0 — Formula + Auto-Switch + Demo"

def http_post(url, body, headers=None, timeout=15):
    data = json.dumps(body).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "text": r.read().decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", "replace")
        except Exception:
            body_txt = ""
        return {"status": e.code, "text": body_txt}
    except Exception as e:
        return {"status": None, "text": "", "err": str(e)}

def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "BOZAW/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "text": r.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"status": None, "text": "", "err": str(e)}

# ==========================================
# သင့်အကောင့် အချက်အလက်များ (လိုအပ်ပါက ဒီနေရာတွင် ပြင်ပါ)
# ==========================================
DEFAULTS = {
    # Set secrets in environment variables or wingo_settings.json.
    "bot_token": os.environ.get("WINGO_BOT_TOKEN", ""),
    "admin_id":  os.environ.get("WINGO_ADMIN_ID", ""),
    "phone":     os.environ.get("WINGO_PHONE", ""),
    "password":  os.environ.get("WINGO_PASSWORD", ""),
    "ladder":      [100, 300, 900, 2700],
    "target":      5000,
    "stoploss":    3000,
    "window":      100,
    "ladder_reset_on_win": True,
    "demo_mode":        False,
    "demo_balance":     10000.0,
    "formula_file":     FORMULA_FILE,
    "app_url":          "http://127.0.0.1:8080",
    "use_app_api":      True,
    "top_n":            1,
    "auto_switch":      True,
    "auto_every":       5,
    "rounds_since_search": 0,
    "auto_add_count":   100,
}

def load_settings():
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k in s.keys():
            if k in saved:
                s[k] = saved[k]
    except Exception:
        pass
    return s

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log("could not save settings: %s" % e)
        return False

LOG_LOCK = threading.Lock()
EVENT_LOG_LOCK = threading.Lock()

def log(msg):
    with LOG_LOCK:
        print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)

def append_event_log(event):
    row = dict(event)
    row["logged_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        with EVENT_LOG_LOCK:
            with open(EVENT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        log("event log write failed: %s" % e)

FORMULA_CACHE = {"mtime": 0, "state": None}

def load_formula_state():
    path = FORMULA_FILE
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {"sum": [], "diff": [], "checked": {}}
    if FORMULA_CACHE["mtime"] == mtime and FORMULA_CACHE["state"] is not None:
        return FORMULA_CACHE["state"]
    state = {"sum": [], "diff": [], "checked": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if isinstance(data.get("sum"), list):
                state["sum"] = data["sum"]
            if isinstance(data.get("diff"), list):
                state["diff"] = data["diff"]
            if isinstance(data.get("checked"), dict):
                state["checked"] = data["checked"]
    except Exception as e:
        log("formula read failed: %s" % e)
    FORMULA_CACHE["mtime"] = mtime
    FORMULA_CACHE["state"] = state
    return state

def save_formula_state(state):
    with FORMULA_LOCK:
        tmp = FORMULA_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, FORMULA_FILE)
            FORMULA_CACHE["mtime"] = 0
            FORMULA_CACHE["state"] = None
            return True
        except Exception as e:
            log("formula write failed: %s" % e)
            return False

def active_patterns(state=None):
    state = state or load_formula_state()
    sums = [x for x in state.get("sum") or [] if x.get("enabled", True) is not False]
    diffs = [x for x in state.get("diff") or [] if x.get("enabled", True) is not False]
    return sums, diffs

def digit_to_bs(digit):
    if digit is None:
        return None
    return "B" if digit >= 5 else "S"

def reduce_to_single_digit(n):
    n = abs(int(n))
    while n >= 10:
        n = sum(int(ch) for ch in str(n))
    return n

def diff_pair_val(op, x, y):
    if op == "+":
        return x + y
    if op == "*":
        return x * y
    return abs(x - y)

def pos_sum_calc(positions, digits, length):
    vals = []
    for pos in positions:
        idx = length - pos
        if idx < 0:
            return None
        vals.append(digits[idx])
    total = sum(vals)
    units = total % 10
    return {"pred": "B" if units >= 5 else "S", "units": units}

def diff_pair_calc(pairs, digits, length, combine_op):
    vals = []
    for pair in pairs:
        idx1 = length - int(pair["a"])
        idx2 = length - int(pair["b"])
        if idx1 < 0 or idx2 < 0:
            return None
        vals.append(diff_pair_val(pair.get("op") or "-", digits[idx1], digits[idx2]))
    total = vals[0] if vals else 0
    for extra in vals[1:]:
        total = diff_pair_val(combine_op, total, extra)
    digit = reduce_to_single_digit(total)
    return {"pred": "B" if digit >= 5 else "S", "digit": digit}

def wl_stats(seq):
    wins = seq.count("W")
    losses = seq.count("L")
    total = wins + losses
    current = 0
    current_type = ""
    longest_w = 0
    longest_l = 0
    run_w = 0
    run_l = 0
    for mark in seq:
        if mark == "W":
            run_w += 1
            run_l = 0
            longest_w = max(longest_w, run_w)
        elif mark == "L":
            run_l += 1
            run_w = 0
            longest_l = max(longest_l, run_l)
    if seq:
        last = seq[-1]
        current_type = last
        for mark in reversed(seq):
            if mark != last:
                break
            current += 1
    return {
        "wins": wins, "losses": losses, "total": total,
        "rate": round((wins / total) * 100, 1) if total else 0,
        "currentType": current_type, "currentStreak": current,
        "longestW": longest_w, "longestL": longest_l, "seq": seq[-100:],
    }

def eval_sum_stats(positions, digits):
    seq = []
    if not positions:
        return {"pred": None, "seq": [], **wl_stats([])}
    max_pos = max(positions)
    for idx in range(max_pos, len(digits)):
        calc = pos_sum_calc(positions, digits, idx)
        actual = digit_to_bs(digits[idx])
        if calc and actual:
            seq.append("W" if calc["pred"] == actual else "L")
    next_calc = pos_sum_calc(positions, digits, len(digits))
    return {"pred": next_calc["pred"] if next_calc else None, "seq": seq, **wl_stats(seq)}

def eval_diff_stats(pattern, digits):
    seq = []
    pairs = pattern.get("pairs") or []
    if not pairs:
        return {"pred": None, "seq": [], **wl_stats([])}
    max_pos = max(max(p["a"], p["b"]) for p in pairs)
    for idx in range(max_pos, len(digits)):
        calc = diff_pair_calc(pairs, digits, idx, pattern.get("combineOp") or "+")
        actual = digit_to_bs(digits[idx])
        if calc and actual:
            seq.append("W" if calc["pred"] == actual else "L")
    next_calc = diff_pair_calc(pairs, digits, len(digits), pattern.get("combineOp") or "+")
    return {"pred": next_calc["pred"] if next_calc else None, "seq": seq, **wl_stats(seq)}

def formula_label(item):
    if item.get("positions"):
        return "Sum " + "+".join(str(x) for x in item["positions"])
    pairs = item.get("pairs") or []
    return "Diff " + "|".join(f"{p['a']}{p.get('op') or '-'}{p['b']}" for p in pairs)

def majority_bs_next(digits, pick_count=3):
    if len(digits) < 10:
        return {"pred": None, "picks": []}
    valid = []
    maps = {length: {} for length in range(1, 31)}
    b_count = 0
    for digit in digits:
        bs_val = digit_to_bs(digit)
        if not bs_val:
            continue
        upto = min(len(valid), 30)
        prefix = ""
        for length in range(1, upto + 1):
            prefix = valid[-1] if length == 1 else valid[-length] + "," + prefix
            maps[length].setdefault(prefix, {"B": 0, "S": 0})
            maps[length][prefix][bs_val] += 1
        if bs_val == "B":
            b_count += 1
        valid.append(bs_val)
    prior_b = b_count / max(1, len(valid))
    prior_s = 1 - prior_b
    keys = {}
    prefix = ""
    upto = min(len(valid), 30)
    for length in range(1, upto + 1):
        prefix = valid[-1] if length == 1 else valid[-length] + "," + prefix
        keys[length] = prefix
    picks = []
    for length in range(30, 0, -1):
        key = keys.get(length)
        stats = maps[length].get(key) if key is not None else None
        total = (stats.get("B", 0) + stats.get("S", 0)) if stats else 0
        if total < 2:
            continue
        denom = total + 3
        s_b = ((stats.get("B", 0) if stats else 0) + 3 * prior_b) / denom
        s_s = ((stats.get("S", 0) if stats else 0) + 3 * prior_s) / denom
        if abs(s_b - s_s) < 0.01:
            continue
        picks.append("B" if s_b > s_s else "S")
        if len(picks) == pick_count:
            break
    if len(picks) < pick_count:
        return {"pred": None, "picks": picks}
    return {
        "pred": "B" if picks.count("B") > picks.count("S") else "S",
        "picks": picks,
        "cb": picks.count("B"), "cs": picks.count("S"),
    }

def formula_backtest(digits, state=None):
    sums, diffs = active_patterns(state)
    ranked = []
    majority = majority_bs_next(digits)
    majority_seq = []
    for idx in range(10, len(digits)):
        pred = majority_bs_next(digits[:idx]).get("pred")
        actual = digit_to_bs(digits[idx])
        if pred in {"B", "S"} and actual in {"B", "S"}:
            majority_seq.append("W" if pred == actual else "L")
    ranked.append({
        "key": "bs", "label": "Majority B/S",
        "pred": majority.get("pred"), **wl_stats(majority_seq),
    })
    for pattern in sums:
        stats = eval_sum_stats(pattern.get("positions") or [], digits)
        ranked.append({
            "key": "pat_" + str(pattern.get("id")),
            "label": formula_label(pattern),
            "pred": stats.get("pred"),
            **{k: stats[k] for k in ("wins", "losses", "total", "rate",
                                     "currentType", "currentStreak",
                                     "longestW", "longestL", "seq")},
        })
    for pattern in diffs:
        stats = eval_diff_stats(pattern, digits)
        ranked.append({
            "key": "diffpat_" + str(pattern.get("id")),
            "label": formula_label(pattern),
            "pred": stats.get("pred"),
            **{k: stats[k] for k in ("wins", "losses", "total", "rate",
                                     "currentType", "currentStreak",
                                     "longestW", "longestL", "seq")},
        })
    ranked.sort(key=lambda x: (x.get("rate") or 0,
                               x.get("longestW") or 0,
                               -(x.get("longestL") or 0),
                               x.get("total") or 0),
                reverse=True)

    def top_vote(count):
        top = ranked[:count]
        votes = [x.get("pred") for x in top if x.get("pred") in {"B", "S"}]
        cb = votes.count("B")
        cs = votes.count("S")
        pred = "B" if cb > cs else "S" if cs > cb else None
        return {
            "n": count, "pred": pred, "cb": cb, "cs": cs,
            "voteCount": len(votes),
            "formulas": [{"label": x.get("label"), "pred": x.get("pred"),
                          "rate": x.get("rate")} for x in top],
            "stats": top[0] if top else {},
        }

    return {
        "ranked": ranked[:8],
        "top1": top_vote(1),
        "top2": top_vote(2),
        "top3": top_vote(3),
        "formulaCount": len(sums) + len(diffs),
    }

def formula_key(item):
    if item.get("positions"):
        return "sum:" + "+".join(str(x) for x in item["positions"])
    pairs = item.get("pairs") or []
    return ("diff:" + "|".join(f"{p['a']}{p.get('op') or '-'}{p['b']}" for p in pairs)
            + "|" + (item.get("combineOp") or "+"))

def build_sum_candidates(max_pos):
    cands = []
    for size in range(2, 7):
        if size > max_pos:
            break
        for combo in itertools.combinations(range(1, max_pos + 1), size):
            cands.append({"type": "sum", "positions": list(combo)})
    return cands

def build_pure_diff_candidates(max_pos, op):
    cands = []
    pairs = [{"a": a, "b": b} for a in range(1, max_pos + 1)
             for b in range(a + 1, max_pos + 1)]
    for i, pa in enumerate(pairs):
        cands.append({"type": "diff",
                      "pairs": [{"a": pa["a"], "b": pa["b"], "op": op}],
                      "combineOp": op})
        for pb in pairs[i + 1:]:
            if pa["a"] in (pb["a"], pb["b"]) or pa["b"] in (pb["a"], pb["b"]):
                continue
            cands.append({
                "type": "diff",
                "pairs": [
                    {"a": pa["a"], "b": pa["b"], "op": op},
                    {"a": pb["a"], "b": pb["b"], "op": op},
                ],
                "combineOp": op,
            })
    return cands

def score_candidate(cand, digits):
    if cand.get("type") == "sum":
        stats = eval_sum_stats(cand["positions"], digits)
    else:
        stats = eval_diff_stats(cand, digits)
    out = dict(cand)
    out.update({k: stats.get(k) for k in
                ("pred", "wins", "losses", "total", "rate", "longestW", "longestL")})
    out["label"] = formula_label(out)
    return out

def rank_key(item):
    return (item.get("rate") or 0, item.get("longestW") or 0,
            -(item.get("longestL") or 0), item.get("total") or 0)

def search_formulas(digits, top_n=100, flags=None):
    flags = flags or {}
    max_pos = min(7, max(2, len(digits) - 1))
    pool = []
    if flags.get("useMixed", True):
        pool.extend(build_sum_candidates(max_pos))
    if flags.get("useMinus", True):
        pool.extend(build_pure_diff_candidates(max_pos, "-"))
    if flags.get("usePlus", True):
        pool.extend(build_pure_diff_candidates(max_pos, "+"))
    if flags.get("useMul", True):
        pool.extend(build_pure_diff_candidates(max_pos, "*"))
    seen = set()
    scored = []
    for cand in pool:
        key = formula_key(cand)
        if key in seen:
            continue
        seen.add(key)
        s = score_candidate(cand, digits)
        if not s.get("total"):
            continue
        scored.append(s)
    scored.sort(key=rank_key, reverse=True)
    return {"pool": scored[:top_n], "count": len(pool), "scanned": len(scored)}

def next_pattern_id(prefix, items):
    seq = 0
    for item in items:
        try:
            seq = max(seq, int("".join(ch for ch in str(item.get("id") or "")
                                       if ch.isdigit()) or 0))
        except ValueError:
            pass
    return f"{prefix}{seq + 1}"

def replace_formulas(cands):
    state = {"sum": [], "diff": [], "checked": {"bs": True}, "seq": 0}
    seen = set()
    added = []
    for cand in cands:
        key = formula_key(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.get("type") == "sum" or cand.get("positions"):
            pid = next_pattern_id("p", state["sum"])
            item = {"id": pid, "positions": list(cand["positions"]), "enabled": True}
            state["sum"].append(item)
            state["checked"]["pat_" + pid] = True
            method_key = "pat_" + pid
        else:
            pid = next_pattern_id("dp", state["diff"])
            item = {
                "id": pid,
                "pairs": [dict(pair) for pair in cand.get("pairs") or []],
                "combineOp": cand.get("combineOp") or "+",
                "enabled": True,
            }
            state["diff"].append(item)
            state["checked"]["diffpat_" + pid] = True
            method_key = "diffpat_" + pid
        added.append({
            "key": method_key,
            "label": formula_label(item),
            "rate": cand.get("rate"),
            "wins": cand.get("wins"),
            "losses": cand.get("losses"),
        })
    if not state["sum"] and not state["diff"]:
        return {"added": 0, "items": [], "count": 0}
    save_formula_state(state)
    return {
        "added": len(added),
        "items": added,
        "count": len(state["sum"]) + len(state["diff"]),
    }

BASE_URL = "https://6lotteryapi.com/api/webapi/"
FEED_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"),
    "Origin": "https://6win598.com",
    "Referer": "https://6win598.com/",
    "Ar-Origin": "https://6win598.com",
    "Accept": "application/json, text/plain, */*",
}

TYPE_ID = 30
TYPE_RANDOM = "c21f4a3e9b6d47f1a0e2c3d4b5a69788"
SELECT_TYPE = {"BIG": 13, "SMALL": 14}
GAME_TYPE = {"BS": 2}
PAYOUT_DEFAULT = {"BIG": 0.98, "SMALL": 0.98}

def generate_signature(data):
    excluded = {"signature", "track", "xosoBettingData"}
    out = {}
    for key in sorted(data):
        value = data[key]
        if value is not None and value != "" and key not in excluded:
            out[key] = 0 if value == 0 else value
    encoded = json.dumps(out, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(encoded.encode("utf-8")).hexdigest().upper()

def normalise_phone(raw):
    d = re.sub(r"[^0-9]", "", str(raw or ""))
    if not d:
        return ""
    if d.startswith("95") and len(d) >= 9:
        return d
    if d.startswith("0"):
        return "95" + d[1:]
    return "95" + d

def local_phone(full):
    d = re.sub(r"[^0-9]", "", str(full or ""))
    if d.startswith("95") and len(d) > 2:
        return d[2:]
    return d

DEFAULT_SCOPE = [10, 100, 1000, 10000]
DEFAULT_MULTIPLES = [1, 5, 10, 20, 50, 100]

def stake_to_pair(stake, scope=None, multiples=None):
    """Convert a requested total stake into an exact API amount/count pair.

    Prefer a configured denomination only when its count is supported. Never
    silently send an unsupported count (e.g. 10 x 30 for a requested 300); in
    that case send the requested amount as one bet so the API can validate it.
    """
    target = int(round(float(stake)))
    if target <= 0:
        raise ValueError("stake must be a positive integer")
    sc = sorted(set(int(x) for x in (scope or DEFAULT_SCOPE) if int(x) > 0),
                reverse=True)
    mu = set(int(x) for x in (multiples or DEFAULT_MULTIPLES) if int(x) > 0)
    for amount in sc:
        if target % amount == 0 and (target // amount) in mu:
            return amount, target // amount
    # Exact fallback: preserve the user's custom ladder value. The previous
    # fallback could produce a count absent from DEFAULT_MULTIPLES and trigger
    # the server's "Betting amount error".
    return target, 1

def size_of(num):
    return "BIG" if num >= 5 else "SMALL"

class Feed(object):
    def __init__(self, timeout=12):
        self.token = None
        self.token_header = "Bearer "
        self.timeout = timeout
        self.phone = None
        self.last_error = ""
        self.server_skew = 0.0
        self.scope = list(DEFAULT_SCOPE)
        self.multiples = list(DEFAULT_MULTIPLES)

    def call(self, path, payload, auth=True):
        body = dict(payload)
        body["signature"] = generate_signature(body)
        body["timestamp"] = int(time.time())
        headers = dict(FEED_HEADERS)
        if auth and self.token:
            headers["Authorization"] = self.token_header + self.token
        r = http_post(BASE_URL + path, body, headers, self.timeout)
        if r["status"] is None:
            self.last_error = "network: %s" % r.get("err", "")
            return {"ok": False, "msg": self.last_error, "net": True}
        if r["status"] != 200:
            self.last_error = "HTTP %d" % r["status"]
            return {"ok": False, "msg": self.last_error}
        try:
            j = json.loads(r["text"])
        except Exception:
            self.last_error = "non-JSON: %s" % r["text"][:120]
            return {"ok": False, "msg": self.last_error}
        if str(j.get("code")) == "0":
            return {"ok": True, "data": j.get("data")}
        self.last_error = str(j.get("msg") or "rejected")
        return {"ok": False, "msg": self.last_error}

    def login(self, phone, password):
        ph = normalise_phone(phone)
        body = {
            "username": ph, "pwd": password, "phonetype": 1,
            "logintype": "mobile", "packId": "",
            "deviceId": "web", "language": 1,
            "random": hashlib.md5(str(time.time()).encode()).hexdigest(),
        }
        r = self.call("Login", body, auth=False)
        if r["ok"] and r.get("data") and r["data"].get("token"):
            d = r["data"]
            self.token = d.get("token")
            self.token_header = d.get("tokenHeader") or "Bearer "
            self.phone = ph
            return {"ok": True, "user": d.get("userId", ph)}
        return {"ok": False, "msg": r.get("msg", "login failed")}

    def issue(self):
        r = self.call("GetGameIssue", {"typeId": TYPE_ID, "language": 7,
                                        "random": "7d76f361dc5d4d8c98098ae3d48ef7af"})
        if not r["ok"] or not r["data"]:
            return None
        d = r["data"]
        try:
            st = datetime.strptime(str(d.get("serviceTime")), "%Y-%m-%d %H:%M:%S")
            self.server_skew = (st - datetime.now()).total_seconds()
        except Exception:
            pass
        return {
            "issue":   str(d.get("issueNumber") or ""),
            "start":   str(d.get("startTime") or ""),
            "end":     str(d.get("endTime") or ""),
            "service": str(d.get("serviceTime") or ""),
        }

    def history(self, page_size=100):
        r = self.call("GetNoaverageEmerdList", {
            "pageSize": page_size, "typeId": TYPE_ID, "language": 7,
            "random": "4ad5325e389745a882f4189ed6550e70",
        })
        if not r["ok"] or not r["data"]:
            return None
        lst = r["data"].get("list") or []
        return lst if isinstance(lst, list) else []

    def balance(self):
        r = self.call("GetBalance", {"language": 7,
                                      "random": "71ebd56cff7d4679971c482807c33f6f"})
        if not r["ok"] or not r["data"]:
            return None
        v = r["data"].get("Amount", r["data"].get("amount"))
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def bet(self, issue, call, stake, game_type):
        amount, count = stake_to_pair(stake, self.scope, self.multiples)
        body = {
            "typeId": TYPE_ID, "issuenumber": str(issue), "language": 7,
            "gameType": int(game_type), "amount": amount, "betCount": count,
            "selectType": SELECT_TYPE[call],
            "random": "f9ec46840a374a65bb2abad44dfc4dc3",
        }
        r = None
        for _ in range(3):
            r = self.call("GameBetting", body)
            if r["ok"] or not r.get("net"):
                break
            time.sleep(1.5)
        r = r or {"ok": False, "msg": "no reply"}
        r["pair"] = (amount, count)
        return r

def norm_result(raw):
    v = raw.get("number", raw.get("result", raw.get("num")))
    try:
        n = int(re.sub(r"[^0-9]", "", str(v)))
    except Exception:
        return None
    if n < 0 or n > 9:
        return None
    iss = raw.get("issueNumber", raw.get("issue"))
    if iss is None:
        return None
    return {"issue": str(iss), "number": n}

class AppBridge:
    def __init__(self, base_url):
        self.base = (base_url or "").rstrip("/")

    def health(self):
        if not self.base:
            return False
        return http_get(self.base + "/health", timeout=3).get("status") == 200

    def live(self):
        if not self.base:
            return None
        r = http_get(self.base + "/api/live", timeout=20)
        if r.get("status") != 200:
            return None
        try:
            return json.loads(r["text"])
        except Exception:
            return None

TG_API = "https://api.telegram.org/bot"

MENU = {
    "keyboard": [
        [{"text": "▶️ START"}, {"text": "⏹️ STOP"}],
        [{"text": "📊 STATUS"}, {"text": "💰 BALANCE"}],
        [{"text": "🧠 STRATEGY"}, {"text": "⚙️ SETTINGS"}],
        [{"text": "🔑 LOGIN"}, {"text": "🔒 SESSION"}],
        [{"text": "🔄 AUTO SWITCH"}, {"text": "🧮 FORMULAS"}],
        [{"text": "🎭 DEMO MODE"}, {"text": "🎯 TOP N"}],
        [{"text": "❓ HELP"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}

CANCEL_KB = {"keyboard": [[{"text": "❌ CANCEL"}]],
             "resize_keyboard": True, "is_persistent": False}

ASK = {
    "ladder": "🎚 <b>LADDER</b>\n\nStakes ကို ပို့ပါ:\n<code>10,30,90,270,810</code>",
    "target": "🎯 <b>TARGET</b>\n\n<code>5000</code>",
    "stoploss": "🛑 <b>STOP LOSS</b>\n\n<code>2000</code>",
    "top_n": "🎯 <b>TOP N</b>\n\n1 / 2 / 3 ပို့ပါ",
    "auto_every": "🔄 <b>AUTO-SWITCH EVERY</b>\n\n5 / 10 / 20 / 50 ပို့ပါ",
}

BUTTONS = set(b["text"] for row in MENU["keyboard"] for b in row) | {"❌ CANCEL"}

def esc(s):
    return (str("" if s is None else s)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def ks(v):
    try:
        n = float(v)
        if abs(n - round(n)) < 1e-9:
            return "{:,} Ks".format(int(round(n)))
        return "{:,.1f} Ks".format(n).rstrip("0").rstrip(".")
    except Exception:
        return "0 Ks"

class Telegram(object):
    def __init__(self, token, admin_id):
        self.token = token
        self.admin = str(admin_id)
        self.offset = 0
        self.me = {}
        self.denied = set()

    def call(self, method, params=None, long=False):
        if not self.token:
            return {"ok": False}
        r = http_post(TG_API + self.token + "/" + method, params or {},
                      None, 35 if long else 15)
        if r["status"] is None:
            return {"ok": False, "msg": r.get("err", "")}
        try:
            j = json.loads(r["text"])
        except Exception:
            return {"ok": False}
        if not j.get("ok"):
            return {"ok": False, "msg": j.get("description"), "code": j.get("error_code")}
        return {"ok": True, "data": j.get("result")}

    def get_me(self):
        r = self.call("getMe")
        if r["ok"]:
            self.me = r["data"] or {}
        return r

    def send(self, text, keyboard="menu", chat_id=None, reply_to_message_id=None):
        body = {"chat_id": chat_id or self.admin, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to_message_id:
            body["reply_to_message_id"] = int(reply_to_message_id)
            body["allow_sending_without_reply"] = True
        if keyboard == "menu":
            body["reply_markup"] = MENU
        elif isinstance(keyboard, dict):
            body["reply_markup"] = keyboard
        return self.call("sendMessage", body)

    def notify(self, text, reply_to_message_id=None):
        try:
            return self.send(text, reply_to_message_id=reply_to_message_id)
        except Exception:
            return {"ok": False}

    def poll(self, handler):
        while True:
            r = self.call("getUpdates", {
                "offset": self.offset, "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            }, long=True)
            if not r["ok"]:
                time.sleep(3)
                continue
            for u in (r["data"] or []):
                self.offset = u["update_id"] + 1
                msg = u.get("message") or {}
                cq = u.get("callback_query") or {}
                chat = str((msg.get("chat") or {}).get("id")
                           or (cq.get("from") or {}).get("id") or "")
                if chat != self.admin:
                    if chat and chat not in self.denied:
                        self.denied.add(chat)
                        self.send("⛔ private\n🆔 <code>%s</code>" % esc(chat), chat_id=chat)
                    continue
                try:
                    if msg.get("text"):
                        handler("text", msg["text"])
                    elif cq:
                        self.call("answerCallbackQuery",
                                  {"callback_query_id": cq.get("id")})
                        handler("callback", cq.get("data") or "")
                except Exception:
                    log("cmd error:\n" + traceback.format_exc())

MIN_REMAIN = 6.0
POLL_MS = 1500

class Bot(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.feed = Feed()
        self.bridge = AppBridge(cfg.get("app_url") or "")
        self.tg = Telegram(cfg.get("bot_token", ""), cfg.get("admin_id", ""))
        self.lock = threading.RLock()

        self.balance = 0.0
        self.profit = 0.0
        self.level = 0
        self.ladder = list(cfg.get("ladder") or DEFAULTS["ladder"])
        self.target = int(cfg.get("target") or 0)
        self.stoploss = int(cfg.get("stoploss") or 0)
        self.top_n = int(cfg.get("top_n") or 1)

        self.running = False
        self.logged_in = False
        self.pending = None
        self.history = []
        self.last_issue = None
        self.last_round = {}
        self.results = []
        self.notes = []
        self.started_at = None
        self.awaiting = None
        self.current_confidence = 50.0
        self.start_pending = False
        self.last_decision = None
        self.tmp_phone = ""

        self.rounds_since_search = int(cfg.get("rounds_since_search") or 0)
        self.auto_switch = bool(cfg.get("auto_switch", True))
        self.auto_every = int(cfg.get("auto_every") or 5)
        self.auto_add_count = int(cfg.get("auto_add_count") or 100)

        if self.cfg.get("demo_mode"):
            self.balance = float(self.cfg.get("demo_balance", 10000.0))

    def rules_pair(self):
        return (getattr(self.feed, "scope", None) or DEFAULT_SCOPE,
                getattr(self.feed, "multiples", None) or DEFAULT_MULTIPLES)

    def payout_for(self, call):
        return PAYOUT_DEFAULT.get(call, 0.98)

    def ingest_results(self, items):
        known = set(i for i, _ in self.results)
        fresh = 0
        for it in items or []:
            iss = str(it.get("issue") or "")
            n = it.get("number")
            if not iss or not isinstance(n, int) or iss in known:
                continue
            self.results.append((iss, n))
            known.add(iss)
            fresh += 1
        self.results.sort(key=lambda x: (0, int(x[0])) if str(x[0]).isdigit()
                          else (1, len(str(x[0])), str(x[0])))
        if len(self.results) > 100:
            self.results = self.results[-100:]
        return fresh

    def digits(self):
        return [n for _, n in self.results]

    def strategy(self):
        digits = self.digits()
        if len(digits) < 20:
            return {"call": "BIG", "picked": "BIG", "confidence": 50.0,
                    "detail": {"mode": "formula", "reason": "history %d < 20" % len(digits)}}
        pred = formula_backtest(digits)
        top_key = "top%d" % self.top_n
        top = pred.get(top_key) or pred.get("top1") or {}
        call = top.get("pred")
        if call not in {"B", "S"}:
            ranked = pred.get("ranked") or []
            fb = ranked[0].get("pred") if ranked else None
            call = fb if fb in {"B", "S"} else "B"
        call_full = "BIG" if call == "B" else "SMALL"
        stats = top.get("stats") or {}
        return {
            "call": call_full, "picked": call_full,
            "confidence": float(stats.get("rate") or 50.0),
            "detail": {
                "mode": "formula", "top_n": self.top_n,
                "cb": top.get("cb"), "cs": top.get("cs"),
                "rate": stats.get("rate"),
                "formulas": top.get("formulas"),
                "formulaCount": pred.get("formulaCount"),
            },
        }

    def note(self, s):
        self.notes.append("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), s))
        del self.notes[:-40]
        log(s)

    def persist(self):
        self.cfg.update({
            "ladder": self.ladder, "target": self.target,
            "stoploss": self.stoploss, "top_n": self.top_n,
            "auto_switch": self.auto_switch, "auto_every": self.auto_every,
            "rounds_since_search": self.rounds_since_search,
            "auto_add_count": self.auto_add_count,
            "phone": self.feed.phone or self.cfg.get("phone", ""),
        })
        save_settings(self.cfg)

    def remaining(self, iss):
        try:
            end = datetime.strptime(iss["end"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
        now = datetime.now() + timedelta(seconds=self.feed.server_skew)
        return (end - now).total_seconds()

    def next_stake(self):
        if self.level >= len(self.ladder):
            self.level = len(self.ladder) - 1
        return self.ladder[self.level]

    def auto_switch_run(self, force=False):
        digits = self.digits()
        if len(digits) < 20:
            return {"ok": False, "msg": "history < 20"}
        found = search_formulas(
            digits, top_n=self.auto_add_count,
            flags={"useMixed": True, "useMinus": True,
                   "usePlus": True, "useMul": True},
        )
        if not found.get("pool"):
            return {"ok": False, "msg": "no candidate"}
        top_rate = found["pool"][0].get("rate") or 0
        replaced = replace_formulas(found["pool"])
        self.rounds_since_search = 0
        self.persist()
        self.note("AUTO-SWITCH: %d formulas · top rate %.1f%%"
                  % (replaced.get("added") or 0, top_rate))
        append_event_log({
            "event": "auto_switch",
            "scanned": found.get("scanned"),
            "added": replaced.get("added"),
            "top_rate": top_rate,
            "every": self.auto_every,
        })
        return {
            "ok": True,
            "added": replaced.get("added"),
            "top_rate": top_rate,
            "scanned": found.get("scanned"),
            "count": replaced.get("count"),
        }

    def auto_switch_tick(self):
        if not self.auto_switch:
            return
        self.rounds_since_search += 1
        if self.rounds_since_search >= self.auto_every:
            self.note("AUTO-SWITCH triggering (round %d)"
                      % self.rounds_since_search)
            r = self.auto_switch_run()
            if r["ok"]:
                self.tg.notify(
                    "🔄 <b>FORMULA SWITCH</b>\n"
                    "ဖော်မြူလာ <b>%d</b> ခု အသစ်ထည့်ပြီး\n"
                    "Top rate <b>%.1f%%</b>\n"
                    "စမ်းသပ် <b>%d</b> ခု\n"
                    "Next switch in <b>%d</b> rounds"
                    % (r["added"], r["top_rate"], r["scanned"], self.auto_every))
            else:
                self.tg.notify("⚠️ auto-switch failed: %s" % esc(r["msg"]))

    def run_forever(self):
        while True:
            try:
                self.tick()
            except Exception:
                self.note("tick error: " + traceback.format_exc().splitlines()[-1])
            time.sleep(POLL_MS / 1000.0)

    def _fetch_live(self):
        if self.cfg.get("use_app_api") and self.bridge.base:
            data = self.bridge.live()
            if data and isinstance(data.get("results"), list):
                items = [norm_result(r) for r in data["results"]]
                items = [x for x in items if x]
                d = data.get("data") or {}
                if d.get("issueNumber"):
                    return {"issue": {
                        "issue": str(d["issueNumber"]),
                        "start": d.get("startTime") or "",
                        "end": d.get("endTime") or "",
                        "service": d.get("serviceTime") or "",
                    }, "results": items, "source": "app.py", "remain": d.get("remaining")}
        iss = self.feed.issue()
        if not iss or not iss["issue"]:
            return None
        hist = self.feed.history()
        items = [norm_result(r) for r in (hist or [])]
        items = [x for x in items if x]
        return {"issue": iss, "results": items, "source": "feed", "remain": None}

    def tick(self):
        live = self._fetch_live()
        if not live:
            return
        iss = live["issue"]
        fresh = self.ingest_results(live["results"])
        if fresh:
            self.settle_from_history()

        changed = iss["issue"] != self.last_issue
        force_start = self.start_pending
        if not changed and not force_start:
            return
        if changed:
            self.last_issue = iss["issue"]

        decided = self.strategy()
        call = str(decided["call"]).upper()
        self.current_confidence = float(decided.get("confidence", 50.0) or 50.0)
        self.last_decision = decided
        remain = (live.get("remain") if live.get("remain") is not None
                  else self.remaining(iss))
        self.last_round = {
            "issue": iss["issue"], "market": "BS", "call": call,
            "picked": call, "confidence": self.current_confidence,
            "type": "formula", "remain": remain,
        }
        append_event_log({
            "event": "prediction", "issue": str(iss["issue"]),
            "call": call, "confidence": self.current_confidence,
            "decision": decided.get("detail"),
            "demo_mode": bool(self.cfg.get("demo_mode")),
        })

        if not self.running or self.pending:
            return
        if not self.cfg.get("demo_mode") and not self.logged_in:
            return
        if force_start:
            self.start_pending = False
        if not force_start and remain is not None and remain < MIN_REMAIN:
            return
        self.place(iss["issue"], "BS", call, self.current_confidence,
                   decision=decided.get("detail"))

    def place(self, issue, market, call, confidence=50.0, decision=None):
        ask = self.next_stake()
        stake = int(round(float(ask)))

        if self.cfg.get("demo_mode"):
            demo_bal = float(self.cfg.get("demo_balance", 10000.0))
            if stake > demo_bal:
                self.halt("🔴 <b>DEMO BALANCE INSUFFICIENT</b>", "stop")
                return
            lvl = self.level
            self.pending = {
                "issue": issue, "call": call, "market": market,
                "stake": stake, "lvl": lvl, "demo": True,
                "bet_message_id": None, "decision": decision,
            }
            self.balance = demo_bal - stake
            sent = self.tg.notify(self.stake_card(
                issue, lvl, call, market, stake, stake, 1, 2, confidence, demo=True))
            if sent.get("ok") and isinstance(sent.get("data"), dict):
                self.pending["bet_message_id"] = sent["data"].get("message_id")
            return

        if self.balance and stake > self.balance:
            self.halt("🔴 <b>INSUFFICIENT BALANCE</b>", "stop")
            return
        lvl = self.level
        bal_before = self.balance
        r = self.feed.bet(issue, call, stake, 2)
        if r["ok"]:
            amount, count = r["pair"]
            self.pending = {
                "issue": issue, "call": call, "market": market,
                "stake": amount * count, "tries": 0, "lvl": lvl,
                "bal_before": bal_before, "bet_message_id": None,
                "demo": False, "decision": decision,
            }
            if self.balance:
                self.balance -= amount * count
            sent = self.tg.notify(self.stake_card(
                issue, lvl, call, market, amount * count,
                amount, count, 2, confidence))
            if sent.get("ok") and isinstance(sent.get("data"), dict):
                self.pending["bet_message_id"] = sent["data"].get("message_id")
        else:
            low = str(r["msg"]).lower()
            if "settled" in low or "period" in low or "closed" in low:
                pass
            else:
                amount, count = r.get("pair", (stake, 1))
                self.tg.notify("⚠️ BET FAILED: %s\n#%s %s\n"
                               "Requested: %s Ks · sent pair: %s × %s"
                               % (esc(r["msg"]), esc(issue), esc(call),
                                  ks(stake), amount, count))

    def settle_from_history(self):
        if not self.pending:
            return
        num = dict(self.results).get(self.pending["issue"])
        if num is None:
            self.pending["tries"] = self.pending.get("tries", 0) + 1
            if self.pending["tries"] > 40:
                self.pending = None
            return
        p = self.pending

        if p.get("demo"):
            won = size_of(num) == p["call"]
            expected = (float(p["stake"]) * self.payout_for(p["call"])
                        if won else -float(p["stake"]))
            returned = (float(p["stake"]) + float(p["stake"]) * self.payout_for(p["call"])
                        if won else 0.0)
            new_demo = (float(self.cfg.get("demo_balance", 10000.0))
                        - float(p["stake"]) + returned)
            self.cfg["demo_balance"] = new_demo
            self.balance = new_demo
            self.profit += expected
            self.history.append({"issue": p["issue"], "call": p["call"],
                                 "stake": p["stake"], "pl": expected,
                                 "won": won, "number": num, "demo": True})
            append_event_log({
                "event": "demo_settlement", "issue": str(p["issue"]),
                "call": p["call"], "actual_number": num,
                "outcome": "WIN" if won else "LOSS",
                "stake": p["stake"], "pl": expected,
                "demo_balance_after": new_demo,
            })
            if won:
                self.level = 0
            else:
                self.level += 1
            self.tg.notify(self.result_card(
                won, num, p["stake"], expected, p.get("lvl", self.level), demo=True),
                reply_to_message_id=p.get("bet_message_id"))
            self.pending = None
            self.auto_switch_tick()
            self.check_limits()
            save_settings(self.cfg)
            return

        won = size_of(num) == p["call"]
        expected = (float(p["stake"]) * self.payout_for(p["call"])
                    if won else -float(p["stake"]))
        b = self.feed.balance()
        before = p.get("bal_before")
        self.profit += expected
        if b is not None:
            self.balance = b
        elif before is not None:
            self.balance = before + expected
        self.history.append({"issue": p["issue"], "call": p["call"],
                             "stake": p["stake"], "pl": expected,
                             "won": won, "number": num})
        append_event_log({
            "event": "settlement", "issue": str(p["issue"]),
            "call": p["call"], "actual_number": num,
            "outcome": "WIN" if won else "LOSS",
            "stake": p["stake"], "pl": expected,
        })
        if won:
            self.level = 0
        else:
            self.level += 1
        self.tg.notify(self.result_card(
            won, num, p["stake"], expected, p.get("lvl", self.level)),
            reply_to_message_id=p.get("bet_message_id"))
        self.pending = None
        self.auto_switch_tick()
        self.check_limits()
        self.persist()

    def check_limits(self):
        if self.target and self.profit >= self.target:
            self.halt("🏆 <b>TARGET REACHED</b>", "target")
        elif self.stoploss and self.profit <= -self.stoploss:
            self.halt("🔴 <b>STOP LOSS</b>", "stop")

    def halt(self, title, kind):
        self.running = False
        self.tg.notify("%s\nP/L %s%s\n\nstopped."
                       % (title, "+" if self.profit >= 0 else "", ks(self.profit)))

    def start(self):
        if not self.cfg.get("demo_mode") and not self.logged_in:
            return {"ok": False, "msg": "not logged in"}
        with self.lock:
            if self.running:
                return {"ok": False, "msg": "already running"}
            self.tg.notify("⏳ <b>LOADING</b>")
            live = self._fetch_live()
            if live:
                self.ingest_results(live["results"])
            if self.cfg.get("demo_mode"):
                self.balance = float(self.cfg.get("demo_balance", 10000.0))
            self.profit = 0.0
            self.level = 0
            self.pending = None
            self.running = True
            self.start_pending = True
            self.started_at = datetime.now()
            self.rounds_since_search = 0
        self.persist()
        return {"ok": True}

    def stop(self):
        with self.lock:
            self.running = False
        return {"ok": True}

    def status_text(self):
        r = self.last_round or {}
        L = ["<b>🧠 WINGO BOT v5</b>", ""]
        L.append(("🟢 <b>RUNNING</b>" if self.running else "⚪ <b>STOPPED</b>"))
        L.append("🎭 mode: <b>%s</b>" % ("DEMO" if self.cfg.get("demo_mode") else "REAL"))
        L.append("💰 %s · P/L %s%s"
                 % (ks(self.balance), "+" if self.profit >= 0 else "",
                    ks(abs(self.profit))))
        L.append("🎚 L%d/%d · next %s"
                 % (self.level + 1, len(self.ladder), ks(self.next_stake())))
        L.append("🎯 target %s · 🛑 SL %s"
                 % (ks(self.target), ks(self.stoploss)))
        L.append("🎯 top N: <b>%d</b> · results: %d"
                 % (self.top_n, len(self.results)))
        L.append("🔄 auto-switch: <b>%s</b> every <b>%d</b> · %d/%d"
                 % ("ON" if self.auto_switch else "OFF",
                    self.auto_every, self.rounds_since_search, self.auto_every))
        if r.get("issue"):
            L.append("🎯 #%s → <b>%s</b> (%.0f%%)"
                     % (esc(r["issue"]), esc(r.get("call", "-")),
                        r.get("confidence") or 0))
        if self.pending:
            L.append("🔒 in play #%s %s %s"
                     % (esc(self.pending["issue"]), esc(self.pending["call"]),
                        ks(self.pending["stake"])))
        if self.history:
            w = sum(1 for h in self.history if h["won"])
            L.append("📚 %d · <b>%dW</b>/%dL"
                     % (len(self.history), w, len(self.history) - w))
        return "\n".join(L)

    def help_text(self):
        return ("<b>🧠 WINGO Bot v%s</b>\n\n" % VERSION +
                "▶️ START · ⏹️ STOP\n"
                "📊 STATUS · 💰 BALANCE\n"
                "🧠 STRATEGY · 🧮 FORMULAS\n"
                "🔄 AUTO SWITCH — formula ရှာ + အစားထိုး\n"
                "🎭 DEMO MODE · 🎯 TOP N\n"
                "🔑 LOGIN · 🔒 SESSION · ⚙️ SETTINGS\n\n"
                "<code>/set ladder 100,200,500</code>\n"
                "<code>/set target 5000</code>\n"
                "<code>/set stoploss 2000</code>\n"
                "<code>/set topn 2</code>\n"
                "<code>/autoswitch on|off</code>\n"
                "<code>/autoevery 5|10|20|50</code>\n"
                "<code>/autoswitchnow</code> — ချက်ချင်း ရှာ\n"
                "<code>/demo</code> · <code>/demoreset</code>")

    def apply_set(self, what, val):
        what = (what or "").lower()
        if what in ("ladder", "bet", "stakes"):
            p = parse_numbers(val)
            if not p:
                return "⚠️ ladder"
            self.ladder = [int(v) for v in p]
            self.level = min(self.level, len(self.ladder) - 1)
            self.persist()
            return "🎚 <b>%s</b>" % " → ".join(str(x) for x in self.ladder)
        if what in ("target", "tp"):
            n = (parse_numbers(val, single=True) or [0])[0]
            if n <= 0:
                return "⚠️ target"
            self.target = n
            self.persist()
            return "🎯 %s" % ks(n)
        if what in ("stoploss", "sl"):
            n = (parse_numbers(val, single=True) or [0])[0]
            if n <= 0:
                return "⚠️ stoploss"
            self.stoploss = n
            self.persist()
            return "🛑 %s" % ks(n)
        if what in ("topn", "top"):
            n = (parse_numbers(val, single=True) or [0])[0]
            if n not in (1, 2, 3):
                return "⚠️ 1/2/3"
            self.top_n = n
            self.persist()
            return "🎯 top N = <b>%d</b>" % n
        if what in ("autoevery", "every"):
            n = (parse_numbers(val, single=True) or [0])[0]
            if n not in (5, 10, 20, 50):
                return "⚠️ 5/10/20/50"
            self.auto_every = n
            self.rounds_since_search = 0
            self.persist()
            return "🔄 auto every = <b>%d</b>" % n
        return "⚠️ ladder / target / stoploss / topn / autoevery"

    def login_start(self):
        self.awaiting = "phone"
        self.tmp_phone = ""
        self.tg.send("🔑 <b>LOGIN</b>\n\n<b>Phone</b> ကို ပို့ပါ:", CANCEL_KB)

    def answer_step(self, text):
        if self.awaiting == "phone":
            ph = re.sub(r"[^0-9]", "", text) or text.strip()
            self.tmp_phone = ph
            self.awaiting = "password"
            self.tg.send("✅ <code>%s</code>\n<b>Password</b> ကို ပို့ပါ:"
                         % esc(ph), CANCEL_KB)
            return
        if self.awaiting == "password":
            self.awaiting = None
            self.tg.send("⏳ logging in…")
            r = self.feed.login(self.tmp_phone, text)
            self.tmp_phone = ""
            if r.get("ok"):
                self.logged_in = True
                self.tg.send("✅ <b>%s</b>" % esc(r.get("user")))
            else:
                self.tg.send("❌ " + esc(r.get("msg")))
            return
        if self.awaiting in ASK:
            kind = self.awaiting
            msg = self.apply_set(kind, text)
            if msg.startswith("⚠️"):
                self.tg.send(msg + "\n❌ CANCEL", CANCEL_KB)
                return
            self.awaiting = None
            self.tg.send(msg)
            return
        self.awaiting = None

    def stake_card(self, issue, lvl, call, market, stake, amount, count, gt,
                   confidence=50.0, demo=False):
        tag = "🧪 DEMO" if demo else "💰 REAL"
        return ("👑 <b>PRIME BOT • %s</b>\n"
                "🎫 <code>%s</code>\n"
                "✩ %s · %s\n"
                "🎯 L%d · 🔢 %d×%d\n"
                "🧠 %.1f%% · 💳 %s"
                % (tag, esc(issue), esc(call), ks(stake),
                   lvl + 1, amount, count, confidence, ks(self.balance)))

    def result_card(self, win, num, stake, pl, lvl, demo=False):
        call = self.history[-1]["call"] if self.history else "?"
        issue = self.history[-1]["issue"] if self.history else "?"
        tag = "🧪 DEMO · " if demo else ""
        return ("%s%s <b>%s%s</b>\n🎫 %s\n🎯 %s → 🎲 %d %s\n💳 %s · P/L %s%s"
                % (tag, "✅ WIN" if win else "❌ LOSS",
                   "+" if pl >= 0 else "−", ks(abs(pl)),
                   esc(issue), esc(call), num, size_of(num),
                   ks(self.balance),
                   "+" if self.profit >= 0 else "−", ks(abs(self.profit))))

    def strategy_text(self):
        digits = self.digits()
        L = ["<b>🧮 STRATEGY</b>", ""]
        L.append("results: <b>%d</b>" % len(digits))
        if len(digits) < 20:
            return "\n".join(L + ["⏳ history < 20"])
        pred = formula_backtest(digits)
        L.append("formulas: <b>%d</b>" % pred.get("formulaCount"))
        L.append("top N: <b>%d</b>" % self.top_n)
        L.append("")
        for i, x in enumerate(pred.get("ranked") or [], 1):
            L.append("%d. %s <b>%s</b> · %.1f%%"
                     % (i, esc(x.get("label")), esc(x.get("pred") or "-"),
                        x.get("rate") or 0))
        top = pred.get("top%d" % self.top_n) or {}
        L.append("")
        L.append("🎯 <b>Top%d</b> → <b>%s</b>"
                 % (self.top_n, esc(top.get("pred") or "-")))
        return "\n".join(L)

    def formulas_text(self):
        state = load_formula_state()
        sums, diffs = active_patterns(state)
        L = ["🧮 <b>FORMULAS</b> · %d sum + %d diff = %d"
             % (len(sums), len(diffs), len(sums) + len(diffs)), ""]
        all_items = sums + diffs
        for i, x in enumerate(all_items[:20], 1):
            L.append("%d. %s" % (i, esc(formula_label(x))))
        if len(all_items) > 20:
            L.append("... +%d" % (len(all_items) - 20))
        return "\n".join(L)

    def auto_switch_text(self):
        L = ["🔄 <b>AUTO-SWITCH</b>", ""]
        L.append("status: <b>%s</b>" % ("ON" if self.auto_switch else "OFF"))
        L.append("every: <b>%d</b> rounds" % self.auto_every)
        L.append("progress: <b>%d / %d</b>"
                 % (self.rounds_since_search, self.auto_every))
        L.append("add: <b>%d</b> formulas per switch" % self.auto_add_count)
        L.append("")
        L.append("<i>ပွဲ %d ခုတိုင်း ဖော်မြူလာ အသစ် ရှာပြီး</i>" % self.auto_every)
        L.append("<i>trx_formulas.json ကို အဟောင်းဖျက်၊ အသစ်ထည့်မယ်</i>")
        return "\n".join(L)

    def auto_switch_menu(self):
        return {"inline_keyboard": [
            [{"text": "🔄 %s" % ("ON → OFF" if self.auto_switch else "OFF → ON"),
              "callback_data": "as:toggle"}],
            [{"text": "5 rounds", "callback_data": "as:every:5"},
             {"text": "10 rounds", "callback_data": "as:every:10"},
             {"text": "20 rounds", "callback_data": "as:every:20"},
             {"text": "50 rounds", "callback_data": "as:every:50"}],
            [{"text": "🚀 Switch NOW", "callback_data": "as:now"}],
            [{"text": "📊 Status", "callback_data": "as:status"}],
        ]}

    def on_command(self, kind, payload):
        if kind == "callback":
            data = payload
            if data == "as:toggle":
                self.auto_switch = not self.auto_switch
                self.persist()
                self.tg.send("🔄 auto-switch <b>%s</b>"
                             % ("ON" if self.auto_switch else "OFF"))
                return
            if data.startswith("as:every:"):
                n = int(data.split(":")[-1])
                if n in (5, 10, 20, 50):
                    self.auto_every = n
                    self.rounds_since_search = 0
                    self.persist()
                    self.tg.send("🔄 every <b>%d</b> rounds" % n)
                return
            if data == "as:now":
                self.tg.send("⏳ searching…")
                threading.Thread(target=lambda: self.auto_switch_run(force=True)
                                 or self.tg.send("🔄 <b>switch done</b>"),
                                 daemon=True).start()
                return
            if data == "as:status":
                self.tg.send(self.auto_switch_text())
                return
            return

        t = payload.strip()
        low = t.lower()
        raw = t.split()
        cmd = re.sub(r"@[a-z0-9_]+$", "", raw[0].lower()) if raw else ""
        parts = low.split()

        try:
            if self.awaiting:
                if cmd == "/cancel" or t == "❌ CANCEL":
                    self.awaiting = None
                    self.tmp_phone = ""
                    self.tg.send("❌ cancelled.", MENU)
                    return
                if t in BUTTONS:
                    self.awaiting = None
                    self.tmp_phone = ""
                    self.tg.send("↩️ cancelled.")
                else:
                    self.answer_step(t)
                    return

            if t == "▶️ START" or cmd in ("/run", "/start_bot"):
                r = self.start()
                if not r["ok"]:
                    self.tg.send("⚠️ " + esc(r["msg"]))
                else:
                    self.tg.send("▶️ <b>STARTED</b>")
                return
            if t == "⏹️ STOP" or cmd == "/stop":
                self.stop()
                self.tg.send("⏹️ <b>STOPPED</b>")
                return
            if cmd in ("/start", "/help") or t == "❓ HELP":
                self.tg.send(self.help_text())
                return
            if t == "📊 STATUS" or cmd in ("/status", "/stat"):
                self.tg.send(self.status_text())
                return
            if t == "💰 BALANCE" or cmd in ("/balance", "/bal"):
                if self.cfg.get("demo_mode"):
                    self.tg.send("🎭 <b>%s</b>" % ks(self.balance))
                    return
                b = self.feed.balance()
                if b is None:
                    self.tg.send("⚠️ " + esc(self.feed.last_error))
                else:
                    self.balance = b
                    self.tg.send("💰 <b>%s</b>" % ks(b))
                return
            if t == "🧠 STRATEGY" or cmd == "/strategy":
                self.tg.send(self.strategy_text())
                return
            if t == "🧮 FORMULAS" or cmd == "/formulas":
                self.tg.send(self.formulas_text())
                return
            if t == "🔄 AUTO SWITCH" or cmd == "/autoswitch":
                self.tg.send(self.auto_switch_text(), self.auto_switch_menu())
                return
            if cmd == "/autoevery":
                if len(raw) >= 2:
                    self.tg.send(self.apply_set("autoevery", raw[1]))
                else:
                    self.tg.send(self.auto_switch_menu())
                return
            if cmd == "/autoswitchnow":
                self.tg.send("⏳ searching…")
                threading.Thread(target=lambda: self.auto_switch_run(force=True)
                                 or self.tg.send("🔄 done"), daemon=True).start()
                return
            if t == "🎯 TOP N" or cmd == "/topn":
                if len(raw) >= 2:
                    self.tg.send(self.apply_set("topn", raw[1]))
                else:
                    self.ask("top_n")
                return
            if t == "⚙️ SETTINGS" or cmd == "/settings":
                self.tg.send("⚙️ ladder <b>%s</b>\ntarget <b>%s</b> · SL <b>%s</b>\ntop N <b>%d</b> · auto every <b>%d</b>"
                             % (" → ".join(str(x) for x in self.ladder),
                                ks(self.target), ks(self.stoploss),
                                self.top_n, self.auto_every))
                return
            if t == "🔑 LOGIN":
                self.login_start()
                return
            if cmd == "/login":
                if len(raw) >= 3:
                    self.tg.send("⏳ logging in…")
                    r = self.feed.login(raw[1], " ".join(raw[2:]))
                    if r.get("ok"):
                        self.logged_in = True
                        self.tg.send("✅ <b>%s</b>" % esc(r.get("user")))
                    else:
                        self.tg.send("❌ " + esc(r.get("msg")))
                    return
                self.login_start()
                return
            if t == "🔒 SESSION" or cmd == "/session":
                L = ["<b>🔒 SESSION</b>", ""]
                L.append("app.py: %s" % ("✅" if self.bridge.health() else "⚪"))
                L.append("feed: %s" % ("✅" if self.feed.issue() else "❌"))
                L.append("mode: <b>%s</b>"
                         % ("DEMO" if self.cfg.get("demo_mode") else "REAL"))
                L.append("account: " + ("<b>%s</b>"
                                       % esc(local_phone(self.feed.phone))
                                       if self.logged_in else "<i>none</i>"))
                L.append("balance: <b>%s</b>" % ks(self.balance))
                L.append("results: <b>%d</b>" % len(self.results))
                self.tg.send("\n".join(L))
                return
            if t == "🎭 DEMO MODE" or cmd == "/demo":
                self.cfg["demo_mode"] = not self.cfg.get("demo_mode", False)
                save_settings(self.cfg)
                if self.cfg["demo_mode"]:
                    self.balance = float(self.cfg.get("demo_balance", 10000.0))
                    self.tg.send("🎭 <b>DEMO ON</b> · %s" % ks(self.balance))
                else:
                    b = self.feed.balance() if self.logged_in else None
                    if b is not None:
                        self.balance = b
                    self.tg.send("💰 <b>REAL ON</b> · %s" % ks(self.balance))
                return
            if cmd == "/demoreset":
                self.cfg["demo_balance"] = 10000.0
                if self.cfg.get("demo_mode"):
                    self.balance = 10000.0
                save_settings(self.cfg)
                self.tg.send("🎭 reset → 10,000 Ks")
                return
            if cmd == "/set":
                if len(parts) < 2:
                    self.tg.send("⚠️ /set ladder|target|stoploss|topn|autoevery …")
                    return
                self.tg.send(self.apply_set(parts[1], " ".join(parts[2:])))
                return
            if cmd == "/ping":
                self.tg.send("🏓")
                return
            self.tg.send("🤔 unknown — /help")
        except Exception:
            log("cmd error:\n" + traceback.format_exc())
            self.tg.send("⚠️ %s" % esc(traceback.format_exc().splitlines()[-1]))

    def ask(self, kind):
        self.awaiting = kind
        self.tg.send(ASK[kind], CANCEL_KB)

def parse_numbers(text, single=False):
    t = str(text or "").lower().strip().replace("ks", " ").replace("k", "000")
    groups = [g for g in re.split(r"[^0-9]+", t) if g]
    if not groups:
        return []
    if single or any(set(g) == {"0"} for g in groups):
        v = int("".join(groups))
        return [v] if v > 0 else []
    return [int(g) for g in groups if int(g) > 0]

def check_mode(bot):
    print("  version → %s" % VERSION)
    state = load_formula_state()
    sums, diffs = active_patterns(state)
    print("  formulas: %d sum + %d diff = %d"
          % (len(sums), len(diffs), len(sums) + len(diffs)))
    print("  auto-switch: %s every %d"
          % ("ON" if bot.auto_switch else "OFF", bot.auto_every))
    print("  app.py: " + ("✅" if bot.bridge.health() else "⚪"))
    me = bot.tg.get_me()
    print("  telegram: " + ("✅ @%s" % (me["data"] or {}).get("username")
                            if me["ok"] else "❌ " + str(me.get("msg"))))
    iss = bot.feed.issue()
    print("  feed: " + ("✅ #%s" % iss["issue"] if iss else "⚠️ " + bot.feed.last_error))
    live = bot._fetch_live()
    if live:
        bot.ingest_results(live["results"])
        digits = bot.digits()
        print("  results: %d" % len(digits))
        if len(digits) >= 20:
            pred = formula_backtest(digits)
            print("  top1: %s · top2: %s · top3: %s"
                  % ((pred.get("top1") or {}).get("pred") or "-",
                     (pred.get("top2") or {}).get("pred") or "-",
                     (pred.get("top3") or {}).get("pred") or "-"))
    return 0

def main():
    print("  BOZAW v%s — Formula + Auto-Switch + Demo" % VERSION)
    cfg = load_settings()
    if not cfg.get("bot_token"):
        sys.exit("no bot token")
    bot = Bot(cfg)

    if "--check" in sys.argv:
        sys.exit(check_mode(bot))

    if cfg.get("phone") and cfg.get("password") and not cfg.get("demo_mode"):
        r = bot.feed.login(cfg["phone"], cfg["password"])
        if r.get("ok"):
            bot.logged_in = True
            print("  login: ok")
        else:
            print("  login: %s" % r.get("msg"))

    threading.Thread(target=bot.run_forever, name="betting", daemon=True).start()
    threading.Thread(target=lambda: bot.tg.poll(bot.on_command),
                     name="telegram", daemon=True).start()

    me = bot.tg.get_me()
    if me["ok"]:
        bot.tg.send("🤖 <b>BOZAW v%s</b>\nadmin <code>%s</code>\n\n%s"
                    % (VERSION, esc(bot.tg.admin), bot.help_text()))

    print("  running. Ctrl+C to quit.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        bot.persist()
        print("\n  stopped.")

if __name__ == "__main__":
    main()