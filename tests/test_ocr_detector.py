import pytest
from scripts.ocr_detector import detect_episode_boundaries

def test_detect_episode_boundaries(mocker, tmp_path):
    """测试 OCR 识别集数边界"""
    # Mock OpenCV VideoCapture
    mock_cap = mocker.MagicMock()
    mock_cap.get.side_effect = [100, 25]  # 100 帧, 25 fps = 4 秒
    mock_cap.read.return_value = (True, mocker.MagicMock())

    mocker.patch('cv2.VideoCapture', return_value=mock_cap)

    # Mock EasyOCR
    mock_reader = mocker.MagicMock()
    mock_reader.readtext.return_value = [
        (None, "第1集", 0.95),
        (None, "第2集", 0.92),
    ]
    mocker.patch('easyocr.Reader', return_value=mock_reader)

    boundaries = detect_episode_boundaries("test.mp4", sample_interval=1)

    assert len(boundaries) >= 2
    assert boundaries[0]['episode'] == 1
    assert boundaries[0]['confidence'] > 0.9
