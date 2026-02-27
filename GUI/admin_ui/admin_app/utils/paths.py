# admin_app/utils/paths.py
from __future__ import annotations

from pathlib import Path

# admin_app/
BASE_DIR = Path(__file__).resolve().parents[1]

# admin_app/ui/admin/
UI_DIR = BASE_DIR / "ui" / "admin"

# admin_app/resources/
RES_DIR = BASE_DIR / "resources"
CALIB_DIR = RES_DIR / "calib"
MODELS_DIR = RES_DIR / "models"


def ui_path(filename: str) -> str:
    """
    .ui 파일 절대경로 반환
    """
    return str((UI_DIR / filename).resolve())


def resource_path(*parts: str) -> str:
    """
    resources 하위 파일 절대경로 반환
    예) resource_path("calib", "camera_calib.yml")
    """
    return str((RES_DIR.joinpath(*parts)).resolve())


def calib_path(filename: str = "camera_calib.yml") -> str:
    """
    calib 파일 절대경로
    """
    return str((CALIB_DIR / filename).resolve())


def model_path(kind: str = "pinky", filename: str = "best.pt") -> str:
    """
    모델 best.pt 절대경로
    kind:
      - "pinky"    -> resources/models/pinky/best.pt
      - "jetcobot" -> resources/models/jetcobot/best.pt
      - 그 외     -> resources/models/<kind>/best.pt 로 시도
    """
    kind = (kind or "pinky").strip().lower()

    # 호환 별칭들
    alias = {
        "arm": "jetcobot",
        "robotarm": "jetcobot",
        "charm": "jetcobot",
        "pinky1": "pinky",
        "pinky2": "pinky",
        "car1": "pinky",
        "car2": "pinky",
    }
    kind = alias.get(kind, kind)

    p = (MODELS_DIR / kind / filename).resolve()
    return str(p)


def model_path_pinky(filename: str = "best.pt") -> str:
    return model_path("pinky", filename)


def model_path_jetcobot(filename: str = "best.pt") -> str:
    return model_path("jetcobot", filename)
