"""Fingerprint-Modell der Raumerkennung – reines Python, keine HA-Importe.

Ein Raum besteht aus einer oder mehreren Komponenten (Positionen im Raum). Jede Komponente
hält pro Scanner eine Statistik aus der Kalibrierung und eine aus dem Selbstlernen. Der
Abgleich eines Messfensters ist eine Naive-Bayes-Log-Likelihood über alle Scanner:
Gauß auf dem RSSI, wenn der Scanner das Gerät gehört hat, sonst die Wahrscheinlichkeit,
es *nicht* zu hören. Genau das trennt Räume, deren stärkster Scanner gleich ist – die
übrigen Scanner liefern das Unterscheidungsmerkmal.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

# Untergrenze der Streuung (dB) – verhindert, dass wenige ruhige Kalibrierfenster ein zu
# spitzes Modell erzeugen.
MIN_SD = 4.0
# Abweichungen über so viele Standardabweichungen zählen nicht mehr stärker (Ausreißer).
MAX_Z = 3.5
P_SEEN_MIN = 0.05
P_SEEN_MAX = 0.95
# Neutrale Annahme für Scanner, über die ein Raum (noch) nichts weiß.
NEUTRAL_P_SEEN = 0.5
NEUTRAL_MEAN = -85.0
NEUTRAL_SD = 15.0
# Mindestens so viele Fenster mit gehörtem Gerät, bevor eine Gauß-Statistik zählt.
MIN_SEEN_FOR_GAUSS = 3

# Pegelversatz je Fenster (Hosentasche, Hand, Körper, anderes Gerät): wird je Raum mitgeschätzt
# und herausgerechnet, mit dieser Streuung als Vorwissen (dB). None = aus (absolute Pegel).
GAIN_PRIOR_SD: float | None = 8.0
# Empfangsschwelle der Scanner (dB). Mit dem Pegelversatz verschiebt sich, wie wahrscheinlich
# ein Scanner das Gerät überhaupt hört: 8 dB leiser → ein Scanner bei −92 dBm hört meist nichts mehr.
DETECTION_FLOOR = -98.0

# Selbstlernen: Anteil des Gelernten am Modell höchstens 30 % → Kalibrierung ≥ 70 %.
LEARN_SHARE_MAX = 0.3
# Gelerntes vergisst langsam: effektive Fensterzahl wird auf diesen Wert gedeckelt.
LEARN_MAX_WINDOWS = 600.0
# Ein Scanner, den die Kalibrierung nicht kannte, zählt erst nach so vielen gelernten Fenstern.
MIN_LEARNED_FOR_NEW_SCANNER = 30.0
# Kalibrierung endet von selbst, sobald sie reicht: frühestens nach so vielen Fenstern ...
CAL_MIN_WINDOWS = 8
# ... wenn die Mittelwerte der gut hörbaren Scanner auf diesen Standardfehler (dB) stehen ...
CAL_MAX_STDERR = 2.5
# ... und der Raum sich in den bisherigen Fenstern so gut von allen anderen unterscheidet.
CAL_TARGET_SEPARABILITY = 0.95
# Umschaltzeit (Fenster), die bei der geglätteten Trennschärfe nicht mitzählt.
SEQUENCE_WARMUP = 3
# So viele Kalibrierfenster je Komponente werden aufgehoben (für die Trennschärfe-Prüfung).
MAX_SAMPLES = 120


@dataclass
class Window:
    """Ein Messfenster: geglätteter RSSI je Scanner, der das Gerät gehört hat."""

    rssi: dict[str, float]
    # Scanner, die während des Fensters aktiv waren (gehört oder nicht).
    active: set[str]

    @property
    def seen_count(self) -> int:
        return len(self.rssi)

    def to_dict(self) -> dict[str, Any]:
        return {"rssi": {k: round(v, 1) for k, v in self.rssi.items()}, "active": sorted(self.active)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Window:
        return cls(
            rssi={k: float(v) for k, v in data.get("rssi", {}).items()},
            active=set(data.get("active", [])),
        )


def robust_mean(values: list[float]) -> float:
    """Mittel innerhalb des Tukey-Zauns (wie ESPresense), Fallback Median."""
    if len(values) < 4:
        return statistics.median(values)
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    inside = [v for v in values if lo <= v <= hi]
    return statistics.fmean(inside) if inside else statistics.median(values)


@dataclass
class Stat:
    """Gewichtete Statistik eines Scanners: Präsenz, Sichtbarkeit, RSSI-Mittel/Varianz."""

    present: float = 0.0  # Fenster, in denen der Scanner aktiv war
    seen: float = 0.0  # davon: Gerät gehört
    mean: float = 0.0
    m2: float = 0.0  # Summe der quadrierten Abweichungen (Welford, gewichtet mit seen)

    def add(self, rssi: float | None) -> None:
        self.present += 1.0
        if rssi is None:
            return
        self.seen += 1.0
        delta = rssi - self.mean
        self.mean += delta / self.seen
        self.m2 += delta * (rssi - self.mean)

    def remove(self, rssi: float | None) -> None:
        """Umkehrung von add() – für Leave-one-out-Prüfungen."""
        self.present -= 1.0
        if rssi is None:
            return
        if self.seen <= 1.0:
            self.seen, self.mean, self.m2 = 0.0, 0.0, 0.0
            return
        old_mean = self.mean
        self.seen -= 1.0
        self.mean = (old_mean * (self.seen + 1.0) - rssi) / self.seen
        self.m2 = max(0.0, self.m2 - (rssi - self.mean) * (rssi - old_mean))

    def copy(self) -> Stat:
        return Stat(self.present, self.seen, self.mean, self.m2)

    def scale(self, factor: float) -> None:
        """Gewicht verringern (Vergessen), Mittel bleibt, Varianz bleibt."""
        self.present *= factor
        self.seen *= factor
        self.m2 *= factor

    @property
    def var(self) -> float:
        return self.m2 / self.seen if self.seen > 0 else 0.0

    def to_dict(self) -> dict[str, float]:
        return {"present": self.present, "seen": self.seen, "mean": self.mean, "m2": self.m2}

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> Stat:
        return cls(**{k: float(data[k]) for k in ("present", "seen", "mean", "m2")})


@dataclass
class Effective:
    """Zusammengeführte Kenngrößen eines Scanners für eine Komponente."""

    p_seen: float
    mean: float
    sd: float
    has_gauss: bool


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class Component:
    """Eine Position in einem Raum."""

    cal: dict[str, Stat] = field(default_factory=dict)
    learned: dict[str, Stat] = field(default_factory=dict)
    cal_windows: int = 0
    learned_windows: float = 0.0
    # Kalibrierfenster (gedeckelt) – Grundlage der Trennschärfe-Prüfung.
    samples: list[Window] = field(default_factory=list)

    def add_calibration(self, window: Window) -> None:
        self.cal_windows += 1
        for scanner in window.active | window.rssi.keys():
            self.cal.setdefault(scanner, Stat()).add(window.rssi.get(scanner))
        self.samples.append(window)
        del self.samples[:-MAX_SAMPLES]

    def is_converged(self) -> bool:
        """Pegel der gut hörbaren Scanner (≥ 50 % gehört) sind ausreichend genau bestimmt."""
        relevant = [
            st for st in self.cal.values() if st.present and st.seen / st.present >= 0.5
        ]
        if not relevant:
            return False
        return all(
            max(MIN_SD, math.sqrt(st.var)) / math.sqrt(st.seen) <= CAL_MAX_STDERR
            for st in relevant
        )

    def without(self, window: Window) -> Component:
        """Kopie, aus deren Kalibrierung ein Fenster herausgerechnet ist."""
        cal = {k: v.copy() for k, v in self.cal.items()}
        for scanner in window.active | window.rssi.keys():
            if scanner in cal:
                cal[scanner].remove(window.rssi.get(scanner))
        return Component(
            cal=cal,
            learned=self.learned,
            cal_windows=self.cal_windows - 1,
            learned_windows=self.learned_windows,
        )

    def add_learned(self, window: Window) -> None:
        self.learned_windows += 1.0
        for scanner in window.active | window.rssi.keys():
            self.learned.setdefault(scanner, Stat()).add(window.rssi.get(scanner))
        if self.learned_windows > LEARN_MAX_WINDOWS:
            factor = LEARN_MAX_WINDOWS / self.learned_windows
            self.learned_windows = LEARN_MAX_WINDOWS
            for stat in self.learned.values():
                stat.scale(factor)

    def reset_learned(self) -> None:
        self.learned.clear()
        self.learned_windows = 0.0

    def effective(self, scanner: str) -> Effective | None:
        """Kenngrößen aus Kalibrierung und Gelerntem, oder None wenn unbekannt."""
        cal = self.cal.get(scanner)
        lrn = self.learned.get(scanner)
        cal_ok = cal is not None and cal.present > 0
        lrn_ok = lrn is not None and lrn.present > 0
        if not cal_ok and not (lrn_ok and lrn.present >= MIN_LEARNED_FOR_NEW_SCANNER):
            return None
        if cal_ok and lrn_ok:
            w = min(LEARN_SHARE_MAX, lrn.present / (lrn.present + cal.present))
            parts = [(1.0 - w, cal), (w, lrn)]
        elif cal_ok:
            parts = [(1.0, cal)]
        else:
            parts = [(1.0, lrn)]

        p_seen = sum(w * (s.seen / s.present) for w, s in parts)
        gauss_parts = [(w, s) for w, s in parts if s.seen >= MIN_SEEN_FOR_GAUSS]
        if gauss_parts:
            total = sum(w for w, _ in gauss_parts)
            mean = sum(w * s.mean for w, s in gauss_parts) / total
            second = sum(w * (s.var + s.mean**2) for w, s in gauss_parts) / total
            sd = max(MIN_SD, math.sqrt(max(0.0, second - mean**2)))
            has_gauss = True
        else:
            mean, sd, has_gauss = NEUTRAL_MEAN, NEUTRAL_SD, False
        return Effective(_clamp(p_seen, P_SEEN_MIN, P_SEEN_MAX), mean, sd, has_gauss)

    def scanners(self) -> set[str]:
        return set(self.cal) | set(self.learned)

    def gain(self, window: Window) -> float:
        """Gemeinsamer Pegelversatz des Fensters gegenüber dieser Komponente (MAP-Schätzung).

        Gewichteter Mittelwert der Abweichungen aller gehörten Scanner, zur 0 hin gezogen.
        Hört das Gerät überall 6 dB leiser (Hosentasche), ist das Muster trotzdem dasselbe.
        """
        if GAIN_PRIOR_SD is None:
            return 0.0
        num, den = 0.0, 1.0 / GAIN_PRIOR_SD**2
        for scanner, rssi in window.rssi.items():
            eff = self.effective(scanner)
            if eff is None or not eff.has_gauss:
                continue
            weight = 1.0 / eff.sd**2
            num += weight * (rssi - eff.mean)
            den += weight
        return num / den

    def scanner_log_likelihood(self, window: Window, scanner: str, gain: float = 0.0) -> float:
        rssi = window.rssi.get(scanner)
        eff = self.effective(scanner)
        if eff is None:
            p_seen, mean, sd = NEUTRAL_P_SEEN, NEUTRAL_MEAN, NEUTRAL_SD
        else:
            p_seen, mean, sd = eff.p_seen, eff.mean + gain, eff.sd
            if gain and eff.has_gauss:
                p_seen = _shift_p_seen(p_seen, eff.mean, gain, sd)
        if rssi is None:
            return math.log(1.0 - p_seen)
        z = min(abs(rssi - mean) / sd, MAX_Z)
        return math.log(p_seen) - 0.5 * z * z - math.log(sd)

    def log_likelihood(self, window: Window, scanners: set[str]) -> float:
        g = self.gain(window)
        prior = -0.5 * (g / GAIN_PRIOR_SD) ** 2 if GAIN_PRIOR_SD else 0.0
        return prior + sum(self.scanner_log_likelihood(window, s, g) for s in scanners)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cal": {k: v.to_dict() for k, v in self.cal.items()},
            "learned": {k: v.to_dict() for k, v in self.learned.items()},
            "cal_windows": self.cal_windows,
            "learned_windows": self.learned_windows,
            "samples": [w.to_dict() for w in self.samples],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Component:
        return cls(
            cal={k: Stat.from_dict(v) for k, v in data.get("cal", {}).items()},
            learned={k: Stat.from_dict(v) for k, v in data.get("learned", {}).items()},
            cal_windows=int(data.get("cal_windows", 0)),
            learned_windows=float(data.get("learned_windows", 0.0)),
            samples=[Window.from_dict(w) for w in data.get("samples", [])],
        )


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _shift_p_seen(p_seen: float, mean: float, gain: float, sd: float) -> float:
    """Sichtbarkeit unter einem Pegelversatz: Anteil der Verteilung über der Empfangsschwelle."""
    before = _norm_cdf((mean - DETECTION_FLOOR) / sd)
    after = _norm_cdf((mean + gain - DETECTION_FLOOR) / sd)
    if before <= 1e-6:
        return p_seen
    return _clamp(p_seen * after / before, P_SEEN_MIN, P_SEEN_MAX)


def _logsumexp(values: list[float]) -> float:
    top = max(values)
    return top + math.log(sum(math.exp(v - top) for v in values))


@dataclass
class FingerprintModel:
    """Alle Räume eines Geräts."""

    rooms: dict[str, list[Component]] = field(default_factory=dict)

    def begin_calibration(self, room: str, replace: bool) -> Component:
        """Neue Komponente anlegen. replace=True verwirft alles Bisherige dieses Raums."""
        component = Component()
        if replace or room not in self.rooms:
            self.rooms[room] = [component]
        else:
            self.rooms[room].append(component)
        return component

    def calibrated_rooms(self, allowed: set[str] | None = None) -> list[str]:
        return [
            room
            for room, comps in self.rooms.items()
            if any(c.cal_windows > 0 for c in comps) and (allowed is None or room in allowed)
        ]

    def log_likelihoods(
        self, window: Window, allowed: set[str] | None = None
    ) -> dict[str, float]:
        """Log-Likelihood je kalibriertem Raum für ein Fenster."""
        rooms = self.calibrated_rooms(allowed)
        # Alle Räume werden über dieselbe Scanner-Menge bewertet (die gerade aktiven), sonst
        # wären Summen über unterschiedlich viele Terme nicht vergleichbar. Ein ausgefallener
        # Scanner zählt nicht als „Gerät nicht gehört“.
        scanners = set(window.active) | set(window.rssi)
        return {room: self._room_ll(self.rooms[room], window, scanners) for room in rooms}

    @staticmethod
    def _room_ll(comps: list[Component], window: Window, scanners: set[str]) -> float:
        lls = [c.log_likelihood(window, scanners) for c in comps if c.cal_windows > 0]
        if not lls:
            return -math.inf
        return _logsumexp(lls) - math.log(len(lls))

    def confusion(
        self, allowed: set[str] | None = None, only: str | None = None
    ) -> dict[str, dict[str, float]]:
        """Leave-one-out: Anteil der Kalibrierfenster eines Raums, die als welcher Raum erkannt werden.

        Jedes Fenster wird gegen ein Modell geprüft, aus dem genau dieses Fenster herausgerechnet
        ist – sonst wäre das Ergebnis zu optimistisch. Bewertet wird das einzelne Fenster ohne
        zeitliche Glättung, also eher pessimistisch.
        """
        rooms = self.calibrated_rooms(allowed)
        result: dict[str, dict[str, float]] = {}
        for room in rooms:
            if only is not None and room != only:
                continue
            counts: dict[str, int] = {}
            total = 0
            for idx, comp in enumerate(self.rooms[room]):
                for sample in comp.samples:
                    scanners = set(sample.active) | set(sample.rssi)
                    own = list(self.rooms[room])
                    own[idx] = comp.without(sample)
                    lls = {
                        other: self._room_ll(own if other == room else self.rooms[other], sample, scanners)
                        for other in rooms
                    }
                    best = max(lls, key=lls.__getitem__)
                    counts[best] = counts.get(best, 0) + 1
                    total += 1
            if total:
                result[room] = {r: n / total for r, n in sorted(counts.items(), key=lambda i: -i[1])}
        return result

    def sequence_accuracy(
        self, allowed: set[str] | None = None, only: str | None = None
    ) -> dict[str, float]:
        """Trennschärfe mit zeitlicher Glättung wie live (Leave-one-out je Fenster).

        Jede Position wird als Zeitfolge durch einen RoomDecider geschickt, der im ähnlichsten
        anderen Raum startet – als käme man gerade von dort herein. Gezählt wird nach einer
        kurzen Umschaltzeit, ob der richtige Raum angezeigt wird. Ohne diesen Start würde die
        Klebrigkeit selbst ununterscheidbare Räume als „sicher“ ausweisen.
        """
        rooms = self.calibrated_rooms(allowed)
        result: dict[str, float] = {}
        for room in rooms:
            if only is not None and room != only:
                continue
            sequences: list[list[dict[str, float]]] = []
            for idx, comp in enumerate(self.rooms[room]):
                seq = []
                for sample in comp.samples:
                    scanners = set(sample.active) | set(sample.rssi)
                    own = list(self.rooms[room])
                    own[idx] = comp.without(sample)
                    seq.append(
                        {
                            other: self._room_ll(
                                own if other == room else self.rooms[other], sample, scanners
                            )
                            for other in rooms
                        }
                    )
                sequences.append(seq)
            others = [r for r in rooms if r != room]
            if not others:
                result[room] = 1.0
                continue
            flat = [lls for seq in sequences for lls in seq]
            if not flat:
                continue
            rival = max(others, key=lambda r: sum(lls[r] - lls[room] for lls in flat))
            hits = total = 0
            for seq in sequences:
                decider = RoomDecider(room=rival, posterior={rival: 0.9})
                warmup = SEQUENCE_WARMUP if len(seq) > SEQUENCE_WARMUP else 0
                for k, lls in enumerate(seq):
                    shown = decider.step(lls).room
                    if k >= warmup:
                        hits += shown == room
                        total += 1
            if total:
                result[room] = hits / total
        return result

    def calibration_complete(self, room: str, component: Component, allowed: set[str] | None = None) -> bool:
        """Reicht die laufende Kalibrierung? Stabil und (falls es andere Räume gibt) sicher trennbar."""
        if component.cal_windows < CAL_MIN_WINDOWS or not component.is_converged():
            return False
        if len(self.calibrated_rooms(allowed)) < 2:
            return True
        # Maßstab ist die geglättete Erkennung (wie live); einzelne Ausreißer-Fenster schluckt sie.
        return self.sequence_accuracy(allowed, only=room).get(room, 0.0) >= CAL_TARGET_SEPARABILITY

    def explain(self, window: Window, room_a: str, room_b: str) -> list[tuple[str, float]]:
        """Scanner, die am stärksten für room_a gegen room_b sprechen (Log-Likelihood-Differenz)."""
        scanners = set(window.active) | set(window.rssi)

        def best(room: str) -> Component | None:
            comps = [c for c in self.rooms.get(room, []) if c.cal_windows > 0]
            return max(comps, key=lambda c: c.log_likelihood(window, scanners)) if comps else None

        a, b = best(room_a), best(room_b)
        if a is None or b is None:
            return []
        ga, gb = a.gain(window), b.gain(window)
        diffs = [
            (s, a.scanner_log_likelihood(window, s, ga) - b.scanner_log_likelihood(window, s, gb))
            for s in scanners
        ]
        return sorted(diffs, key=lambda i: -i[1])

    def learn(self, room: str, window: Window) -> None:
        """Fenster in die am besten passende Komponente des Raums einlernen."""
        comps = [c for c in self.rooms.get(room, []) if c.cal_windows > 0]
        if not comps:
            return
        scanners = set(window.active) | set(window.rssi)
        best = max(comps, key=lambda c: c.log_likelihood(window, scanners))
        best.add_learned(window)

    def reset_learned(self) -> None:
        for comps in self.rooms.values():
            for comp in comps:
                comp.reset_learned()

    def remove_room(self, room: str) -> None:
        self.rooms.pop(room, None)

    def summary(self) -> dict[str, dict[str, float]]:
        """Kalibrier- und Lernstand je Raum (Fenster)."""
        return {
            room: {
                "positionen": len(comps),
                "kalibrierfenster": sum(c.cal_windows for c in comps),
                "gelernte_fenster": round(sum(c.learned_windows for c in comps), 1),
            }
            for room, comps in self.rooms.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {room: [c.to_dict() for c in comps] for room, comps in self.rooms.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FingerprintModel:
        if not data:
            return cls()
        return cls(
            rooms={room: [Component.from_dict(c) for c in comps] for room, comps in data.items()}
        )


# --- Entscheidung über die Zeit (HMM-Vorwärtsschritt mit Klebrigkeit) ---------------------

# Skalierung der Log-Likelihood: Scanner sind nicht unabhängig, das reine Naive-Bayes-Produkt
# wäre deutlich zu selbstsicher.
LIK_SCALE = 0.5
# Wahrscheinlichkeit pro Fenster, den Raum zu wechseln (Übergangsmodell).
SWITCH_PROB = 0.05
POSTERIOR_FLOOR = 1e-4
# Raumwechsel: neuer Raum muss so viele Fenster in Folge mit mindestens dieser Wahrscheinlichkeit vorn liegen.
SWITCH_MIN_POSTERIOR = 0.6
CONFIRM_WINDOWS = 2
# Selbstlernen nur bei sicherer, stabiler Erkennung.
LEARN_MIN_POSTERIOR = 0.9
LEARN_MIN_MARGIN = 0.8
LEARN_STREAK_WINDOWS = 20


@dataclass
class Decision:
    room: str | None
    posterior: dict[str, float]
    learn_room: str | None = None


@dataclass
class RoomDecider:
    posterior: dict[str, float] = field(default_factory=dict)
    room: str | None = None
    candidate: str | None = None
    candidate_count: int = 0
    learn_streak: int = 0

    def reset(self) -> None:
        self.posterior.clear()
        self.room = None
        self.candidate = None
        self.candidate_count = 0
        self.learn_streak = 0

    def step(self, log_likelihoods: dict[str, float]) -> Decision:
        rooms = list(log_likelihoods)
        if not rooms:
            self.reset()
            return Decision(None, {})
        n = len(rooms)
        # Prior: bisherige Verteilung, Räume ohne Vorgeschichte gleichverteilt.
        prev = {r: self.posterior.get(r, 1.0 / n) for r in rooms}
        prev_total = sum(prev.values())
        prior = {
            r: (1.0 - SWITCH_PROB) * prev[r] / prev_total + SWITCH_PROB / n for r in rooms
        }
        top = max(log_likelihoods.values())
        unnorm = {
            r: prior[r] * math.exp(LIK_SCALE * (log_likelihoods[r] - top)) for r in rooms
        }
        total = sum(unnorm.values())
        post = {r: max(POSTERIOR_FLOOR, v / total) for r, v in unnorm.items()}
        total = sum(post.values())
        self.posterior = {r: v / total for r, v in post.items()}

        ranked = sorted(rooms, key=self.posterior.__getitem__, reverse=True)
        best = ranked[0]
        p_best = self.posterior[best]
        p_second = self.posterior[ranked[1]] if n > 1 else 0.0

        if self.room is None or self.room not in log_likelihoods:
            self.room = best
            self.candidate, self.candidate_count = None, 0
        elif best != self.room and p_best >= SWITCH_MIN_POSTERIOR:
            if best == self.candidate:
                self.candidate_count += 1
            else:
                self.candidate, self.candidate_count = best, 1
            if self.candidate_count >= CONFIRM_WINDOWS:
                self.room = best
                self.candidate, self.candidate_count = None, 0
                self.learn_streak = 0
        else:
            self.candidate, self.candidate_count = None, 0

        learn_room = None
        if (
            best == self.room
            and p_best >= LEARN_MIN_POSTERIOR
            and p_best - p_second >= LEARN_MIN_MARGIN
        ):
            self.learn_streak += 1
            if self.learn_streak >= LEARN_STREAK_WINDOWS:
                learn_room = self.room
        else:
            self.learn_streak = 0
        return Decision(self.room, dict(self.posterior), learn_room)


# --- Drift über feste Referenzgeräte ---------------------------------------------------------

# Fenster, aus denen die Ausgangslage eines Referenzgeräts je Scanner gebildet wird.
BASELINE_WINDOWS = 50
# Gleitender Mittelwert der aktuellen Lage (Gewicht pro Fenster; 1/100 ≈ 10 min bei 6-s-Fenstern).
DRIFT_ALPHA = 0.01
# Mindestens so viele Fenster seit Ausgangslage, bevor eine Drift zählt.
DRIFT_MIN_WINDOWS = 30
# Drift wird auf diesen Betrag begrenzt (dB) – größere Sprünge sind eher Umbau als Drift.
DRIFT_MAX = 12.0


@dataclass
class AnchorScanner:
    """Ausgangslage und aktuelle Lage eines Referenzgeräts an einem Scanner."""

    baseline: Stat = field(default_factory=Stat)
    current: float | None = None
    windows: int = 0

    def add(self, rssi: float) -> None:
        if self.baseline.seen < BASELINE_WINDOWS:
            self.baseline.add(rssi)
            return
        self.current = rssi if self.current is None else (
            (1.0 - DRIFT_ALPHA) * self.current + DRIFT_ALPHA * rssi
        )
        self.windows += 1

    @property
    def drift(self) -> float | None:
        if self.current is None or self.windows < DRIFT_MIN_WINDOWS:
            return None
        return self.current - self.baseline.mean

    def to_dict(self) -> dict[str, Any]:
        return {"baseline": self.baseline.to_dict(), "current": self.current, "windows": self.windows}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnchorScanner:
        return cls(
            baseline=Stat.from_dict(data["baseline"]),
            current=data.get("current"),
            windows=int(data.get("windows", 0)),
        )


@dataclass
class DriftEstimator:
    """Schätzt pro Scanner, um wie viel dB er gegenüber der Ausgangslage verschoben ist.

    Feste Geräte (z.B. Thermometer) bewegen sich nicht. Hört ein Scanner sie alle plötzlich
    schwächer oder stärker, hat sich der Scanner verändert (Firmware, getauscht, verrückt) –
    das betrifft dann auch das verfolgte Gerät. Median über alle Referenzgeräte, damit ein
    einzelnes umgestelltes Referenzgerät nicht alles verschiebt.
    """

    anchors: dict[str, dict[str, AnchorScanner]] = field(default_factory=dict)

    def add(self, anchor: str, rssi_by_scanner: dict[str, float]) -> None:
        per_scanner = self.anchors.setdefault(anchor, {})
        for scanner, rssi in rssi_by_scanner.items():
            per_scanner.setdefault(scanner, AnchorScanner()).add(rssi)

    def drift(self) -> dict[str, float]:
        values: dict[str, list[float]] = {}
        for per_scanner in self.anchors.values():
            for scanner, state in per_scanner.items():
                if (d := state.drift) is not None:
                    values.setdefault(scanner, []).append(d)
        return {
            s: _clamp(statistics.median(v), -DRIFT_MAX, DRIFT_MAX) for s, v in values.items()
        }

    def retain(self, anchors: set[str]) -> None:
        for anchor in list(self.anchors):
            if anchor not in anchors:
                del self.anchors[anchor]

    def reset(self) -> None:
        self.anchors.clear()

    def to_dict(self) -> dict[str, Any]:
        return {a: {s: st.to_dict() for s, st in per.items()} for a, per in self.anchors.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> DriftEstimator:
        if not data:
            return cls()
        return cls(
            anchors={
                a: {s: AnchorScanner.from_dict(st) for s, st in per.items()}
                for a, per in data.items()
            }
        )


def apply_drift(window: Window, drift: dict[str, float]) -> Window:
    """RSSI eines Fensters in die Ausgangslage zurückrechnen."""
    if not drift:
        return window
    return Window(
        rssi={s: v - drift.get(s, 0.0) for s, v in window.rssi.items()}, active=window.active
    )
