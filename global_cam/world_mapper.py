import numpy as np


class WorldMapper:
    """
    pixel(undistorted + cropped ROI) -> world (as defined by homography.json)

    ✅ 여기서는 Homography(H)만 적용합니다.
    ✅ affine / origin shift / scale / clamp 등 모든 보정은 하지 않습니다.
    """

    def __init__(self, H: np.ndarray):
        self.H = H.astype(np.float32)

    def pixel_to_world(self, u: float, v: float) -> tuple[float, float]:
        pt = np.array([u, v, 1.0], dtype=np.float32)
        pw = self.H @ pt

        z = float(pw[2])
        if abs(z) < 1e-8:
            raise ValueError("Invalid homography projection (z≈0)")

        x = float(pw[0] / z)
        y = float(pw[1] / z)
        return x, y
