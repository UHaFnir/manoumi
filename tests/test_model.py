"""Tests für das reine Fingerprint-Modell."""

from __future__ import annotations

import random

from custom_components.manoumi.model import (
    LEARN_SHARE_MAX,
    LEARN_STREAK_WINDOWS,
    FingerprintModel,
    RoomDecider,
    Window,
    robust_mean,
)

SCANNERS = {"buero", "galerie", "klo", "dachboden", "schlaf"}

# Büro und Galerie: Haupt-Scanner (Rücken an Rücken) gleich stark, Unterschied nur
# bei den Scannern an der gegenüberliegenden Wand.
PROFILES = {
    "buero": {"buero": -62, "galerie": -62, "klo": -88, "dachboden": -86, "schlaf": -92},
    "galerie": {"buero": -62, "galerie": -62, "klo": -76, "dachboden": -74, "schlaf": -84},
    "schlafzimmer": {"buero": -95, "galerie": -88, "klo": -72, "dachboden": -70, "schlaf": -60},
}


def make_window(rng: random.Random, profile: dict[str, float], noise: float = 6.0) -> Window:
    rssi = {}
    for scanner, mean in profile.items():
        value = rng.gauss(mean, noise)
        if value > -97:  # schwache Signale gehen verloren
            rssi[scanner] = value
    return Window(rssi=rssi, active=set(SCANNERS))


def calibrated_model(rng: random.Random, windows: int = 30) -> FingerprintModel:
    model = FingerprintModel()
    for room, profile in PROFILES.items():
        comp = model.begin_calibration(room, replace=True)
        for _ in range(windows):
            comp.add_calibration(make_window(rng, profile))
    return model


def test_robust_mean_drops_outlier() -> None:
    assert abs(robust_mean([-60, -61, -62, -60, -61, -95]) - (-60.8)) < 0.01
    assert robust_mean([-70.0]) == -70.0


def test_rooms_with_equal_main_scanner_are_separated() -> None:
    rng = random.Random(1)
    model = calibrated_model(rng)
    for room in ("buero", "galerie"):
        hits = smooth_hits = 0
        decider = RoomDecider()
        for _ in range(200):
            lls = model.log_likelihoods(make_window(rng, PROFILES[room]))
            hits += max(lls, key=lls.__getitem__) == room
            smooth_hits += decider.step(lls).room == room
        # Einzelne Fenster sind verrauscht (6 dB), entscheidend ist die geglättete Entscheidung.
        assert hits / 200 > 0.7, room
        assert smooth_hits / 200 > 0.95, room


def test_decider_is_sticky_and_switches_after_confirmation() -> None:
    rng = random.Random(2)
    model = calibrated_model(rng)
    decider = RoomDecider()
    for _ in range(10):
        decider.step(model.log_likelihoods(make_window(rng, PROFILES["buero"])))
    assert decider.room == "buero"
    # Ein einzelner Ausreißer kippt nichts.
    decider.step(model.log_likelihoods(make_window(rng, PROFILES["schlafzimmer"], noise=0.1)))
    assert decider.room == "buero"
    # Dauerhafter Wechsel wird übernommen.
    for _ in range(8):
        decider.step(model.log_likelihoods(make_window(rng, PROFILES["schlafzimmer"])))
    assert decider.room == "schlafzimmer"


def test_learning_only_after_stable_streak() -> None:
    rng = random.Random(3)
    model = calibrated_model(rng)
    decider = RoomDecider()
    learned = [
        decider.step(model.log_likelihoods(make_window(rng, PROFILES["schlafzimmer"]))).learn_room
        for _ in range(LEARN_STREAK_WINDOWS + 5)
    ]
    assert learned[0] is None
    assert learned[-1] == "schlafzimmer"


def test_learned_share_is_capped() -> None:
    rng = random.Random(4)
    model = calibrated_model(rng, windows=20)
    comp = model.rooms["buero"][0]
    # Drift: Raum klingt plötzlich ganz anders – Gelerntes darf die Kalibrierung nicht überstimmen.
    drifted = {s: v - 20 for s, v in PROFILES["buero"].items()}
    for _ in range(1000):
        comp.add_learned(make_window(rng, drifted))
    eff = comp.effective("buero")
    assert eff is not None
    cal_mean = comp.cal["buero"].mean
    assert abs(eff.mean - cal_mean) <= LEARN_SHARE_MAX * 20 + 1.0


def test_new_scanner_counts_only_after_enough_learning() -> None:
    rng = random.Random(5)
    model = calibrated_model(rng)
    comp = model.rooms["galerie"][0]
    assert comp.effective("kinder") is None
    for _ in range(40):
        comp.add_learned(
            Window(rssi={"kinder": rng.gauss(-70, 3), "galerie": -62}, active=SCANNERS | {"kinder"})
        )
    eff = comp.effective("kinder")
    assert eff is not None and eff.has_gauss and abs(eff.mean + 70) < 3


def test_replace_affects_only_one_room_and_add_creates_component() -> None:
    rng = random.Random(6)
    model = calibrated_model(rng)
    galerie_before = model.rooms["galerie"][0].to_dict()
    model.begin_calibration("buero", replace=False)
    assert len(model.rooms["buero"]) == 2
    model.begin_calibration("buero", replace=True)
    assert len(model.rooms["buero"]) == 1
    assert model.rooms["buero"][0].cal_windows == 0
    assert model.rooms["galerie"][0].to_dict() == galerie_before
    # Raum ohne Kalibrierfenster nimmt nicht an der Erkennung teil.
    assert "buero" not in model.log_likelihoods(make_window(rng, PROFILES["buero"]))


def test_offline_scanner_is_ignored() -> None:
    rng = random.Random(7)
    model = calibrated_model(rng)
    window = make_window(rng, {k: v for k, v in PROFILES["schlafzimmer"].items() if k != "schlaf"})
    window.active.discard("schlaf")
    lls = model.log_likelihoods(window)
    assert max(lls, key=lls.__getitem__) == "schlafzimmer"


def test_roundtrip() -> None:
    rng = random.Random(8)
    model = calibrated_model(rng)
    model.rooms["buero"][0].add_learned(make_window(rng, PROFILES["buero"]))
    again = FingerprintModel.from_dict(model.to_dict())
    assert again.to_dict() == model.to_dict()


def test_stat_remove_is_inverse_of_add() -> None:
    from custom_components.manoumi.model import Stat

    stat = Stat()
    for v in (-60.0, -65.0, -70.0, None, -62.0):
        stat.add(v)
    before = stat.copy()
    stat.add(-80.0)
    stat.remove(-80.0)
    assert abs(stat.mean - before.mean) < 1e-9
    assert abs(stat.m2 - before.m2) < 1e-9
    assert stat.present == before.present and stat.seen == before.seen


def test_confusion_reports_separable_and_confusable_rooms() -> None:
    rng = random.Random(9)
    model = calibrated_model(rng)
    # Ein vierter Raum, der fast wie das Büro klingt.
    twin = model.begin_calibration("buero_zwilling", replace=True)
    for _ in range(30):
        twin.add_calibration(make_window(rng, {k: v + 1 for k, v in PROFILES["buero"].items()}))
    confusion = model.confusion()
    assert confusion["schlafzimmer"]["schlafzimmer"] > 0.9
    assert confusion["buero"].get("buero", 0.0) < 0.8
    assert "buero_zwilling" in confusion["buero"]


def test_explain_names_the_deciding_scanner() -> None:
    rng = random.Random(10)
    model = calibrated_model(rng)
    window = Window(rssi=dict(PROFILES["galerie"]), active=set(SCANNERS))
    top = model.explain(window, "galerie", "buero")[0][0]
    # Haupt-Scanner sind gleich – entscheiden müssen die der Gegenwand.
    assert top in {"klo", "dachboden", "schlaf"}


def test_drift_is_estimated_and_removed() -> None:
    from custom_components.manoumi.model import (
        BASELINE_WINDOWS,
        DRIFT_MIN_WINDOWS,
        DriftEstimator,
        apply_drift,
    )

    rng = random.Random(11)
    est = DriftEstimator()
    for _ in range(BASELINE_WINDOWS):
        est.add("meter_a", {"klo": rng.gauss(-70, 2), "schlaf": rng.gauss(-80, 2)})
        est.add("meter_b", {"klo": rng.gauss(-75, 2)})
    assert est.drift() == {}
    # Scanner „klo“ hört ab jetzt alles 6 dB schwächer.
    for _ in range(DRIFT_MIN_WINDOWS + 400):
        est.add("meter_a", {"klo": rng.gauss(-76, 2), "schlaf": rng.gauss(-80, 2)})
        est.add("meter_b", {"klo": rng.gauss(-81, 2)})
    drift = est.drift()
    assert abs(drift["klo"] + 6) < 1.5
    assert abs(drift["schlaf"]) < 1.5
    corrected = apply_drift(Window(rssi={"klo": -76.0}, active={"klo"}), drift)
    assert abs(corrected.rssi["klo"] + 70) < 1.5
    again = DriftEstimator.from_dict(est.to_dict())
    assert again.drift() == drift


def test_pocket_attenuation_is_compensated() -> None:
    """Alle Scanner 10 dB leiser (Hosentasche) – das Muster bleibt, der Raum auch."""
    rng = random.Random(12)
    model = calibrated_model(rng)
    decider = RoomDecider()
    pocket = {s: v - 10 for s, v in PROFILES["schlafzimmer"].items()}
    for _ in range(10):
        decider.step(model.log_likelihoods(make_window(rng, pocket)))
    assert decider.room == "schlafzimmer"


def test_visibility_follows_gain() -> None:
    """Leiseres Gerät: ein schwacher Scanner hört es seltener, ein starker kaum seltener."""
    from custom_components.manoumi.model import _shift_p_seen

    weak = _shift_p_seen(0.9, mean=-93.0, gain=-8.0, sd=4.0)
    strong = _shift_p_seen(0.9, mean=-70.0, gain=-8.0, sd=4.0)
    louder = _shift_p_seen(0.5, mean=-95.0, gain=+8.0, sd=4.0)
    assert weak < 0.3
    assert strong > 0.85
    assert louder > 0.6


def test_calibration_stops_early_only_when_separable() -> None:
    from custom_components.manoumi.model import CAL_MIN_WINDOWS

    rng = random.Random(14)
    model = calibrated_model(rng)
    # Deutlich anderer Raum: endet früh.
    other = {"buero": -95, "galerie": -95, "klo": -60, "dachboden": -90, "schlaf": -70}
    comp = model.begin_calibration("klo_raum", replace=True)
    stopped = None
    for n in range(1, 31):
        comp.add_calibration(make_window(rng, other, noise=3.0))
        if model.calibration_complete("klo_raum", comp):
            stopped = n
            break
    assert stopped is not None and CAL_MIN_WINDOWS <= stopped <= 15
    # Zwilling des Büros: nie sicher trennbar, läuft bis zum Ende.
    twin = model.begin_calibration("buero_zwilling", replace=True)
    for _ in range(30):
        twin.add_calibration(make_window(rng, PROFILES["buero"]))
        assert not model.calibration_complete("buero_zwilling", twin)
