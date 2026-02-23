#!/usr/bin/env python3
import numpy as np

# ==========================
# 여기에 9점 데이터 입력
# 각 줄: (X_raw, Y_raw, X_gt, Y_gt)
# ==========================
DATA = [
    # 예시 형식:
    # (Xraw, Yraw, Xgt, Ygt),
    # 아래 9줄을 당신 값으로 채우세요.
]

def fit_affine(data):
    # Xgt = a*x + b*y + c
    # Ygt = d*x + e*y + f
    A = []
    bx = []
    by = []
    for (x, y, Xgt, Ygt) in data:
        A.append([x, y, 1.0])
        bx.append(Xgt)
        by.append(Ygt)

    A = np.array(A, dtype=np.float64)
    bx = np.array(bx, dtype=np.float64)
    by = np.array(by, dtype=np.float64)

    # least squares
    px, *_ = np.linalg.lstsq(A, bx, rcond=None)  # [a,b,c]
    py, *_ = np.linalg.lstsq(A, by, rcond=None)  # [d,e,f]

    a, b, c = px.tolist()
    d, e, f = py.tolist()

    # 평가
    predX = A @ px
    predY = A @ py
    err = np.sqrt((predX - bx)**2 + (predY - by)**2)

    print("=== Affine coefficients ===")
    print(f"a={a:.10f}, b={b:.10f}, c={c:.10f}")
    print(f"d={d:.10f}, e={e:.10f}, f={f:.10f}")
    print()
    print("=== Error (m) ===")
    print(f"mean={err.mean():.4f}, max={err.max():.4f}")
    print("per-point:", np.round(err, 4).tolist())

    return (a,b,c,d,e,f)

if __name__ == "__main__":
    if len(DATA) < 6:
        raise SystemExit("DATA에 최소 6점 이상 입력하세요. (9점 권장)")
    fit_affine(DATA)

