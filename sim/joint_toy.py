#!/usr/bin/env python3
"""Joint controller x observer toy for Thesis #27.

The plant and the sparse Kenyon-cell-style controller are the objects
declared in Thesis #6. The dense clock and the sparse delayed scalar
(lag, floor, noise scale, censoring) are the maps declared in Thesis #20.
Neither deposit's results file is read. Occult mode switches are not
resimulated. Lumped-versus-sparse identification is not repeated.

The question is whether steering bounds that the same frozen controller
meets under full state still hold when the online map is the dense
schedule or the sparse delayed scalar.

Seed 20260921 builds the controller, matching Thesis #6. Observation
noise uses independent streams spawned from SeedSequence(20260921).
Research only. The input is not a dose.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from numpy.random import Generator, SeedSequence

ROOT = Path(__file__).resolve().parent
FIG = ROOT / "figures"
FIG.mkdir(parents=True, exist_ok=True)

SEED = 20260921
DT = 0.05
T_FINAL = 200.0
N_STEPS = int(round(T_FINAL / DT))
N_REP = 80

# Thesis #20 map constants, applied here to total burden of the two-clone plant.
DENSE_DT = 2.0
DENSE_SIGMA = np.array([0.02, 0.025])
SPARSE_DT = 20.0
SPARSE_SIGMA = 0.03
SPARSE_LAG = 14.0
SPARSE_FLOOR = 0.10
# Symmetry imputation. The training box is symmetric in the two clones,
# so the fraction a burden-only record does not identify is 1/2.
PHI0 = 0.5

# Steering bounds. Fixed as round cuts before any partial-observer path
# is interpreted. They are not fitted to a results file.
SEP_MIN = 0.25
TRACK_MAX = 0.15
DUTY_LO = 0.02
DUTY_HI = 0.30
TMEAN_LO = 0.50
TMEAN_HI = 0.97
TEND_MAX = 0.97
TMAX_MAX = 1.05
UDEV_MAX = 0.20
XDEV_MAX = 0.15

S_RICH = np.array([0.48, 0.12])
R_RICH = np.array([0.12, 0.48])
MID = np.array([0.30, 0.20])
ICS = {
    "sensitive_rich": S_RICH,
    "resistant_rich": R_RICH,
    "mid_mix": MID,
}


@dataclass(frozen=True)
class PlantParams:
    r_s: float = 0.28
    r_r: float = 0.16
    a_sr: float = 1.0
    a_rs: float = 1.6
    d_s: float = 0.55
    d_r: float = 0.06
    k: float = 1.0


@dataclass(frozen=True)
class KCParams:
    n_pn: int = 8
    n_kc: int = 96
    n_claw: int = 3
    top_k: int = 8
    ridge: float = 1e-2


def digest(obj) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def rhs(state: np.ndarray, u: float, p: PlantParams) -> np.ndarray:
    s = state[0] if state[0] > 0.0 else 0.0
    r = state[1] if state[1] > 0.0 else 0.0
    load = (s + p.a_sr * r) / p.k
    load_r = (r + p.a_rs * s) / p.k
    ds = p.r_s * s * (1.0 - load) - p.d_s * u * s
    dr = p.r_r * r * (1.0 - load_r) - p.d_r * u * r
    return np.array([ds, dr], dtype=float)


def rk4_step(state: np.ndarray, u: float, p: PlantParams, dt: float) -> np.ndarray:
    k1 = rhs(state, u, p)
    k2 = rhs(state + 0.5 * dt * k1, u, p)
    k3 = rhs(state + 0.5 * dt * k2, u, p)
    k4 = rhs(state + dt * k3, u, p)
    nxt = state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
    if nxt[0] < 0.0:
        nxt[0] = 0.0
    if nxt[1] < 0.0:
        nxt[1] = 0.0
    return nxt


def gatenby_like_target(state: np.ndarray) -> float:
    """In-silico training target of Thesis #6. Not a clinical rule."""
    s, r = float(state[0]), float(state[1])
    t = s + r
    frac_s = s / t if t > 1e-8 else 0.0
    if t >= 0.50 and frac_s >= 0.35:
        return 1.0
    if t <= 0.22:
        return 0.0
    if frac_s < 0.20:
        return 0.0
    return 0.45 if t > 0.35 else 0.0


class SparseKC:
    """Frozen expansion plus ridge readout. Architecture of Thesis #6."""

    def __init__(self, params: KCParams, rng: Generator):
        self.p = params
        self.u_prev = 0.0
        claws = np.array(
            [rng.choice(params.n_pn, size=params.n_claw, replace=False) for _ in range(params.n_kc)]
        )
        self.claws = claws
        self.weights = rng.normal(0.0, 1.0, size=(params.n_kc, params.n_claw))
        self.bias = rng.normal(0.0, 0.25, size=params.n_kc)
        self.readout = np.zeros(params.n_kc, dtype=float)
        self.readout_bias = 0.0

    def reset(self) -> None:
        self.u_prev = 0.0

    def code(self, state: np.ndarray) -> np.ndarray:
        s = float(state[0])
        r = float(state[1])
        t = s + r
        if t > 1e-8:
            frac_s = s / t
            frac_r = r / t
        else:
            frac_s = 0.0
            frac_r = 0.0
        slack = 1.0 - t if t < 1.0 else 0.0
        pn = np.array([s, r, t, slack, frac_s, frac_r, self.u_prev, 1.0], dtype=float)
        drive = np.einsum("ij,ij->i", self.weights, pn[self.claws]) + self.bias
        k = self.p.top_k
        idx = np.argpartition(drive, -k)[-k:]
        h = np.zeros_like(drive)
        h[idx] = np.maximum(drive[idx], 0.0)
        nrm = np.linalg.norm(h)
        if nrm > 1e-12:
            h = h / nrm
        return h

    def act(self, state: np.ndarray) -> float:
        h = self.code(state)
        u = float(h @ self.readout + self.readout_bias)
        if u < 0.0:
            u = 0.0
        elif u > 1.0:
            u = 1.0
        self.u_prev = u
        return u

    def fit_readout(self, states: np.ndarray, targets: np.ndarray) -> None:
        codes = np.stack([self.code(s) for s in states], axis=0)
        x = np.concatenate([codes, np.ones((len(states), 1))], axis=1)
        xtx = x.T @ x + self.p.ridge * np.eye(x.shape[1])
        w = np.linalg.solve(xtx, x.T @ targets)
        self.readout = w[:-1]
        self.readout_bias = float(w[-1])
        self.reset()

    def weight_record(self) -> dict:
        return {
            "claws": self.claws.tolist(),
            "weights": np.round(self.weights, 10).tolist(),
            "bias": np.round(self.bias, 10).tolist(),
            "readout": np.round(self.readout, 10).tolist(),
            "readout_bias": round(self.readout_bias, 10),
        }


def train_controller() -> SparseKC:
    rng = np.random.default_rng(SEED)
    kc = SparseKC(KCParams(), rng)
    ss = rng.uniform(0.02, 0.85, size=800)
    rr = rng.uniform(0.02, 0.85, size=800)
    states = np.stack([ss, rr], axis=1)
    targets = np.array([gatenby_like_target(st) for st in states])
    kc.fit_readout(states, targets)
    return kc


def burden(state: np.ndarray) -> float:
    return float(state[0] + state[1])


def impute(t_hat: float, phi: float) -> np.ndarray:
    if t_hat < 0.0:
        t_hat = 0.0
    return np.array([phi * t_hat, (1.0 - phi) * t_hat], dtype=float)


def clip_pair(y: np.ndarray) -> np.ndarray:
    out = y.copy()
    if out[0] < 0.0:
        out[0] = 0.0
    if out[1] < 0.0:
        out[1] = 0.0
    return out


def lag_update(f: float, b_new: float, lag: float, dt: float) -> float:
    decay = np.exp(-dt / lag)
    return float(b_new) + (f - float(b_new)) * decay


def censor(y: float, floor: float) -> tuple[float, bool]:
    if y < floor:
        return floor, True
    return y, False


def simulate(
    kc: SparseKC,
    state0: np.ndarray,
    plant: PlantParams,
    kind: str,
    noise: np.ndarray | None = None,
) -> dict:
    """Closed loop.

    kind:
      full: the controller sees the true state at every step.
      dense: zero-order hold of a noisy (S, R) sample every DENSE_DT.
      sparse: lagged burden, sparse clock, floor, fraction fixed at PHI0.
      restored: same scalar, but the fraction is the true fraction at the sample.
    noise is a flat Gaussian stream consumed in order, or None for the latent path.
    """
    kc.reset()
    s = np.array(state0, dtype=float)
    n = N_STEPS
    t_hist = np.empty(n + 1)
    s_hist = np.empty(n + 1)
    r_hist = np.empty(n + 1)
    u_hist = np.empty(n + 1)
    cursor = 0

    def take(k: int) -> np.ndarray:
        nonlocal cursor
        if noise is None:
            return np.zeros(k)
        sl = noise[cursor : cursor + k]
        cursor += k
        return sl

    f = burden(s)
    if kind == "full":
        held = s.copy()
    elif kind == "dense":
        z = take(2)
        held = clip_pair(s + DENSE_SIGMA * z)
    elif kind in ("sparse", "restored"):
        z = take(1)
        y, _ = censor(f + SPARSE_SIGMA * float(z[0]), SPARSE_FLOOR)
        phi = PHI0 if kind == "sparse" else (float(s[0]) / burden(s) if burden(s) > 1e-8 else PHI0)
        held = impute(y, phi)
    else:
        raise KeyError(kind)

    t_hist[0] = burden(s)
    s_hist[0] = s[0]
    r_hist[0] = s[1]
    u_hist[0] = kc.act(held)
    n_obs = 1
    n_cens = 0
    f_min = f

    for i in range(n):
        u = u_hist[i]
        s = rk4_step(s, u, plant, DT)
        if kind in ("sparse", "restored"):
            f = lag_update(f, burden(s), SPARSE_LAG, DT)
            if f < f_min:
                f_min = f
        t_now = (i + 1) * DT
        if kind == "full":
            held = s
        elif kind == "dense" and abs(t_now / DENSE_DT - round(t_now / DENSE_DT)) < 1e-8:
            z = take(2)
            held = clip_pair(s + DENSE_SIGMA * z)
            n_obs += 1
        elif kind in ("sparse", "restored") and abs(t_now / SPARSE_DT - round(t_now / SPARSE_DT)) < 1e-8:
            z = take(1)
            y_raw = f + SPARSE_SIGMA * float(z[0])
            y, cens = censor(y_raw, SPARSE_FLOOR)
            if cens:
                n_cens += 1
            if kind == "sparse":
                phi = PHI0
            else:
                b = burden(s)
                phi = float(s[0]) / b if b > 1e-8 else PHI0
            held = impute(y, phi)
            n_obs += 1
        t_hist[i + 1] = burden(s)
        s_hist[i + 1] = s[0]
        r_hist[i + 1] = s[1]
        u_hist[i + 1] = kc.act(held)

    on = u_hist > 0.5
    switches = int(np.sum(on[1:] != on[:-1]))
    return {
        "t": t_hist,
        "s": s_hist,
        "r": r_hist,
        "u": u_hist,
        "duty": float(np.mean(u_hist)),
        "t_mean": float(np.mean(t_hist)),
        "t_end": float(t_hist[-1]),
        "t_max": float(np.max(t_hist)),
        "switches": switches,
        "u0": float(u_hist[0]),
        "n_obs": (n + 1) if kind == "full" else n_obs,
        "n_cens": n_cens if kind in ("sparse", "restored") else 0,
        "f_min": f_min if kind in ("sparse", "restored") else None,
        "s_end": float(s[0]),
        "r_end": float(s[1]),
        "noise_used": cursor,
    }


def open_loop_u(kc: SparseKC, state: np.ndarray, kind: str, noise: np.ndarray | None = None) -> float:
    """One reset action. The lag is at equilibrium, so the scalar equals burden."""
    kc.reset()
    s = np.array(state, dtype=float)
    b = burden(s)
    if kind == "full":
        held = s
    elif kind == "dense":
        z = np.zeros(2) if noise is None else noise[:2]
        held = clip_pair(s + DENSE_SIGMA * z)
    elif kind == "sparse":
        z = 0.0 if noise is None else float(noise[0])
        y, _ = censor(b + SPARSE_SIGMA * z, SPARSE_FLOOR)
        held = impute(y, PHI0)
    elif kind == "restored":
        z = 0.0 if noise is None else float(noise[0])
        y, _ = censor(b + SPARSE_SIGMA * z, SPARSE_FLOOR)
        phi = float(s[0]) / b if b > 1e-8 else PHI0
        held = impute(y, phi)
    else:
        raise KeyError(kind)
    return kc.act(held)


def isoline_actions(kc: SparseKC, kind: str) -> dict:
    fracs = np.linspace(0.05, 0.95, 81)
    u_full = np.empty(81)
    u_map = np.empty(81)
    for i, f in enumerate(fracs):
        state = np.array([0.60 * f, 0.60 * (1.0 - f)])
        u_full[i] = open_loop_u(kc, state, "full")
        u_map[i] = open_loop_u(kc, state, kind)
    mad = float(np.mean(np.abs(u_map - u_full)))
    return {
        "fractions": fracs,
        "u_full": u_full,
        "u_map": u_map,
        "mad": mad,
        "u_map_min": float(np.min(u_map)),
        "u_map_max": float(np.max(u_map)),
        "track_pass": mad <= TRACK_MAX,
    }


def separation(kc: SparseKC, kind: str, noise_s: np.ndarray | None = None, noise_r: np.ndarray | None = None) -> dict:
    ua = open_loop_u(kc, S_RICH, kind, noise_s)
    ub = open_loop_u(kc, R_RICH, kind, noise_r)
    gap = abs(ua - ub)
    return {"u_sensitive": ua, "u_resistant": ub, "gap": gap, "sep_pass": gap >= SEP_MIN}


def closed_bounds(out: dict, design: dict) -> dict:
    duty_ok = DUTY_LO <= out["duty"] <= DUTY_HI
    tmean_ok = TMEAN_LO <= out["t_mean"] <= TMEAN_HI
    tend_ok = out["t_end"] <= TEND_MAX
    tmax_ok = out["t_max"] <= TMAX_MAX
    udev = float(np.mean(np.abs(out["u"] - design["u"])))
    xdev = float(np.sqrt(np.mean((out["s"] - design["s"]) ** 2 + (out["r"] - design["r"]) ** 2)))
    return {
        "duty": out["duty"],
        "duty_pass": duty_ok,
        "t_mean": out["t_mean"],
        "t_mean_pass": tmean_ok,
        "t_end": out["t_end"],
        "t_end_pass": tend_ok,
        "t_max": out["t_max"],
        "t_max_pass": tmax_ok,
        "switches": out["switches"],
        "u0": out["u0"],
        "u_dev": udev,
        "u_dev_pass": udev <= UDEV_MAX,
        "x_dev": xdev,
        "x_dev_pass": xdev <= XDEV_MAX,
        "n_obs": out["n_obs"],
        "n_cens": out["n_cens"],
        "f_min": out["f_min"],
        "s_end": out["s_end"],
        "r_end": out["r_end"],
        "ic_pass": bool(duty_ok and tmean_ok and tend_ok and tmax_ok and udev <= UDEV_MAX and xdev <= XDEV_MAX),
    }


def round_call(call: dict) -> dict:
    out = {}
    for k, v in call.items():
        if isinstance(v, float):
            out[k] = round(v, 6)
        elif isinstance(v, (bool, np.bool_)):
            out[k] = bool(v)
        else:
            out[k] = v
    return out


def summarise_map(kc: SparseKC, plant: PlantParams, kind: str, design: dict[str, dict]) -> tuple[dict, dict]:
    sep = separation(kc, kind)
    track = isoline_actions(kc, kind)
    paths = {}
    ics = {}
    for name, ic in ICS.items():
        out = simulate(kc, ic, plant, kind, noise=None)
        paths[name] = out
        ref = design[name] if kind != "full" else out
        ics[name] = closed_bounds(out, ref)
    ic_pass = all(ics[n]["ic_pass"] for n in ICS)
    certificate = bool(sep["sep_pass"] and track["track_pass"] and ic_pass)
    record = {
        "map": kind,
        "separation": {
            "u_sensitive": round(sep["u_sensitive"], 6),
            "u_resistant": round(sep["u_resistant"], 6),
            "gap": round(sep["gap"], 6),
            "pass": bool(sep["sep_pass"]),
        },
        "track": {
            "mad": round(track["mad"], 6),
            "u_min": round(track["u_map_min"], 6),
            "u_max": round(track["u_map_max"], 6),
            "pass": bool(track["track_pass"]),
        },
        "closed_loop": {name: round_call(ics[name]) for name in ICS},
        "certificate": certificate,
    }
    record["digest"] = digest(record)
    return record, paths


def noise_bank(rng: Generator, kind: str) -> np.ndarray:
    """Upper bound on Gaussian draws for one trajectory. Unused tail is ignored."""
    if kind == "dense":
        n_times = int(round(T_FINAL / DENSE_DT)) + 1
        return rng.normal(0.0, 1.0, size=n_times * 2 + 4)
    n_times = int(round(T_FINAL / SPARSE_DT)) + 1
    return rng.normal(0.0, 1.0, size=n_times + 4)


def monte_carlo(kc: SparseKC, plant: PlantParams, design_paths: dict[str, dict]) -> dict:
    seq = SeedSequence(SEED)
    children = seq.spawn(2)
    # child 0: open-loop separation noise. child 1: closed-loop noise.
    sep_rng = np.random.default_rng(children[0])
    loop_rng = np.random.default_rng(children[1])
    kinds = ("dense", "sparse", "restored")
    out: dict = {"n_rep": N_REP, "maps": {}}
    for kind in kinds:
        sep_pass = 0
        gaps = []
        # per-IC pass counts for each closed-loop bound, and joint
        keys = ("duty_pass", "t_mean_pass", "t_end_pass", "t_max_pass", "u_dev_pass", "x_dev_pass", "ic_pass")
        counts = {name: {k: 0 for k in keys} for name in ICS}
        joint = 0
        duty_sum = {name: 0.0 for name in ICS}
        udev_sum = {name: 0.0 for name in ICS}
        cens_sum = {name: 0 for name in ICS}
        for _ in range(N_REP):
            ns = sep_rng.normal(0.0, 1.0, size=2)
            nr = sep_rng.normal(0.0, 1.0, size=2 if kind == "dense" else 1)
            if kind != "dense":
                ns = ns[:1]
                nr = nr[:1]
            gap = separation(kc, kind, ns, nr)["gap"]
            gaps.append(gap)
            if gap >= SEP_MIN:
                sep_pass += 1
            ic_ok = True
            for name, ic in ICS.items():
                bank = noise_bank(loop_rng, kind)
                sim = simulate(kc, ic, plant, kind, noise=bank)
                call = closed_bounds(sim, design_paths[name])
                for k in keys:
                    if call[k]:
                        counts[name][k] += 1
                duty_sum[name] += sim["duty"]
                udev_sum[name] += call["u_dev"]
                cens_sum[name] += sim["n_cens"]
                ic_ok = ic_ok and call["ic_pass"]
            if ic_ok:
                joint += 1
        gaps_a = np.array(gaps)
        out["maps"][kind] = {
            "separation_pass_fraction": sep_pass / N_REP,
            "separation_gap_mean": float(gaps_a.mean()),
            "separation_gap_sd": float(gaps_a.std(ddof=1)),
            "separation_gap_min": float(gaps_a.min()),
            "separation_gap_max": float(gaps_a.max()),
            "closed_loop_all_ic_fraction": joint / N_REP,
            "by_ic": {
                name: {
                    **{k: counts[name][k] / N_REP for k in keys},
                    "duty_mean": duty_sum[name] / N_REP,
                    "u_dev_mean": udev_sum[name] / N_REP,
                    "censored_samples_mean": cens_sum[name] / N_REP,
                }
                for name in ICS
            },
        }
    return out


def downsample(a: np.ndarray, step: int = 4) -> list[float]:
    return [round(float(v), 5) for v in a[::step]]


def make_figures(paths: dict[str, dict[str, dict]], tracks: dict[str, dict], records: dict[str, dict], mc: dict) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Serif",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 140,
        }
    )
    colors = {"full": "#222222", "dense": "#1f4e79", "sparse": "#8c2f39", "restored": "#6b6b6b"}
    labels = {
        "full": "Full state",
        "dense": "Dense",
        "sparse": "Sparse delayed",
        "restored": "Scalar, fraction restored",
    }

    # Figure 4-1. Sensitive-rich closed loop.
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 5.6), sharex=True)
    time = np.linspace(0.0, T_FINAL, N_STEPS + 1)
    for kind in ("full", "dense", "sparse"):
        p = paths[kind]["sensitive_rich"]
        axes[0].plot(time, p["t"], color=colors[kind], lw=1.3, label=labels[kind])
        axes[1].plot(time, p["u"], color=colors[kind], lw=1.1, label=labels[kind])
    axes[0].set_ylabel("Total burden")
    axes[0].set_ylim(0.0, 1.15)
    axes[0].legend(frameon=False, loc="best")
    axes[1].set_ylabel("Input")
    axes[1].set_xlabel("Toy time")
    axes[1].set_ylim(-0.02, 1.05)
    fig.tight_layout()
    fig.savefig(FIG / "sensitive_rich_loop.png")
    plt.close(fig)

    # Figure 4-2. Isoline actions.
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    fr = tracks["full"]["fractions"]
    ax.plot(fr, tracks["full"]["u_full"], color=colors["full"], lw=1.4, label="Full state")
    ax.plot(fr, tracks["dense"]["u_map"], color=colors["dense"], lw=1.3, ls="--", label="Dense")
    ax.plot(fr, tracks["sparse"]["u_map"], color=colors["sparse"], lw=1.4, label="Sparse delayed")
    ax.plot(fr, tracks["restored"]["u_map"], color=colors["restored"], lw=1.1, ls=":", label="Fraction restored")
    ax.set_xlabel("Sensitive fraction at total burden 0.60")
    ax.set_ylabel("Input after reset")
    ax.set_ylim(-0.02, 1.05)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "isoline_actions.png")
    plt.close(fig)

    # Figure 4-3. Noise-free calls.
    bound_rows = [
        ("SEP", lambda rec, ic: rec["separation"]["pass"]),
        ("TRACK", lambda rec, ic: rec["track"]["pass"]),
        ("DUTY", lambda rec, ic: rec["closed_loop"][ic]["duty_pass"]),
        ("TMEAN", lambda rec, ic: rec["closed_loop"][ic]["t_mean_pass"]),
        ("TEND", lambda rec, ic: rec["closed_loop"][ic]["t_end_pass"]),
        ("TMAX", lambda rec, ic: rec["closed_loop"][ic]["t_max_pass"]),
        ("UDEV", lambda rec, ic: rec["closed_loop"][ic]["u_dev_pass"]),
        ("XDEV", lambda rec, ic: rec["closed_loop"][ic]["x_dev_pass"]),
    ]
    ics = list(ICS)
    col_labels = []
    grid = []
    for kind in ("full", "dense", "sparse", "restored"):
        for ic in ics:
            col_labels.append(f"{kind[:4]}\n{ic[:3]}")
    # rows x columns
    mat = np.zeros((len(bound_rows), 4 * 3))
    for j, kind in enumerate(("full", "dense", "sparse", "restored")):
        rec = records[kind]
        for i, (_, fn) in enumerate(bound_rows):
            for k, ic in enumerate(ics):
                mat[i, j * 3 + k] = 1.0 if fn(rec, ic) else 0.0
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.imshow(mat, cmap="gray_r", vmin=0, vmax=1, aspect="auto")
    ax.set_yticks(range(len(bound_rows)))
    ax.set_yticklabels([r[0] for r in bound_rows])
    ax.set_xticks(range(12))
    ax.set_xticklabels(
        ["S", "R", "M"] * 4,
        fontsize=8,
    )
    for j, name in enumerate(("full", "dense", "sparse", "restored")):
        ax.text(j * 3 + 1, -1.15, name, ha="center", va="bottom", fontsize=8, transform=ax.transData)
    ax.set_xlabel("Initial condition inside each map (S sensitive-rich, R resistant-rich, M mid)")
    fig.tight_layout()
    fig.savefig(FIG / "bound_calls.png")
    plt.close(fig)

    # Figure 4-4. Monte Carlo pass fractions. Closed-loop bars use the minimum
    # across the three initial conditions, which is the replicate-wise joint
    # only for the "all bounds" column. Per-bound bars are pass rates on the
    # sensitive-rich initial condition; the other two agree on the primary maps.
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    metric_labels = ["SEP", "DUTY", "UDEV", "TMEAN", "XDEV", "All closed"]
    x = np.arange(len(metric_labels))
    width = 0.25

    def metric_row(kind: str) -> list[float]:
        block = mc["maps"][kind]
        sr = block["by_ic"]["sensitive_rich"]
        return [
            block["separation_pass_fraction"],
            sr["duty_pass"],
            sr["u_dev_pass"],
            sr["t_mean_pass"],
            sr["x_dev_pass"],
            block["closed_loop_all_ic_fraction"],
        ]

    for i, kind in enumerate(("dense", "sparse", "restored")):
        vals = metric_row(kind)
        ax.bar(x + (i - 1) * width, vals, width, color=colors[kind], label=labels[kind])
    ax.set_xticks(x)
    ax.set_xticklabels(metric_labels)
    ax.set_ylim(0, 1.08)
    ax.set_ylabel(f"Fraction of {N_REP} replicates")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / "monte_carlo_fractions.png")
    plt.close(fig)


def main() -> None:
    plant = PlantParams()
    kc = train_controller()
    weight_dig = digest(kc.weight_record())

    # Design paths first. The partial maps are scored against these paths.
    design_paths = {}
    for name, ic in ICS.items():
        design_paths[name] = simulate(kc, ic, plant, "full", noise=None)

    records = {}
    paths = {}
    tracks = {}
    for kind in ("full", "dense", "sparse", "restored"):
        rec, pth = summarise_map(kc, plant, kind, design_paths)
        records[kind] = rec
        paths[kind] = pth
        tracks[kind] = isoline_actions(kc, kind)

    # Structural check: a burden-only imputation cannot separate the pair.
    sparse_gap = records["sparse"]["separation"]["gap"]
    if sparse_gap > 1e-9:
        raise RuntimeError(f"sparse separation gap {sparse_gap} should be 0 at equilibrium")

    mc = monte_carlo(kc, plant, design_paths)
    make_figures(paths, tracks, records, mc)

    bound_spec = {
        "SEP_MIN": SEP_MIN,
        "TRACK_MAX": TRACK_MAX,
        "DUTY": [DUTY_LO, DUTY_HI],
        "TMEAN": [TMEAN_LO, TMEAN_HI],
        "TEND_MAX": TEND_MAX,
        "TMAX_MAX": TMAX_MAX,
        "UDEV_MAX": UDEV_MAX,
        "XDEV_MAX": XDEV_MAX,
        "PHI0": PHI0,
        "dense": {"dt": DENSE_DT, "sigma": DENSE_SIGMA.tolist()},
        "sparse": {
            "dt": SPARSE_DT,
            "sigma": SPARSE_SIGMA,
            "lag": SPARSE_LAG,
            "floor": SPARSE_FLOOR,
        },
        "horizon": T_FINAL,
        "dt": DT,
        "n_rep": N_REP,
        "seed_controller": SEED,
    }

    # Round Monte Carlo for the manuscript.
    def round_mc(node):
        if isinstance(node, float):
            return round(node, 6)
        if isinstance(node, dict):
            return {k: round_mc(v) for k, v in node.items()}
        return node

    payload = {
        "seed_controller": SEED,
        "noise_seed_sequence": SEED,
        "disclaimer": (
            "In-silico steering certificate on one toy. Not a dose, not a device, "
            "not a clinical observer."
        ),
        "plant": asdict(plant),
        "controller": {
            "class": "sparse_kc",
            "n_kc": 96,
            "n_claw": 3,
            "top_k": 8,
            "ridge": 1e-2,
            "n_train": 800,
            "weight_digest_sha256": weight_dig,
        },
        "bound_spec": bound_spec,
        "bound_spec_digest_sha256": digest(bound_spec),
        "maps": {k: records[k] for k in ("full", "dense", "sparse", "restored")},
        "certificate": {k: records[k]["certificate"] for k in records},
        "monte_carlo": round_mc(mc),
    }
    # Digest of the scientific payload, excluding the digest field itself.
    payload["payload_digest_sha256"] = digest(
        {k: payload[k] for k in payload if k != "payload_digest_sha256"}
    )

    out = ROOT / "results.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print("weight", weight_dig)
    print("bounds", payload["bound_spec_digest_sha256"])
    print("payload", payload["payload_digest_sha256"])
    for kind in ("full", "dense", "sparse", "restored"):
        rec = records[kind]
        print(
            kind,
            "CERT",
            rec["certificate"],
            "SEP",
            rec["separation"],
            "TRACK",
            rec["track"],
            "digest",
            rec["digest"],
        )
        for name in ICS:
            c = rec["closed_loop"][name]
            print(
                " ",
                name,
                "duty",
                c["duty"],
                "tmean",
                c["t_mean"],
                "tend",
                c["t_end"],
                "tmax",
                c["t_max"],
                "udev",
                c["u_dev"],
                "xdev",
                c["x_dev"],
                "sw",
                c["switches"],
                "pass",
                c["ic_pass"],
                "cens",
                c["n_cens"],
                "fmin",
                c["f_min"],
                "send",
                c["s_end"],
                "rend",
                c["r_end"],
                "nobs",
                c["n_obs"],
            )
    print("MC")
    print(json.dumps(payload["monte_carlo"], indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
