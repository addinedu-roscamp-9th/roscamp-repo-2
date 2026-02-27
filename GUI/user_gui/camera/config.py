import os
import json
import numpy as np


class HomographyConfig:
    """
    프로젝트 루트의 homography.json을 절대경로로 읽어서
    - H (3x3)
    - roi (x,y,w,h)
    제공
    """

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.path = os.path.join(self.project_root, "homography.json")

        if not os.path.exists(self.path):
            raise FileNotFoundError(f"homography.json not found: {self.path}")

        with open(self.path, "r") as f:
            data = json.load(f)

        self.H = np.array(data["H"], dtype=np.float32)

        roi_json = data.get("roi", {})
        self.roi_x = int(roi_json.get("x", 0))
        self.roi_y = int(roi_json.get("y", 0))
        self.roi_w = int(roi_json.get("w", 0))
        self.roi_h = int(roi_json.get("h", 0))

    def roi_tuple(self) -> tuple[int, int, int, int]:
        return self.roi_x, self.roi_y, self.roi_w, self.roi_h

