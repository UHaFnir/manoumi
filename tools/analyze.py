"""Offline-Auswertung einer MaNoUmi-Aufzeichnung (capture.jsonl).

    python tools/analyze.py capture.jsonl [scanner_names.json]

Scanner-Namen kommen aus der „scanners“-Kopfzeile der Aufzeichnung oder aus einer JSON-Datei
{MAC: Name}.

Referenzgeräte stehen fest in bekannten Räumen und dienen als „Testpersonen“:
  1. Zeit-Split: erste Hälfte jedes Referenzgeräts kalibriert seinen Raum, zweite Hälfte wird
     erkannt (einzelnes Fenster und mit zeitlicher Glättung wie live).
  2. Fremdposition: Räume mit mehreren Referenzgeräten – mit einem kalibrieren, das andere
     erkennen (ehrlichster Test: andere Stelle im selben Raum).
Dazu RSSI-Statistik je Referenzgerät und Scanner.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.manoumi.model import (
    FingerprintModel,
    RoomDecider,
    Window,
)


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def window(rec: dict) -> Window:
    return Window(rssi={k: float(v) for k, v in rec["rssi"].items()}, active=set(rec["active"]))


def accuracy(model: FingerprintModel, windows: list[Window], room: str) -> tuple[float, float, dict]:
    single_hits, smooth_hits, wrong = 0, 0, {}
    decider = RoomDecider()
    for w in windows:
        lls = model.log_likelihoods(w)
        best = max(lls, key=lls.__getitem__)
        single_hits += best == room
        if best != room:
            wrong[best] = wrong.get(best, 0) + 1
        smooth_hits += decider.step(lls).room == room
    n = max(1, len(windows))
    return single_hits / n, smooth_hits / n, wrong


NAMES: dict[str, str] = {}


def name(source: str) -> str:
    return NAMES.get(source, source)


def main(path: str, names_path: str | None = None) -> None:
    all_records = load(path)
    for r in all_records:
        if r["kind"] == "scanners":
            NAMES.update({s: i["name"] for s, i in r["scanners"].items()})
    if names_path:
        with open(names_path, encoding="utf-8") as fh:
            NAMES.update(json.load(fh))
    records = [r for r in all_records if r["kind"] == "anchor" and r.get("label")]
    by_anchor: dict[str, list[dict]] = {}
    for r in records:
        by_anchor.setdefault(r["id"], []).append(r)
    labels = {a: recs[0]["label"] for a, recs in by_anchor.items()}
    print(f"{len(records)} Fenster von {len(by_anchor)} Referenzgeräten\n")

    print("== RSSI je Referenzgerät (Median ± Streuung, Anteil gehört) ==")
    for anchor, recs in sorted(by_anchor.items(), key=lambda i: labels[i[0]]):
        per: dict[str, list[float]] = {}
        for r in recs:
            for s, v in r["rssi"].items():
                per.setdefault(s, []).append(v)
        print(f"- {labels[anchor]} [{anchor}] {len(recs)} Fenster")
        for s, vals in sorted(per.items(), key=lambda i: -statistics.median(i[1])):
            sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            print(f"    {name(s):28s} {statistics.median(vals):6.1f} ± {sd:4.1f}  ({len(vals) / len(recs):.0%})")

    print("\n== 1. Zeit-Split (erste Hälfte kalibriert, zweite erkannt) ==")
    model = FingerprintModel()
    tests: dict[str, list[Window]] = {}
    for anchor, recs in by_anchor.items():
        half = len(recs) // 2
        comp = model.begin_calibration(labels[anchor], replace=False)
        for r in recs[:half]:
            comp.add_calibration(window(r))
        tests[anchor] = [window(r) for r in recs[half:]]
    for anchor, ws in tests.items():
        single, smooth, wrong = accuracy(model, ws, labels[anchor])
        print(f"- {labels[anchor]:14s} einzeln {single:5.0%}  geglättet {smooth:5.0%}  falsch: {wrong}")
    print("\nLeave-one-out auf den Kalibrierfenstern (wie die Trennschärfe in HA):")
    for room, dist in model.confusion().items():
        print(f"- {room:14s} {dist.get(room, 0):5.0%}  {dict(list(dist.items())[:3])}")

    print("\n== 2. Fremdposition (Räume mit mehreren Referenzgeräten) ==")
    rooms: dict[str, list[str]] = {}
    for anchor, room in labels.items():
        rooms.setdefault(room, []).append(anchor)
    multi = {r: a for r, a in rooms.items() if len(a) > 1}
    if not multi:
        print("- keine Räume mit mehreren Referenzgeräten")
    for room, anchors in multi.items():
        for test_anchor in anchors:
            m = FingerprintModel()
            for anchor, recs in by_anchor.items():
                if anchor == test_anchor:
                    continue
                comp = m.begin_calibration(labels[anchor], replace=False)
                for r in recs:
                    comp.add_calibration(window(r))
            single, smooth, wrong = accuracy(m, [window(r) for r in by_anchor[test_anchor]], room)
            print(f"- {room} [{test_anchor}] einzeln {single:5.0%}  geglättet {smooth:5.0%}  falsch: {wrong}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
