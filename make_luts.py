"""필름 시뮬레이션 근사 룩을 표준 .cube 3D LUT 파일로 베이크.

목적: LUT 파이프라인을 실제 .cube 데이터로 즉시 돌리기 위함.
주의: 여기서 만드는 값은 Fuji 정품 엔진이 아니라 대략적 근사다.
      나중에 luts/ 폴더의 .cube 파일을 진짜 Fuji LUT 로 교체하면
      코드 변경 없이 그대로 정확한 룩이 적용된다.
"""

from pathlib import Path

import numpy as np

LUTS_DIR = Path(__file__).resolve().parent / "luts"
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
SIZE = 33  # LUT 해상도 (33^3)


def _sat(c, s):
    l = c @ LUMA
    return l[..., None] + (c - l[..., None]) * s


def _contrast(c, k):
    return (c - 0.5) * k + 0.5


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


# 각 함수는 shaders/adjust.frag 의 옛 film_sim() 분기와 동일한 수식.
def identity(c):
    return c


def provia(c):
    c = _sat(c, 1.10)
    return _contrast(c, 1.05)


def velvia(c):
    c = _sat(c, 1.45)
    c = _contrast(c, 1.18)
    c = c.copy()
    c[..., 0] = np.power(np.clip(c[..., 0], 0.0, None), 0.95)
    return c


def astia(c):
    c = _sat(c, 1.15)
    c = _contrast(c, 0.95)
    c = c.copy()
    c[..., 0] *= 1.02
    c[..., 2] *= 0.99
    return c


def classic_chrome(c):
    c = _sat(c, 0.82)
    c = _contrast(c, 1.08)
    c = c.copy()
    sh = 1.0 - _smoothstep(0.0, 0.5, c @ LUMA)
    c[..., 2] += sh * 0.03
    c[..., 0] -= sh * 0.01
    return c


def classic_neg(c):
    c = _sat(c, 0.95)
    c = _contrast(c, 1.14)
    c = c.copy()
    l = c @ LUMA
    sh = 1.0 - _smoothstep(0.0, 0.45, l)
    hi = _smoothstep(0.55, 1.0, l)
    c[..., 2] += sh * 0.04
    c[..., 1] += sh * 0.015
    c[..., 0] += hi * 0.03
    c[..., 1] += hi * 0.01
    return c


def pro_neg_std(c):
    c = _sat(c, 0.92)
    return _contrast(c, 0.90)


def eterna(c):
    c = _sat(c, 0.75)
    c = _contrast(c, 0.82)
    c = c * 0.94 + 0.03
    c = c.copy()
    c[..., 0] += 0.005
    return c


SIMS = {
    "identity": identity,
    "provia": provia,
    "velvia": velvia,
    "astia": astia,
    "classic_chrome": classic_chrome,
    "classic_neg": classic_neg,
    "pro_neg_std": pro_neg_std,
    "eterna": eterna,
}


def _grid(n):
    """(n^3, 3) 입력 색 그리드 — red 가 가장 빠르게 변하는 순서."""
    idx = np.arange(n ** 3)
    r = (idx % n) / (n - 1)
    g = ((idx // n) % n) / (n - 1)
    b = (idx // (n * n)) / (n - 1)
    return np.stack([r, g, b], axis=1).astype(np.float32)


def write_cube(name: str, fn, n: int = SIZE):
    grid = _grid(n)
    out = np.clip(fn(grid), 0.0, 1.0)
    path = LUTS_DIR / f"{name}.cube"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'TITLE "{name} (approx)"\n')
        f.write(f"LUT_3D_SIZE {n}\n")
        f.write("DOMAIN_MIN 0.0 0.0 0.0\n")
        f.write("DOMAIN_MAX 1.0 1.0 1.0\n")
        for r, g, b in out:
            f.write(f"{r:.6f} {g:.6f} {b:.6f}\n")
    return path


def generate_all() -> None:
    LUTS_DIR.mkdir(exist_ok=True)
    for name, fn in SIMS.items():
        p = write_cube(name, fn)
        print(f"[lut] {p.name}")


if __name__ == "__main__":
    generate_all()
