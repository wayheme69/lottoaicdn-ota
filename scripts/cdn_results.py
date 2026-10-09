#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cdn_results.py — robot LOTTO AI CDN v4 : écrit cdn_results.json (30 derniers tirages Lotto 6/49 et Lotto Max avec
le vrai lot et le nombre de gagnants de chaque catégorie, le Gold Ball, le prochain tirage et son gros lot) et
cdn_archive.json (tous les tirages depuis 2025, pour les anciennes grilles des utilisateurs).

Sources :
  1. API officielle PlayNow (BCLC, membre de l'ILC)  /services2/lotto/draw/{six49|lmax}/{yyyy-mm-dd} + /jackpot/{jeu}
  2. WCLC (Western Canada Lottery Corporation), page des numéros gagnants  (recoupement)
Contrôles : numéros valides selon l'époque (Max 1-52 depuis le 14/04/2026), numéros de tirage CONSÉCUTIFS sans trou,
dates croissantes, recoupement WCLC des derniers tirages (numéros + complémentaire) : sinon le robot échoue et
rien n'est publié. PN_RESOLVE=IP force l'adresse de playnow (réseau à DNS détourné, en local).

Format : {"updated", "six49": [{date, draw, numbers[6], bonus, payouts{"6","5+B","5","4","3","2+B","2"}, winners{...},
          goldBall{ticket, gold, prize}}], "lmax": [{... numbers[7], payouts{"7" … "3"} ...}],
          "next": {"six49": {date, jackpot}, "lmax": {date, jackpot}}}
payouts[k] = null quand personne n'a gagné cette catégorie ; tirages du plus récent au plus ancien.
"""
import json, os, re, subprocess, sys, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT, ARCH = ROOT / "cdn_results.json", ROOT / "cdn_archive.json"
KEEP, ARCH_FROM = 30, "2025-01-01"
PN = "www.playnow.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
GAMES = {"six49": dict(n=6, wd=(2, 5), ranks=["6", "5+B", "5", "4", "3", "2+B", "2"]),
         "lmax": dict(n=7, wd=(1, 4), ranks=["7", "6+B", "6", "5+B", "5", "4+B", "4", "3+B", "3"])}
WCLC = {"six49": "https://www.wclc.com/winning-numbers/lotto-649-extra.htm",
        "lmax": "https://www.wclc.com/winning-numbers/lotto-max-extra.htm"}


def curl(url, extra=None, timeout=45):
    cmd = ["curl", "-sL", "--max-time", str(timeout), "-A", UA] + (extra or [])
    ip = os.environ.get("PN_RESOLVE")
    if ip and PN in url:
        cmd += ["--resolve", f"{PN}:443:{ip}"]
    r = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout + 20)
    return r.stdout if r.returncode == 0 else ""


def pn(path, tries=3):
    for i in range(tries):
        body = curl(f"https://{PN}{path}")
        if body.strip().startswith("{"):
            try:
                return json.loads(body)
            except ValueError:
                pass
        if body.strip() == "" and i < tries - 1:
            time.sleep(2 * (i + 1)); continue
        return None
    return None


def top(game, d):
    return 49 if game == "six49" else (52 if d >= "2026-04-14" else (50 if d >= "2019-05-14" else 49))


def check(game, d):
    g, t = GAMES[game], top(game, d["date"])
    allb = d["numbers"] + [d["bonus"]]
    if len(d["numbers"]) != g["n"] or len(set(allb)) != len(allb) or not all(1 <= v <= t for v in allb):
        raise SystemExit(f"{game} {d['date']} : numéros invalides {d['numbers']} + {d['bonus']}")


def fetch(game, day):
    """Tirage officiel d'un jour (None s'il n'y a pas eu de tirage)."""
    j = pn(f"/services2/lotto/draw/{game}/{day}")
    if not j or "drawNbrs" not in j:
        return None
    ks = GAMES[game]["ranks"]
    payouts = {k: None for k in ks}; winners = {k: 0 for k in ks}
    for r in j.get("gameBreakdown") or []:
        p = r.get("prizeDiv") or 0
        if 1 <= p <= len(ks):
            k = ks[p - 1]
            w = int(r.get("winnersTotal") or 0)
            winners[k] = max(winners[k], w)
            v = float(r.get("prizeAmount") or 0)
            if w > 0 and v > 0:
                payouts[k] = v
    d = dict(date=day, draw=int(j["drawNbr"]), numbers=sorted(int(x) for x in j["drawNbrs"]), bonus=int(j["bonusNbr"]),
             payouts=payouts, winners=winners)
    if game == "six49":
        gp = (j.get("gpNumbers") or [None])[0]
        if isinstance(gp, dict) and isinstance(gp.get("drawNbrs"), list) and len(gp["drawNbrs"]) >= 8:
            t = "".join(str(x) for x in gp["drawNbrs"])
            gold = bool(gp.get("goldBallDrawn"))
            prize = gp.get("goldBallPrizeAmount") if gold else gp.get("whiteBallPrizeAmount")
            d["goldBall"] = dict(ticket=f"{t[:8]}-{t[8:]}" if len(t) > 8 else t, gold=gold,
                                 prize=float(prize) if prize else None)
    check(game, d)
    return d


def wclc(game):
    """Derniers tirages publiés par la WCLC (numéros + complémentaire), recoupement."""
    html = curl(WCLC[game])
    if "pastWinNumber" not in html:
        html = curl(f"https://r.jina.ai/{WCLC[game]}", ["-H", "X-Return-Format: html"], 90)
    out = {}
    months = {m: i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                               "August", "September", "October", "November", "December"])}
    for ch in re.split(r'(?=\w+day,\s+\w+\s+\d{1,2},\s+\d{4})', html):
        m = re.search(r'(\w+day),\s+(\w+)\s+(\d{1,2}),\s+(\d{4})', ch)
        if not m or m.group(2) not in months:
            continue
        mains = [int(x) for x in re.findall(r'pastWinNumber["\s>]+(\d{1,2})\s*<', ch)[:GAMES[game]["n"]]]
        b = re.search(r'pastWinNumberBonus">\s*(?:<span[^>]*>[^<]*</span>)?\s*(\d{1,2})\s*<', ch)
        if len(mains) == GAMES[game]["n"] and b:
            out[f"{int(m.group(4)):04d}-{months[m.group(2)]:02d}-{int(m.group(3)):02d}"] = (sorted(mains), int(b.group(1)))
    return out


def build(game, arch):
    g = GAMES[game]
    today = datetime.now(timezone(timedelta(hours=-5))).date()
    have = {d["date"]: d for d in arch.get(game, [])}
    out, day, misses = [], today, 0
    # On remonte jour par jour (tirages déplacés compris) jusqu'à 30 tirages
    while len(out) < KEEP and (today - day).days < 200:
        ds = day.isoformat()
        recent = (today - day).days <= 8
        sched = day.weekday() in g["wd"]
        if ds in have and not recent:
            out.append(have[ds])
        elif sched or recent:
            d = fetch(game, ds)
            if d:
                out.append(d)
            elif sched and (today - day).days > 1:
                misses += 1
            time.sleep(0.4)
        day -= timedelta(days=1)
    if len(out) < KEEP:
        raise SystemExit(f"{game} : seulement {len(out)} tirages trouvés")
    # Numéros de tirage consécutifs, dates dans l'ordre
    for a, b in zip(out, out[1:]):
        if a["draw"] != b["draw"] + 1 or a["date"] <= b["date"]:
            raise SystemExit(f"{game} : suite cassée {b['draw']} ({b['date']}) → {a['draw']} ({a['date']})")
    if (today - date.fromisoformat(out[0]["date"])).days > 6:
        raise SystemExit(f"{game} : dernier tirage {out[0]['date']} trop ancien")
    # Recoupement WCLC
    w = wclc(game); agreed = 0
    for d in out[:4]:
        if d["date"] in w:
            if w[d["date"]] != (d["numbers"], d["bonus"]):
                raise SystemExit(f"{game} {d['date']} : PlayNow {d['numbers']}+{d['bonus']} ≠ WCLC {w[d['date']]}")
            agreed += 1
    if agreed == 0 and w:
        raise SystemExit(f"{game} : aucun tirage commun avec la WCLC ({sorted(w)[-3:]})")
    print(f"{game} : {len(out)} tirages ({out[-1]['draw']}–{out[0]['draw']}), {agreed} recoupés WCLC"
          + (" — WCLC injoignable" if not w else ""), file=sys.stderr)
    j = pn(f"/services2/lotto/jackpot/{game}") or {}
    nd = str(j.get("nextDrawDate") or "")[:10]
    nxt = {"date": nd if re.fullmatch(r"\d{4}-\d{2}-\d{2}", nd) else None,
           "jackpot": float(j["jackpot"]) if j.get("jackpot") else None}
    return out, nxt


def seed_archive(game, arch):
    """Remplit l'archive depuis ARCH_FROM (première exécution, ou trous)."""
    have = {d["date"] for d in arch.get(game, [])}
    day = date.fromisoformat(ARCH_FROM); end = datetime.now(timezone.utc).date()
    rows = list(arch.get(game, []))
    while day <= end:
        ds = day.isoformat()
        if day.weekday() in GAMES[game]["wd"] and ds not in have:
            d = fetch(game, ds)
            if d:
                rows.append(d)
            time.sleep(0.3)
        day += timedelta(days=1)
    arch[game] = rows


def main():
    arch = json.loads(ARCH.read_text()) if ARCH.exists() else {}
    if "--seed" in sys.argv:
        for g in GAMES:
            seed_archive(g, arch)
    data = {"six49": None, "lmax": None, "next": {}}
    for g in GAMES:
        data[g], data["next"][g] = build(g, arch)
        by = {d["date"]: d for d in arch.get(g, []) if d["date"] >= ARCH_FROM}
        for d in data[g]:
            by[d["date"]] = d
        rows = sorted(by.values(), key=lambda d: d["date"], reverse=True)
        for a, b in zip(rows, rows[1:]):
            if a["draw"] != b["draw"] + 1:
                print(f"  ⚠️ archive {g} : trou entre {b['draw']} et {a['draw']}", file=sys.stderr)
        arch[g] = rows
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    if {k: old.get(k) for k in data} != data:
        OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **data},
                                  ensure_ascii=False, separators=(",", ":")))
        print(f"cdn_results.json : 6/49 {data['six49'][0]['draw']} ({data['six49'][0]['date']}), "
              f"Max {data['lmax'][0]['draw']} ({data['lmax'][0]['date']}), next {data['next']}")
    else:
        print("cdn_results.json : aucun changement.")
    ARCH.write_text(json.dumps({g: arch[g] for g in GAMES}, ensure_ascii=False, separators=(",", ":")))
    print(f"cdn_archive.json : 6/49 {len(arch['six49'])}, Max {len(arch['lmax'])} tirages")


if __name__ == "__main__":
    main()
