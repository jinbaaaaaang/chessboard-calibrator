import cv2 as cv
import numpy as np
from PIL import ImageFont, ImageDraw, Image

# 설정
VIDEO_FILE    = 'video_calibration.mp4'
OUTPUT_FILE   = 'video_rectified.mp4'
BOARD_PATTERN = (7, 5)
BOARD_CELLSIZE = 0.025
BOARD_CRITERIA = (cv.TERM_CRITERIA_EPS + cv.TERM_CRITERIA_MAX_ITER, 30, 0.001)


def auto_select_img_from_video(video_file, board_pattern, max_images=20, min_diversity=30.0):
    """동영상에서 체스보드 프레임 자동 선택 (선명도 + 다양성 기준)"""
    video = cv.VideoCapture(video_file)
    assert video.isOpened(), f'동영상을 열 수 없습니다: {video_file}'
    total = int(video.get(cv.CAP_PROP_FRAME_COUNT))

    img_select = []
    selected_corners = []  # 다양성 비교용
    frame_idx = 0

    # 선명도 기준 자동 결정을 위해 첫 100프레임 샘플링
    sharpness_samples = []
    for _ in range(min(100, total)):
        valid, img = video.read()
        if not valid:
            break
        gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
        sharpness_samples.append(cv.Laplacian(gray, cv.CV_64F).var())
    sharp_thresh = np.percentile(sharpness_samples, 30)  # 하위 30% 제거
    print(f'선명도 기준: {sharp_thresh:.1f} (샘플 중앙값: {np.median(sharpness_samples):.1f})')
    video.set(cv.CAP_PROP_POS_FRAMES, 0)  # 처음으로 되감기

    print(f'총 {total}프레임 자동 탐색 중...')
    while True:
        valid, img = video.read()
        if not valid:
            break
        frame_idx += 1

        if frame_idx % 100 == 0:
            print(f'  {frame_idx}/{total} 탐색 중 (선택: {len(img_select)}장)')

        gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)

        sharpness = cv.Laplacian(gray, cv.CV_64F).var()
        if sharpness < sharp_thresh:
            continue

        complete, corners = cv.findChessboardCorners(gray, board_pattern, cv.CALIB_CB_FAST_CHECK)
        if not complete:
            continue

        corners = cv.cornerSubPix(gray, corners, (11, 11), (-1, -1), BOARD_CRITERIA)

        # 다양성 필터: 기존 선택 프레임과 코너 위치 차이가 충분해야 선택
        if selected_corners:
            diffs = [cv.norm(corners - prev, cv.NORM_L2) / len(corners)
                     for prev in selected_corners]
            if min(diffs) < min_diversity:
                continue

        img_select.append(img)
        selected_corners.append(corners)
        print(f'  프레임 {frame_idx} 선택 (선명도={sharpness:.0f}, 총 {len(img_select)}장)')

        if len(img_select) >= max_images:
            break

    video.release()
    return img_select


def calib_camera_from_chessboard(images, board_pattern, board_cellsize):
    """체스보드 이미지로 카메라 캘리브레이션"""
    img_points = []
    for img in images:
        gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
        complete, corners = cv.findChessboardCorners(gray, board_pattern)
        if complete:
            corners_refined = cv.cornerSubPix(gray, corners, (11, 11), (-1, -1), BOARD_CRITERIA)
            img_points.append(corners_refined)

    assert len(img_points) > 0, '체스보드가 검출된 이미지가 없습니다!'

    obj_pts = [[c, r, 0] for r in range(board_pattern[1]) for c in range(board_pattern[0])]
    obj_points = [np.array(obj_pts, dtype=np.float32) * board_cellsize] * len(img_points)

    img_size = images[0].shape[:2][::-1]
    return cv.calibrateCamera(obj_points, img_points, img_size, None, None)


def correct_distortion(video_file, output_file, K, dist_coeff):
    """영상 전체에 왜곡 보정 적용 후 저장 + 원본/보정본 나란히 미리보기"""
    video = cv.VideoCapture(video_file)
    assert video.isOpened(), f'동영상을 열 수 없습니다: {video_file}'

    w = int(video.get(cv.CAP_PROP_FRAME_WIDTH))
    h = int(video.get(cv.CAP_PROP_FRAME_HEIGHT))
    fps = video.get(cv.CAP_PROP_FPS)
    total = int(video.get(cv.CAP_PROP_FRAME_COUNT))

    fourcc = cv.VideoWriter_fourcc(*'mp4v')
    out = cv.VideoWriter(output_file, fourcc, fps, (w, h))

    map1, map2 = cv.initUndistortRectifyMap(K, dist_coeff, None, None, (w, h), cv.CV_32FC1)

    print(f'\n[2단계] 왜곡 보정 영상 저장 중: {output_file}')
    print('[ESC]: 중단\n')

    _pil_font = ImageFont.truetype('/System/Library/Fonts/AppleSDGothicNeo.ttc', size=22)

    def put_label(panel, text, color):
        pil_img = Image.fromarray(cv.cvtColor(panel, cv.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil_img)
        draw.text((16, 14), text, font=_pil_font, fill=(0, 0, 0))
        draw.text((14, 12), text, font=_pil_font, fill=tuple(reversed(color)))
        panel[:] = cv.cvtColor(np.array(pil_img), cv.COLOR_RGB2BGR)

    frame_count = 0
    while True:
        valid, img = video.read()
        if not valid:
            break

        rectified = cv.remap(img, map1, map2, interpolation=cv.INTER_LINEAR)
        out.write(rectified)
        frame_count += 1

        if frame_count % 30 == 0:
            print(f'  {frame_count}/{total} 프레임 완료')

        # 원본 + 보정본 나란히 미리보기
        preview_h = 360
        preview_w = int(w * preview_h / h)
        left  = cv.resize(img,       (preview_w, preview_h))
        right = cv.resize(rectified, (preview_w, preview_h))

        put_label(left,  'Original',  (255, 255, 255))
        put_label(right, 'Rectified', (255, 255, 255))

        preview = np.hstack([left, right])

        # 중앙 구분선
        cx = preview_w
        cv.line(preview, (cx, 0), (cx, preview_h), (200, 200, 200), 1)

        # 하단 진행률 바
        bar_h = 6
        bar = np.zeros((bar_h, preview_w * 2, 3), dtype=np.uint8)
        filled = int(preview_w * 2 * frame_count / max(total, 1))
        bar[:, :filled] = (80, 200, 120)
        bar[:, filled:] = (50, 50, 50)
        preview = np.vstack([preview, bar])

        cv.imshow('Original | Rectified', preview)
        cv.waitKey(1)


    video.release()
    out.release()
    cv.destroyAllWindows()
    print(f'\n완료! → {output_file} 저장됨 ({frame_count}프레임)')


if __name__ == '__main__':
    print('=== Camera Calibration & Distortion Correction ===\n')

    # 1) 캘리브레이션
    print('[1단계] 체스보드 프레임 자동 선택 중...')
    img_select = auto_select_img_from_video(VIDEO_FILE, BOARD_PATTERN)
    assert len(img_select) > 0, '선택된 프레임이 없습니다!'
    print(f'\n총 {len(img_select)}장 선택 → 캘리브레이션 수행 중...')

    rms, K, dist_coeff, rvecs, tvecs = calib_camera_from_chessboard(
        img_select, BOARD_PATTERN, BOARD_CELLSIZE
    )

    np.set_printoptions(precision=4, suppress=True)
    print('\n## Camera Calibration Results')
    print(f'* 사용된 이미지 수 = {len(img_select)}')
    print(f'* RMS error (rmse) = {rms:.6f}')
    print(f'* fx = {K[0,0]:.4f}')
    print(f'* fy = {K[1,1]:.4f}')
    print(f'* cx = {K[0,2]:.4f}')
    print(f'* cy = {K[1,2]:.4f}')
    print(f'* Distortion coefficients (k1, k2, p1, p2, k3) = {dist_coeff.flatten()}')

    img_w, img_h = img_select[0].shape[:2][::-1]
    fov_x = 2 * np.degrees(np.arctan(img_w / 2 / K[0, 0]))
    fov_y = 2 * np.degrees(np.arctan(img_h / 2 / K[1, 1]))
    print(f'* FOV: Horizontal={fov_x:.1f}°, Vertical={fov_y:.1f}°')

    # 2) 왜곡 보정 영상 저장
    correct_distortion(VIDEO_FILE, OUTPUT_FILE, K, dist_coeff)
