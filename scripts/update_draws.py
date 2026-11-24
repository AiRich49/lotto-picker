#!/usr/bin/env python3
"""Add new UK National Lottery draws to data/*.csv.

Sources: the lottery.co.uk yearly archive pages (all draws this year) and the
official National Lottery "latest draw" feed. A draw is added only when it is
newer than the last row in the CSV and passes the checks below. If the two
sources disagree about a draw, it is skipped and reported, never guessed.
Standard library only, so it runs on a plain GitHub Actions runner.
"""
import csv, datetime as dt, os, re, sys, urllib.request, xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (compatible; lotto-picker-updater; +https://github.com/AiRich49/lotto-picker)"
LOCAL = os.environ.get("LOCAL_HTML_DIR")  # for offline testing only

GAMES = {
    "lotto":        dict(slug="lotto", main=6, max=59, xmax=59, nx=1, days={2, 5}, xtype="bonusball"),
    "thunderball":  dict(slug="thunderball", main=5, max=39, xmax=14, nx=1, days={1, 2, 4, 5}, cls=("thunderball-ball", "thunderball-thunderball"), xtype="thunderball"),
    "euromillions": dict(slug="euromillions", main=5, max=50, xmax=12, nx=2, days={1, 4}, cls=("euromillions-ball", "euromillions-lucky-star"), xtype="luckystar"),
    "setforlife":   dict(slug="set-for-life", main=5, max=47, xmax=10, nx=1, days={0, 3}, cls=("setForLife-ball", "setForLife-life-ball"), xtype="life ball"),
}

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")

def balls(row, cls):
    return [int(x) for x in re.findall(r'class="result small %s[^"]*"[^>]*>\s*(\d+)\s*<' % re.escape(cls), row)]

def parse_archive(game, html):
    """Return {date: [ (main tuple, extras tuple, round), ... ]}"""
    g = GAMES[game]; out = {}
    for row in re.split(r"<tr[\s>]", html):
        m = re.search(r'/results-(\d{2})-(\d{2})-(\d{4})"', row)
        if not m: continue
        d = "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))
        sets = []
        if game == "lotto":
            for rnd in (1, 2):
                mn, bx = balls(row, "lotto-ball-round-%d" % rnd), balls(row, "lotto-bonus-ball-round-%d" % rnd)
                if mn: sets.append((tuple(sorted(mn)), tuple(bx), rnd))
            if not sets:
                mn, bx = balls(row, "lotto-ball"), balls(row, "lotto-bonus-ball")
                if mn: sets.append((tuple(sorted(mn)), tuple(bx), 1))
        else:
            mn, ex = balls(row, g["cls"][0]), balls(row, g["cls"][1])
            if mn: sets.append((tuple(sorted(mn)), tuple(sorted(ex)), 1))
        if sets: out[d] = sets
    return out

def parse_official(game, xml):
    g = GAMES[game]; root = ET.fromstring(xml); out = {}
    d = root.findtext(".//draw/draw-date")
    if not d: return out
    sets = []
    for i, b in enumerate(root.iter("balls"), start=1):
        mn = [int(x.text) for x in b.findall("ball")]
        ex = [int(x.text) for x in b.findall("bonus-ball")]
        if mn: sets.append((tuple(sorted(mn)), tuple(sorted(ex)) if game != "lotto" else tuple(ex), i if game == "lotto" else 1))
    if sets: out[d] = sets
    return out

def valid(game, d, s):
    g = GAMES[game]; mn, ex, rnd = s
    try: wd = dt.date.fromisoformat(d).weekday()
    except ValueError: return "bad date"
    if wd not in g["days"]: return "not a draw day"
    if len(mn) != g["main"] or len(set(mn)) != len(mn): return "wrong main numbers"
    if not all(1 <= x <= g["max"] for x in mn): return "main number out of range"
    if game == "lotto":
        if len(ex) > 1 or any(not 1 <= x <= 59 for x in ex): return "bad bonus"
    else:
        if len(ex) != g["nx"] or len(set(ex)) != len(ex) or not all(1 <= x <= g["xmax"] for x in ex): return "bad extra balls"
    return None

def row_for(game, d, s):
    mn, ex, rnd = s
    if game == "lotto": return [d, str(rnd)] + [str(x) for x in mn] + [str(ex[0]) if ex else "0"]
    return [d] + [str(x) for x in mn] + [str(x) for x in ex]

def main():
    today = dt.date.today(); years = {today.year}
    if today.month == 1: years.add(today.year - 1)
    report, changed = [], False
    for game, g in GAMES.items():
        path = os.path.join(ROOT, "data", game + ".csv")
        with open(path, newline="") as f: rows = list(csv.reader(f))
        header, body = rows[0], rows[1:]
        last = max(r[0] for r in body); have = {(r[0], r[1]) if game == "lotto" else r[0] for r in body}
        arch, off = {}, {}
        for y in sorted(years):
            try:
                html = open(os.path.join(LOCAL, "lc-%s.html" % g["slug"]), encoding="utf-8").read() if LOCAL else \
                       get("https://www.lottery.co.uk/%s/results/archive-%d" % (g["slug"], y))
                arch.update(parse_archive(game, html))
            except Exception as e: report.append("%s: archive %d not read (%s)" % (game, y, e))
        try:
            xml = open(os.path.join(LOCAL, "nl-%s.csv" % g["slug"]), encoding="utf-8").read() if LOCAL else \
                  get("https://www.national-lottery.co.uk/results/%s/draw-history/csv" % g["slug"])
            off = parse_official(game, xml)
        except Exception as e: report.append("%s: official feed not read (%s)" % (game, e))
        added = []
        for d in sorted(set(arch) | set(off)):
            if d <= last: continue
            a, o = arch.get(d), off.get(d)
            if a and o and sorted(a) != sorted(o):
                report.append("%s %s: sources disagree, skipped (archive %s, official %s)" % (game, d, a, o)); continue
            for s in (a or o):
                why = valid(game, d, s)
                if why: report.append("%s %s: skipped, %s %s" % (game, d, why, s)); continue
                r = row_for(game, d, s); key = (r[0], r[1]) if game == "lotto" else r[0]
                if key in have: continue
                body.append(r); have.add(key); added.append(r)
        if added:
            body.sort(key=lambda r: (r[0], r[1]) if game == "lotto" else r[0])
            with open(path, "w", newline="") as f:
                w = csv.writer(f, lineterminator="\n"); w.writerow(header); w.writerows(body)
            changed = True
            report.append("%s: added %s" % (game, "; ".join(" ".join(r) for r in added)))
        else:
            report.append("%s: no new draws (latest %s)" % (game, last))
    print("\n".join(report))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f: f.write("\n".join("- " + x for x in report) + "\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
