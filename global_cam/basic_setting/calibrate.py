import cv2
import numpy as np
import glob
import os

# ==========================
# 1️⃣ 체커보드 설정
# ==========================
checkerboard_size = (6,7)  # 내부 코너 수
square_size = 2.5          # 체커보드 한 칸 길이 (cm)

# 3D 좌표 준비
objp = np.zeros((checkerboard_size[0]*checkerboard_size[1],3), np.float32)
objp[:,:2] = np.mgrid[0:checkerboard_size[0],0:checkerboard_size[1]].T.reshape(-1,2)
objp = objp * square_size

objpoints = []  # 실제 좌표
imgpoints = []  # 이미지 좌표

# ==========================
# 2️⃣ 이미지 불러오기 및 코너 검출
# ==========================
image_folder = "images"
undistorted_folder = "undistorted"
os.makedirs(undistorted_folder, exist_ok=True)

images = glob.glob(f"{image_folder}/*.png")
if len(images) == 0:
    print("❌ images 폴더에 이미지가 없습니다.")
    exit()

for fname in images:
    img = cv2.imread(fname)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    ret, corners = cv2.findChessboardCorners(gray, checkerboard_size, None)
    if ret:
        objpoints.append(objp)
        imgpoints.append(corners)
        cv2.drawChessboardCorners(img, checkerboard_size, corners, ret)
        cv2.imshow("Corners", img)
        cv2.waitKey(100)
    else:
        print(f"⚠️ 코너를 찾지 못함: {fname}")

cv2.destroyAllWindows()

# ==========================
# 3️⃣ 카메라 캘리브레이션
# ==========================
if len(objpoints) == 0:
    print("❌ 코너를 찾은 이미지가 없습니다. 캡처 이미지를 확인하세요.")
    exit()

# 첫 번째 코너 찾은 이미지 크기 기준
first_img = cv2.imread(images[0])
h, w = first_img.shape[:2]

ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
    objpoints, imgpoints, (w, h), None, None
)

print("Camera matrix:\n", camera_matrix)
print("Distortion coefficients:\n", dist_coeffs)

# ==========================
# 4️⃣ 왜곡 보정 후 저장
# ==========================
for fname in images:
    img = cv2.imread(fname)
    h, w = img.shape[:2]
    new_camera_matrix, roi = cv2.getOptimalNewCameraMatrix(camera_matrix, dist_coeffs, (w,h), 1, (w,h))
    undistorted = cv2.undistort(img, camera_matrix, dist_coeffs, None, new_camera_matrix)
    
    save_path = os.path.join(undistorted_folder, os.path.basename(fname))
    cv2.imwrite(save_path, undistorted)
    print(f"왜곡 보정 이미지 저장: {save_path}")

print("✅ 모든 이미지 왜곡 보정 완료")

