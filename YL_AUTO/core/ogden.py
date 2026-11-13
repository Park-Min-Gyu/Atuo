# -*- coding: utf-8 -*-
"""
ogden.py : Abaqus Ogden N=3 물성
  - Abaqus 정의:  W = sum_i 2*mu_i/alpha_i^2 * (l1^a_i + l2^a_i + l3^a_i - 3) + sum_i (J-1)^(2i)/D_i
  - 초기 전단계수 mu0 = sum(mu_i), 초기 체적계수 K0 = 2/D1
  - 시험데이터(공칭변형률, 공칭응력)를 주면 Python 에서 N=3 피팅 (상대오차 최소화, 다중 시작점)
  - mu_i*alpha_i > 0 을 강제해 Drucker 안정성(충분조건)을 보장
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares


# ---- 비압축 균질 변형의 공칭응력 (Abaqus 정의 기준) -------------------------
def nominal_stress(mode: str, lam: np.ndarray, mu, alpha) -> np.ndarray:
    lam = np.asarray(lam, float)
    T = np.zeros_like(lam)
    for m_, a_ in zip(mu, alpha):
        k = 2.0 * m_ / a_
        if mode == "uniaxial":
            T += k * (lam ** (a_ - 1) - lam ** (-a_ / 2 - 1))
        elif mode == "biaxial":      # 등이축
            T += k * (lam ** (a_ - 1) - lam ** (-2 * a_ - 1))
        elif mode == "planar":       # 순수전단(평면인장)
            T += k * (lam ** (a_ - 1) - lam ** (-a_ - 1))
        else:
            raise ValueError(mode)
    return T


def read_test_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    """2열 CSV: nominal_strain, nominal_stress(MPa). 머리글 줄은 자동 건너뜀. strain 대신 stretch 열이면
    머리글에 'stretch' 또는 'lambda' 를 쓰면 된다."""
    strain, stress = [], []
    is_stretch = False
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            try:
                a, b = float(row[0]), float(row[1])
            except ValueError:
                h = row[0].lower()
                is_stretch = ("stretch" in h) or ("lambda" in h)
                continue
            strain.append(a - 1.0 if is_stretch else a)
            stress.append(b)
    e = np.array(strain)
    s = np.array(stress)
    keep = np.abs(e) > 1e-9
    return e[keep], s[keep]


def _unpack(p):
    a = p[0::2]
    mu = np.sign(a) * np.exp(p[1::2])      # mu_i * alpha_i > 0 강제
    return mu, a


def fit_ogden3(tests: dict[str, tuple[np.ndarray, np.ndarray]], n_starts: int = 300, seed: int = 0,
               alpha_bound: float = 12.0, stable_range=(0.2, 5.0), stab_weight: float = 50.0) -> dict:
    """tests = {'uniaxial': (strain, stress), 'biaxial': ..., 'planar': ...}
    stable_range: 이 신장비 구간에서 Drucker 안정(진응력 기울기>0)을 벌점으로 강제 (해석 수렴성 확보)"""
    rng = np.random.default_rng(seed)
    data = []
    for mode, (e, s) in tests.items():
        lam = 1.0 + np.asarray(e, float)
        s = np.asarray(s, float)
        scale = np.maximum(np.abs(s), 0.05 * np.abs(s).max())
        data.append((mode, lam, s, scale))

    smax = max(np.abs(d[2]).max() for d in data)
    lam_st = np.geomspace(stable_range[0], stable_range[1], 40) if stable_range else None

    def resid(p):
        mu, a = _unpack(p)
        out = []
        for mode, lam, s, scale in data:
            out.append((nominal_stress(mode, lam, mu, a) - s) / scale)
        if lam_st is not None:
            for mode in ("uniaxial", "biaxial", "planar"):
                g = true_stress_slope(mode, lam_st, mu, a)
                out.append(stab_weight * np.maximum(0.0, -g) / smax)
        return np.concatenate(out)

    # 초기 전단계수 추정 (소변형 기울기 ~ 3*mu0 for uniaxial)
    mode0, lam0, s0, _ = data[0]
    i0 = max(1, len(lam0) // 10)
    g_est = max(abs(s0[:i0] / (lam0[:i0] - 1 + 1e-12)).mean() / 3.0, 1e-3)

    lb = np.tile([-alpha_bound, math.log(1e-8)], 3)
    ub = np.tile([alpha_bound, math.log(1e3)], 3)
    # 1단계: 다중 시작점 짧은 탐색 -> 2단계: 상위 후보만 정밀 수렴
    cands = []
    for _ in range(n_starts):
        a = rng.uniform(-alpha_bound * 0.8, alpha_bound * 0.8, 3)
        a = np.where(np.abs(a) < 0.3, 0.3 * np.sign(a + 1e-12), a)
        mu_mag = np.abs(rng.normal(g_est / 3, g_est / 3, 3)) + 1e-6
        p0 = np.empty(6)
        p0[0::2] = a
        p0[1::2] = np.log(mu_mag)
        p0 = np.clip(p0, lb + 1e-9, ub - 1e-9)
        try:
            r = least_squares(resid, p0, bounds=(lb, ub), method="trf", max_nfev=120)
        except Exception:
            continue
        cands.append(r)
    cands.sort(key=lambda r: r.cost)
    best = None
    for c0 in cands[:8]:
        try:
            r = least_squares(resid, c0.x, bounds=(lb, ub), method="trf", max_nfev=4000, xtol=1e-12, ftol=1e-12)
        except Exception:
            continue
        if best is None or r.cost < best.cost:
            best = r
    mu, a = _unpack(best.x)
    order = np.argsort(-np.abs(mu))
    mu, a = mu[order], a[order]
    rep = {"mu": mu.tolist(), "alpha": a.tolist(), "mu0": float(mu.sum()), "per_mode": {}}
    for mode, lam, s, scale in data:
        pred = nominal_stress(mode, lam, mu, a)
        ss_res = float(((pred - s) ** 2).sum())
        ss_tot = float(((s - s.mean()) ** 2).sum())
        rel = np.abs(pred - s) / scale
        rep["per_mode"][mode] = {
            "R2": 1 - ss_res / ss_tot if ss_tot > 0 else float("nan"),
            "rel_err_rms_%": float(np.sqrt((rel ** 2).mean()) * 100),
            "rel_err_max_%": float(rel.max() * 100),
            "n_points": int(len(lam)),
        }
    rep["stability"] = check_stability(mu, a)
    rep["stability_enforced_range"] = list(stable_range) if stable_range else None
    return rep


def true_stress_slope(mode, lam, mu, alpha):
    """Drucker 안정 조건 (비압축): 진응력 sigma = lam*T 의 d(sigma)/d(lam) (> 0 이어야 안정)"""
    lam = np.asarray(lam, float)
    h = 1e-6 * lam
    sp = (lam + h) * nominal_stress(mode, lam + h, mu, alpha)
    sm = (lam - h) * nominal_stress(mode, lam - h, mu, alpha)
    return (sp - sm) / (2 * h)


def check_stability(mu, alpha, lam_min=0.1, lam_max=10.0) -> dict:
    """Abaqus 의 Drucker 안정성 점검과 같은 방식: 단축/등이축/평면 인장·압축에서 진응력 기울기 > 0 확인.
    lam_min~lam_max (공칭변형률 -0.9 ~ 9.0) 범위에서 불안정 구간을 보고한다."""
    lam = np.geomspace(lam_min, lam_max, 4000)
    res = {}
    for mode in ("uniaxial", "biaxial", "planar"):
        g = true_stress_slope(mode, lam, mu, alpha)
        bad = lam[g <= 0]
        if len(bad) == 0:
            res[mode] = f"stable {lam_min}~{lam_max}"
        else:
            res[mode] = f"unstable at lambda {bad.min():.3f}~{bad.max():.3f}"
    res["mu_alpha_positive"] = bool(np.all(np.asarray(mu) * np.asarray(alpha) > 0))
    return res


def d_from_bulk(mu0: float, bulk_modulus: float | None = None, poisson: float | None = None) -> float:
    """D1 = 2/K0. bulk_modulus(MPa) 또는 poisson 중 하나."""
    if bulk_modulus is None:
        if poisson is None:
            return 0.0
        bulk_modulus = 2 * mu0 * (1 + poisson) / (3 * (1 - 2 * poisson))
    return 2.0 / bulk_modulus


def resolve_material(name: str, spec: dict, base_dir: Path) -> dict:
    """config 의 materials[name] -> {'mu':[3], 'alpha':[3], 'D':[3], 'fit_report':...}"""
    out = {"name": name}
    if "ogden3" in spec:
        o = spec["ogden3"]
        mu, a = list(map(float, o["mu"])), list(map(float, o["alpha"]))
        if len(mu) != 3 or len(a) != 3:
            raise ValueError(f"material {name}: Ogden N=3 이므로 mu/alpha 가 3개씩 필요합니다.")
        out.update(mu=mu, alpha=a, fit_report={"source": "given coefficients", "stability": check_stability(mu, a)})
    elif "test_data" in spec:
        tests = {}
        for mode, f in spec["test_data"].items():
            p = Path(f)
            p = p if p.is_absolute() else base_dir / p
            tests[mode] = read_test_csv(p)
        sr = spec.get("stable_range", [0.2, 5.0])
        rep = fit_ogden3(tests, n_starts=int(spec.get("n_starts", 300)), stable_range=tuple(sr) if sr else None)
        out.update(mu=rep["mu"], alpha=rep["alpha"], fit_report=rep)
    else:
        raise ValueError(f"material {name}: 'ogden3' 계수 또는 'test_data' 가 필요합니다.")
    mu0 = sum(out["mu"])
    if "D" in spec:
        D = [float(v) for v in spec["D"]] + [0.0] * (3 - len(spec["D"]))
    else:
        D = [d_from_bulk(mu0, spec.get("bulk_modulus"), spec.get("poisson")), 0.0, 0.0]
    out["D"] = D[:3]
    out["mu0"] = mu0
    out["K0"] = (2.0 / D[0]) if D[0] > 0 else float("inf")
    return out
