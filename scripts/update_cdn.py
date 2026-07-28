#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_cdn.py — met à jour cdn_recent.json (Lotto 6/49 + Lotto Max) pour LOTTO AI CDN.

Sources (leçons ES/IT 28/07 intégrées : succès = données VALIDES seulement, échec
BRUYANT, no-op réel, fusion bornée auto-guérissante, sonde de fraîcheur) :
  • PRIMAIRE : API PlayNow/BCLC — /services2/lotto/draw/{six49|lmax}/{date} +
    /services2/lotto/jackpot/{six49|lmax} (User-Agent requis ; Akamai peut bloquer
    les runners → fallback).
  • FALLBACK : WCLC winning-numbers (HTML, Cloudflare) en direct puis via
    r.jina.ai X-Return-Format html — mains+bonus+Gold Ball des ~8 derniers tirages.

Sortie : cdn_recent.json
  {"updated",
   "six49": {"draws":[{date,drawNbr,numbers[6],bonus,goldBallTicket,goldBallDrawn}],
             "next":{date,jackpot_cad,goldBallJackpot_cad,goldBallsRemaining,whiteBallsRemaining}},
   "lmax":  {"draws":[{date,drawNbr,numbers[7],bonus}],
             "next":{date,jackpot_cad,maxplusCount}}}
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone, date

PN_HOST = "www.playnow.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
WCLC = {"six49": "https://www.wclc.com/winning-numbers/lotto-649-extra.htm",
        "lmax": "https://www.wclc.com/winning-numbers/lotto-max-extra.htm"}
MAX_ERA_52 = "2026-04-14"


def max_date():
    return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")


def curl(url, extra=None, timeout=45):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA] + (extra or []) + [url]
    # Réseau à DNS détourné (dev local) : PN_RESOLVE=IP force --resolve sur playnow.
    ip = os.environ.get("PN_RESOLVE")
    if ip and PN_HOST in url:
        cmd = cmd[:1] + ["--resolve", f"{PN_HOST}:443:{ip}"] + cmd[1:]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
    return r.stdout if r.returncode == 0 else ""


def pn_json(path):
    body = curl(f"https://{PN_HOST}{path}")
    if body.strip().startswith("{"):
        try:
            return json.loads(body)
        except ValueError:
            return None
    return None


def pool(game, d):
    return 49 if game == "six49" else (52 if d >= MAX_ERA_52 else 50)


def valid(game, d, nums, bonus):
    n = 6 if game == "six49" else 7
    p = pool(game, d)
    return (len(nums) == n and len(set(nums)) == n
            and all(1 <= x <= p for x in nums)
            and 1 <= bonus <= p and bonus not in nums
            and "2009-01-01" <= d <= max_date())


# --------------------------- PlayNow (primaire) ---------------------------

def fetch_pn(game, known_dates):
    """Tirages depuis PlayNow pour les ~15 derniers jours + jackpot/next."""
    jp = pn_json(f"/services2/lotto/jackpot/{game}")
    draws = []
    today = date.today()
    for back in range(0, 16):
        d = (today - timedelta(days=back)).isoformat()
        if d in known_dates:
            continue
        wd = date.fromisoformat(d).weekday()
        if game == "six49" and wd not in (2, 5):
            continue
        if game == "lmax" and wd not in (1, 4):
            continue
        j = pn_json(f"/services2/lotto/draw/{game}/{d}")
        if not j or "drawNbrs" not in j:
            continue
        nums = sorted(int(x) for x in j["drawNbrs"])
        bonus = int(j["bonusNbr"])
        if not valid(game, d, nums, bonus):
            raise SystemExit(f"{game} {d}: valeurs invalides {nums}+{bonus}")
        row = {"date": d, "drawNbr": int(j["drawNbr"]), "numbers": nums, "bonus": bonus}
        if game == "six49":
            # gpNumbers = LISTE d'objets Gold Ball ; drawNbrs = les 10 chiffres du billet.
            gplist = j.get("gpNumbers") or []
            gp = gplist[0] if isinstance(gplist, list) and gplist else {}
            digits = gp.get("drawNbrs")
            if isinstance(digits, list) and len(digits) >= 8:
                t = "".join(str(x) for x in digits)
                row["goldBallTicket"] = f"{t[:8]}-{t[8:]}" if len(t) > 8 else t
            if isinstance(gp.get("goldBallDrawn"), bool):
                row["goldBallDrawn"] = gp["goldBallDrawn"]
        draws.append(row)
    nxt = None
    if jp and jp.get("nextDrawDate"):
        nd = str(jp["nextDrawDate"])[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", nd):
            nxt = {"date": nd}
            try:
                nxt["jackpot_cad"] = int(float(jp.get("jackpot") or 0))
            except (TypeError, ValueError):
                pass
            if game == "six49":
                for src, dst in [("six49GPJackpot", "goldBallJackpot_cad"),
                                 ("six49GPGoldBallsRemaining", "goldBallsRemaining"),
                                 ("six49GPWhiteBallsRemaining", "whiteBallsRemaining")]:
                    try:
                        nxt[dst] = int(float(jp[src]))
                    except (KeyError, TypeError, ValueError):
                        pass
            else:
                try:
                    nxt["maxplusCount"] = int(jp.get("additionalDrawsCount") or 0)
                except (TypeError, ValueError):
                    pass
    return draws, nxt


# --------------------------- WCLC (fallback) ---------------------------

WCLC_DATE = re.compile(r'(\w+day),\s+(\w+)\s+(\d{1,2}),\s+(\d{4})')
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def fetch_wclc(game):
    html = curl(WCLC[game])
    if "pastWinNumber" not in html:
        html = curl(f"https://r.jina.ai/{WCLC[game]}", ["-H", "X-Return-Format: html"], 90)
    if "pastWinNumber" not in html:
        return []
    n = 6 if game == "six49" else 7
    draws = []
    # Blocs par date : la page liste les tirages du mois courant, du plus récent au plus ancien.
    chunks = re.split(r'(?=\w+day,\s+\w+\s+\d{1,2},\s+\d{4})', html)
    for ch in chunks:
        md = WCLC_DATE.search(ch)
        if not md or md.group(2) not in MONTHS:
            continue
        d = f"{int(md.group(4)):04d}-{MONTHS[md.group(2)]:02d}-{int(md.group(3)):02d}"
        mains = [int(x) for x in re.findall(r'pastWinNumber["\s>]+(\d{1,2})\s*<', ch)[:n]]
        bm = re.search(r'pastWinNumberBonus["\s>]+(\d{1,2})\s*<', ch)
        if len(mains) != n or not bm:
            continue
        nums, bonus = sorted(mains), int(bm.group(1))
        if not valid(game, d, nums, bonus):
            continue
        draws.append({"date": d, "drawNbr": 0, "numbers": nums, "bonus": bonus})
    return draws


# --------------------------- Assemblage ---------------------------

def merge(old_draws, fresh, cap=12):
    by = {}
    for r in old_draws or []:
        # bornage de l'existant -> auto-guérison d'un JSON pollué (leçon IT)
        if "2009-01-01" <= r.get("date", "") <= max_date():
            by[r["date"]] = r
    for r in fresh:
        prev = by.get(r["date"])
        # ne pas écraser un enregistrement RICHE (drawNbr/GoldBall) par un pauvre (WCLC)
        if prev and prev.get("drawNbr") and not r.get("drawNbr"):
            continue
        by[r["date"]] = r
    return sorted(by.values(), key=lambda x: x["date"], reverse=True)[:cap]


try:
    with open("cdn_recent.json") as f:
        old = json.load(f)
except (OSError, ValueError):
    old = {}

content = {}
for game in ("six49", "lmax"):
    known = {r["date"] for r in (old.get(game) or {}).get("draws", [])}
    draws, nxt = fetch_pn(game, set())
    if not draws and not nxt:
        print(f"  {game}: PlayNow KO -> fallback WCLC", file=sys.stderr)
        draws = fetch_wclc(game)
        if not draws and not (old.get(game) or {}).get("draws"):
            raise SystemExit(f"{game}: toutes les sources ont échoué")
    merged = merge((old.get(game) or {}).get("draws"), draws)
    if not merged:
        raise SystemExit(f"{game}: aucun tirage")
    # Sonde de fraîcheur (anti-Magayo) : 2 tirages/sem -> écart max normal 4 j (+ marge)
    age = (datetime.now(timezone.utc).date()
           - datetime.strptime(merged[0]["date"], "%Y-%m-%d").date()).days
    if age > 6:
        raise SystemExit(f"{game}: flux périmé — dernier tirage {merged[0]['date']} ({age} j)")
    content[game] = {"draws": merged, "next": nxt or (old.get(game) or {}).get("next")}

old_cmp = dict(old)
old_cmp.pop("updated", None)
if old_cmp == content:
    print("Aucune nouvelle donnée — cdn_recent.json inchangé.", file=sys.stderr)
    raise SystemExit(0)

payload = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **content}
with open("cdn_recent.json", "w") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1)
s49, lmx = content["six49"]["draws"][0], content["lmax"]["draws"][0]
print(f"OK: 649 {s49['date']} {s49['numbers']}+{s49['bonus']} next={content['six49']['next']}; "
      f"Max {lmx['date']} {lmx['numbers']}+{lmx['bonus']} next={content['lmax']['next']}", file=sys.stderr)
