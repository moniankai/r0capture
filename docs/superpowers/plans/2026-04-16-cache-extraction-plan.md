# 红果短剧缓存提取与切分实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从红果 App 缓存中提取《西游，错把玉帝当亲爹》60 集视频，生成全集合并版本和独立集数文件

**Architecture:** 先合并后切分策略 - 拉取缓存 → 按时间排序合并全集 → OCR 识别集数边界 → 切分独立集数

**Tech Stack:** Python 3.8+, OpenCV, EasyOCR, ffmpeg, ADB, tqdm, loguru

---

## 文件结构

### 新建文件

- `scripts/extract_drama_from_cache.py` - 主入口脚本，编排整个流程
- `scripts/cache_puller.py` - 缓存拉取和排序模块
- `scripts/video_merger.py` - 视频合并模块
- `scripts/ocr_detector.py` - OCR 集数边界识别模块
- `scripts/split_planner.py` - 切分计划生成模块
- `scripts/video_splitter.py` - 视频切分执行模块
- `scripts/output_validator.py` - 输出验证模块
- `tests/test_cache_extraction.py` - 集成测试

### 修改文件

- `scripts/pull_cache.py` - 复用现有的 ADB 工具函数
- `requirements.txt` - 添加新依赖

---

## Task 1: 添加依赖项

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: 添加 OCR 和视频处理依赖**

在 `requirements.txt` 末尾添加：

```txt
opencv-python>=4.8.0
easyocr>=1.7.0
torch>=2.0.0
torchvision>=0.15.0
```

- [ ] **Step 2: 安装依赖**

Run: `pip install -r requirements.txt`
Expected: 所有包成功安装

- [ ] **Step 3: 验证 EasyOCR 可用**

Run: `python -c "import easyocr; print('EasyOCR OK')"`
Expected: 输出 "EasyOCR OK"

- [ ] **Step 4: 提交**

```bash
git add requirements.txt
git commit -m "deps: 添加 OCR 和视频处理依赖

- opencv-python 用于视频帧提取
- easyocr 用于集数识别
- torch/torchvision 作为 easyocr 依赖

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 缓存拉取模块

**Files:**
- Create: `scripts/cache_puller.py`
- Test: `tests/test_cache_puller.py`

- [ ] **Step 1: 编写测试 - 列出远程文件**

Create `tests/test_cache_puller.py`:

```python
import pytest
from scripts.cache_puller import list_remote_mdl_with_time

def test_list_remote_mdl_with_time(mocker):
    """测试列出远程 .mdl 文件及修改时间"""
    mock_run = mocker.patch('scripts.cache_puller.run_adb')
    mock_run.return_value.stdout = """
-rw-rw---- 1 u0_a163 sdcard_rw 1048576 2026-04-16 10:25 /sdcard/Android/data/com.phoenix.read/cache/short/file1.mdl
-rw-rw---- 1 u0_a163 sdcard_rw 2097152 2026-04-16 14:07 /sdcard/Android/data/com.phoenix.read/cache/short/file2.mdl
"""
    mock_run.return_value.returncode = 0
    
    files = list_remote_mdl_with_time()
    
    assert len(files) == 2
    assert files[0]['name'] == 'file1.mdl'
    assert files[0]['size'] == '1048576'
    assert files[0]['date'] == '2026-04-16 10:25'
    assert files[1]['name'] == 'file2.mdl'
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_cache_puller.py::test_list_remote_mdl_with_time -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.cache_puller'"

- [ ] **Step 3: 实现 list_remote_mdl_with_time**

Create `scripts/cache_puller.py`:

```python
"""缓存拉取和排序模块"""
import os
import re
from pathlib import Path
from typing import List, Dict
from loguru import logger

CACHE_PATH = "/sdcard/Android/data/com.phoenix.read/cache/short"


def run_adb(args: List[str], check: bool = True):
    """执行 ADB 命令"""
    import subprocess
    cmd = ["adb"] + args
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=check, env=env)


def list_remote_mdl_with_time() -> List[Dict[str, str]]:
    """列出远程 .mdl 文件及其修改时间"""
    result = run_adb(["shell", f"ls -l {CACHE_PATH}/*.mdl"], check=False)
    if result.returncode != 0:
        logger.error("未找到 .mdl 文件")
        return []
    
    files = []
    for line in result.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[-1].endswith(".mdl"):
            files.append({
                "name": Path(parts[-1]).name,
                "size": parts[4],
                "date": f"{parts[5]} {parts[6]}",
                "path": parts[-1],
            })
    return files
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_cache_puller.py::test_list_remote_mdl_with_time -v`
Expected: PASS

- [ ] **Step 5: 编写测试 - 拉取并排序缓存**

Add to `tests/test_cache_puller.py`:

```python
def test_pull_and_sort_cache(mocker, tmp_path):
    """测试拉取并排序缓存文件"""
    mock_list = mocker.patch('scripts.cache_puller.list_remote_mdl_with_time')
    mock_list.return_value = [
        {"name": "file2.mdl", "date": "2026-04-16 14:07", "path": "/path/file2.mdl"},
        {"name": "file1.mdl", "date": "2026-04-16 10:25", "path": "/path/file1.mdl"},
    ]
    
    mock_run = mocker.patch('scripts.cache_puller.run_adb')
    
    from scripts.cache_puller import pull_and_sort_cache
    output_dir = str(tmp_path / "cache")
    
    result = pull_and_sort_cache(output_dir)
    
    assert result == 2
    assert (tmp_path / "cache" / "concat_list.txt").exists()
    assert mock_run.call_count == 2  # 拉取两个文件
```

- [ ] **Step 6: 运行测试验证失败**

Run: `pytest tests/test_cache_puller.py::test_pull_and_sort_cache -v`
Expected: FAIL with "ImportError: cannot import name 'pull_and_sort_cache'"

- [ ] **Step 7: 实现 pull_and_sort_cache**

Add to `scripts/cache_puller.py`:

```python
def pull_and_sort_cache(output_dir: str) -> int:
    """拉取并排序缓存文件，生成 concat 列表"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    remote_files = list_remote_mdl_with_time()
    if not remote_files:
        logger.warning("未找到缓存文件")
        return 0
    
    # 按修改时间排序
    sorted_files = sorted(remote_files, key=lambda x: x['date'])
    logger.info(f"找到 {len(sorted_files)} 个缓存文件，按时间排序")
    
    # 拉取文件
    for i, f in enumerate(sorted_files):
        local_name = f"{i+1:03d}_{f['name']}"
        local_path = output_path / local_name
        logger.info(f"拉取 [{i+1}/{len(sorted_files)}]: {f['name']}")
        run_adb(["pull", f["path"], str(local_path)])
    
    # 生成 concat 列表
    concat_file = output_path / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for i in range(len(sorted_files)):
            f.write(f"file '{i+1:03d}_*.mdl'\n")
    
    logger.info(f"生成 concat 列表: {concat_file}")
    return len(sorted_files)
```

- [ ] **Step 8: 运行测试验证通过**

Run: `pytest tests/test_cache_puller.py::test_pull_and_sort_cache -v`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add scripts/cache_puller.py tests/test_cache_puller.py
git commit -m "feat: 实现缓存拉取和排序模块

- list_remote_mdl_with_time: 列出远程 .mdl 文件
- pull_and_sort_cache: 按时间排序拉取并生成 concat 列表

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 视频合并模块

**Files:**
- Create: `scripts/video_merger.py`
- Test: `tests/test_video_merger.py`

- [ ] **Step 1: 编写测试 - 合并视频**

Create `tests/test_video_merger.py`:

```python
import pytest
from pathlib import Path
from scripts.video_merger import merge_videos

def test_merge_videos(mocker, tmp_path):
    """测试合并视频功能"""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    
    concat_list = cache_dir / "concat_list.txt"
    concat_list.write_text("file '001_test.mdl'\nfile '002_test.mdl'\n")
    
    output_dir = tmp_path / "全集"
    drama_name = "测试短剧"
    
    mock_run = mocker.patch('subprocess.run')
    mock_run.return_value.returncode = 0
    
    result = merge_videos(str(cache_dir), str(output_dir), drama_name)
    
    assert result == str(output_dir / f"{drama_name}_全集.mp4")
    assert mock_run.called
    args = mock_run.call_args[0][0]
    assert "ffmpeg" in args
    assert "-f" in args and "concat" in args
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_video_merger.py::test_merge_videos -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.video_merger'"

- [ ] **Step 3: 实现 merge_videos**

Create `scripts/video_merger.py`:

```python
"""视频合并模块"""
import subprocess
from pathlib import Path
from loguru import logger


def merge_videos(cache_dir: str, output_dir: str, drama_name: str) -> str:
    """使用 ffmpeg 合并视频文件"""
    cache_path = Path(cache_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    concat_list = cache_path / "concat_list.txt"
    if not concat_list.exists():
        raise FileNotFoundError(f"concat 列表不存在: {concat_list}")
    
    output_file = output_path / f"{drama_name}_全集.mp4"
    
    logger.info(f"开始合并视频: {output_file}")
    
    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-y",
        str(output_file)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error(f"ffmpeg 合并失败: {result.stderr}")
        raise RuntimeError(f"视频合并失败: {result.stderr}")
    
    logger.info(f"合并完成: {output_file}")
    return str(output_file)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_video_merger.py::test_merge_videos -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/video_merger.py tests/test_video_merger.py
git commit -m "feat: 实现视频合并模块

使用 ffmpeg concat 协议合并多个 .mdl 文件为单个 MP4

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: OCR 集数识别模块

**Files:**
- Create: `scripts/ocr_detector.py`
- Test: `tests/test_ocr_detector.py`

- [ ] **Step 1: 编写测试 - OCR 识别集数**

Create `tests/test_ocr_detector.py`:

```python
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
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_ocr_detector.py::test_detect_episode_boundaries -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.ocr_detector'"

- [ ] **Step 3: 实现 detect_episode_boundaries**

Create `scripts/ocr_detector.py`:

```python
"""OCR 集数边界识别模块"""
import re
from typing import List, Dict
import cv2
import easyocr
from loguru import logger


def detect_episode_boundaries(video_path: str, sample_interval: int = 30) -> List[Dict]:
    """
    使用 OCR 识别视频中的集数边界
    
    Args:
        video_path: 视频文件路径
        sample_interval: 采样间隔（秒）
    
    Returns:
        边界列表 [{"episode": 1, "start_time": 0, "confidence": 0.95}, ...]
    """
    logger.info(f"开始 OCR 识别: {video_path}")
    logger.info("加载 EasyOCR 模型...")
    
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=True)
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")
    
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    duration = frame_count / fps
    
    logger.info(f"视频时长: {duration:.1f} 秒, 采样间隔: {sample_interval} 秒")
    
    boundaries = []
    current_episode = 0
    
    for timestamp in range(0, int(duration), sample_interval):
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ret, frame = cap.read()
        
        if not ret:
            continue
        
        # OCR 识别
        results = reader.readtext(frame)
        
        for (bbox, text, confidence) in results:
            # 匹配集数模式
            match = re.search(r'第\s*(\d+)\s*集|EP\s*(\d+)|(\d+)\s*/\s*\d+', text)
            
            if match and confidence > 0.7:
                episode_num = int(match.group(1) or match.group(2) or match.group(3))
                
                # 检测到新集数
                if episode_num > current_episode:
                    boundaries.append({
                        'episode': episode_num,
                        'start_time': timestamp,
                        'confidence': confidence,
                        'text': text
                    })
                    current_episode = episode_num
                    logger.info(f"检测到第 {episode_num} 集 @ {timestamp}s (置信度: {confidence:.2f})")
    
    cap.release()
    logger.info(f"OCR 完成，检测到 {len(boundaries)} 个集数边界")
    
    return boundaries
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_ocr_detector.py::test_detect_episode_boundaries -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/ocr_detector.py tests/test_ocr_detector.py
git commit -m "feat: 实现 OCR 集数边界识别模块

使用 EasyOCR 识别视频中的集数标题

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 切分计划生成模块

**Files:**
- Create: `scripts/split_planner.py`
- Test: `tests/test_split_planner.py`

- [ ] **Step 1: 编写测试 - 生成切分计划**

Create `tests/test_split_planner.py`:

```python
import pytest
from scripts.split_planner import generate_split_plan

def test_generate_split_plan():
    """测试生成切分计划"""
    boundaries = [
        {"episode": 1, "start_time": 0},
        {"episode": 2, "start_time": 65},
        {"episode": 4, "start_time": 195},  # 缺失第 3 集
    ]
    total_duration = 300.0
    expected_episodes = 4
    
    plan = generate_split_plan(boundaries, total_duration, expected_episodes)
    
    assert len(plan) == 4
    assert plan[0]['episode'] == 1
    assert plan[0]['start'] == 0
    assert plan[0]['end'] == 65
    assert plan[0]['confidence'] == 'detected'
    
    assert plan[2]['episode'] == 3
    assert plan[2]['confidence'] == 'estimated'  # 插值估算
    
    assert plan[3]['end'] == 300.0  # 最后一集到结尾
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_split_planner.py::test_generate_split_plan -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.split_planner'"

- [ ] **Step 3: 实现 generate_split_plan**

Create `scripts/split_planner.py`:

```python
"""切分计划生成模块"""
from typing import List, Dict
from loguru import logger


def generate_split_plan(boundaries: List[Dict], total_duration: float, expected_episodes: int = 60) -> List[Dict]:
    """
    根据 OCR 边界生成完整的切分计划
    
    Args:
        boundaries: OCR 检测的边界列表
        total_duration: 视频总时长（秒）
        expected_episodes: 预期集数
    
    Returns:
        切分计划 [{"episode": 1, "start": 0, "end": 65, "confidence": "detected"}, ...]
    """
    split_plan = []
    
    for i in range(expected_episodes):
        episode_num = i + 1
        
        # 查找该集的起始时间
        start_time = next((b['start_time'] for b in boundaries if b['episode'] == episode_num), None)
        
        # 查找下一集的起始时间（作为结束时间）
        if i < expected_episodes - 1:
            end_time = next((b['start_time'] for b in boundaries if b['episode'] == episode_num + 1), None)
        else:
            end_time = total_duration
        
        # 处理缺失边界
        if start_time is None:
            # 使用插值估算
            prev_boundary = next((b for b in boundaries if b['episode'] < episode_num), None)
            next_boundary = next((b for b in boundaries if b['episode'] > episode_num), None)
            
            if prev_boundary and next_boundary:
                start_time = (prev_boundary['start_time'] + next_boundary['start_time']) / 2
            elif prev_boundary:
                start_time = prev_boundary['start_time'] + 60  # 假设每集 60 秒
            else:
                start_time = 0
            
            confidence = 'estimated'
            logger.warning(f"第 {episode_num} 集边界缺失，使用插值估算: {start_time:.1f}s")
        else:
            confidence = 'detected'
        
        split_plan.append({
            'episode': episode_num,
            'start': start_time,
            'end': end_time if end_time else total_duration,
            'duration': (end_time - start_time) if end_time else None,
            'confidence': confidence
        })
    
    return split_plan
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_split_planner.py::test_generate_split_plan -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/split_planner.py tests/test_split_planner.py
git commit -m "feat: 实现切分计划生成模块

根据 OCR 边界生成完整切分计划，支持插值估算缺失边界

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 视频切分执行模块

**Files:**
- Create: `scripts/video_splitter.py`
- Test: `tests/test_video_splitter.py`

- [ ] **Step 1: 编写测试 - 切分视频**

Create `tests/test_video_splitter.py`:

```python
import pytest
from pathlib import Path
from scripts.video_splitter import split_episodes

def test_split_episodes(mocker, tmp_path):
    """测试切分视频功能"""
    split_plan = [
        {"episode": 1, "start": 0, "end": 65, "confidence": "detected"},
        {"episode": 2, "start": 65, "end": 130, "confidence": "detected"},
    ]
    
    full_video = "test_full.mp4"
    output_dir = str(tmp_path / "独立集数")
    
    mock_run = mocker.patch('subprocess.run')
    mock_run.return_value.returncode = 0
    
    result = split_episodes(full_video, split_plan, output_dir)
    
    assert result == 2
    assert mock_run.call_count == 2
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_video_splitter.py::test_split_episodes -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.video_splitter'"

- [ ] **Step 3: 实现 split_episodes**

Create `scripts/video_splitter.py`:

```python
"""视频切分执行模块"""
import subprocess
from pathlib import Path
from typing import List, Dict
from tqdm import tqdm
from loguru import logger


def split_episodes(full_video: str, split_plan: List[Dict], output_dir: str) -> int:
    """
    根据切分计划切分视频
    
    Args:
        full_video: 全集视频路径
        split_plan: 切分计划
        output_dir: 输出目录
    
    Returns:
        成功切分的集数
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"开始切分 {len(split_plan)} 集视频")
    
    success_count = 0
    
    for item in tqdm(split_plan, desc="切分集数"):
        episode_num = item['episode']
        start = item['start']
        end = item['end']
        
        output_file = output_path / f"episode_{episode_num:03d}.mp4"
        
        cmd = [
            'ffmpeg',
            '-i', full_video,
            '-ss', str(start),
            '-to', str(end),
            '-c', 'copy',
            '-avoid_negative_ts', '1',
            '-y',
            str(output_file)
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"切分第 {episode_num} 集失败: {result.stderr}")
        else:
            success_count += 1
    
    logger.info(f"切分完成: {success_count}/{len(split_plan)} 集")
    return success_count
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_video_splitter.py::test_split_episodes -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/video_splitter.py tests/test_video_splitter.py
git commit -m "feat: 实现视频切分执行模块

使用 ffmpeg 根据切分计划切分独立集数

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 输出验证模块

**Files:**
- Create: `scripts/output_validator.py`
- Test: `tests/test_output_validator.py`

- [ ] **Step 1: 编写测试 - 验证输出**

Create `tests/test_output_validator.py`:

```python
import pytest
from pathlib import Path
from scripts.output_validator import validate_output

def test_validate_output(tmp_path, mocker):
    """测试输出验证功能"""
    output_dir = tmp_path / "独立集数"
    output_dir.mkdir()
    
    # 创建测试文件
    (output_dir / "episode_001.mp4").write_bytes(b"x" * 200000)  # 正常
    (output_dir / "episode_002.mp4").write_bytes(b"x" * 50000)   # 过小
    # episode_003.mp4 缺失
    
    mock_duration = mocker.patch('scripts.output_validator.get_mp4_duration')
    mock_duration.side_effect = [65.0, 5.0]  # 第2集时长过短
    
    issues = validate_output(str(output_dir), expected_episodes=3)
    
    assert len(issues) >= 2
    assert any("缺失" in issue and "3" in issue for issue in issues)
    assert any("过小" in issue and "2" in issue for issue in issues)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_output_validator.py::test_validate_output -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'scripts.output_validator'"

- [ ] **Step 3: 实现 validate_output**

Create `scripts/output_validator.py`:

```python
"""输出验证模块"""
import os
import struct
from pathlib import Path
from typing import List
from loguru import logger


def get_mp4_duration(filepath: str) -> float:
    """提取 MP4 文件时长"""
    try:
        with open(filepath, "rb") as f:
            while True:
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if box_size == 0:
                    break
                if box_type == b"moov":
                    moov_data = f.read(min(box_size - 8, 200))
                    idx = moov_data.find(b"mvhd")
                    if idx >= 0:
                        mvhd = moov_data[idx:]
                        version = mvhd[4]
                        if version == 0:
                            timescale = struct.unpack(">I", mvhd[16:20])[0]
                            dur = struct.unpack(">I", mvhd[20:24])[0]
                        else:
                            timescale = struct.unpack(">I", mvhd[24:28])[0]
                            dur = struct.unpack(">Q", mvhd[28:36])[0]
                        if timescale > 0:
                            return dur / timescale
                    break
                else:
                    f.seek(box_size - 8, 1)
    except Exception:
        pass
    return 0.0


def validate_output(output_dir: str, expected_episodes: int = 60) -> List[str]:
    """
    验证输出文件的完整性和质量
    
    Args:
        output_dir: 输出目录
        expected_episodes: 预期集数
    
    Returns:
        问题列表
    """
    issues = []
    
    logger.info(f"验证输出: {output_dir}")
    
    for i in range(1, expected_episodes + 1):
        filepath = Path(output_dir) / f"episode_{i:03d}.mp4"
        
        # 检查文件是否存在
        if not filepath.exists():
            issues.append(f"缺失: 第 {i} 集")
            continue
        
        # 检查文件大小
        size = os.path.getsize(filepath)
        if size < 100_000:
            issues.append(f"异常: 第 {i} 集文件过小 ({size/1024:.1f}KB)")
        
        # 检查时长
        duration = get_mp4_duration(str(filepath))
        if duration < 10:
            issues.append(f"异常: 第 {i} 集时长过短 ({duration:.1f}秒)")
        elif duration > 300:
            issues.append(f"警告: 第 {i} 集时长过长 ({duration:.1f}秒)")
    
    if issues:
        logger.warning(f"发现 {len(issues)} 个问题")
    else:
        logger.info("验证通过，无问题")
    
    return issues
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_output_validator.py::test_validate_output -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/output_validator.py tests/test_output_validator.py
git commit -m "feat: 实现输出验证模块

验证文件完整性、大小和时长

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 主入口脚本

**Files:**
- Create: `scripts/extract_drama_from_cache.py`

- [ ] **Step 1: 实现主入口脚本**

Create `scripts/extract_drama_from_cache.py`:

```python
#!/usr/bin/env python
"""红果短剧缓存提取与切分主入口脚本"""
import argparse
import json
import sys
from pathlib import Path
from loguru import logger

from scripts.cache_puller import pull_and_sort_cache
from scripts.video_merger import merge_videos
from scripts.ocr_detector import detect_episode_boundaries
from scripts.split_planner import generate_split_plan
from scripts.video_splitter import split_episodes
from scripts.output_validator import validate_output, get_mp4_duration


def main():
    parser = argparse.ArgumentParser(description="红果短剧缓存提取与切分工具")
    parser.add_argument("--drama-name", required=True, help="短剧名称")
    parser.add_argument("--output", default="./videos", help="输出根目录")
    parser.add_argument("--expected-episodes", type=int, default=60, help="预期集数")
    parser.add_argument("--step", choices=["pull", "merge", "ocr", "split", "validate"], help="只执行指定步骤")
    parser.add_argument("--sample-interval", type=int, default=30, help="OCR 采样间隔（秒）")
    
    args = parser.parse_args()
    
    # 设置输出目录
    base_dir = Path(args.output) / args.drama_name
    cache_dir = base_dir / "cache"
    full_dir = base_dir / "全集"
    episodes_dir = base_dir / "独立集数"
    
    full_video_path = full_dir / f"{args.drama_name}_全集.mp4"
    boundaries_file = base_dir / "ocr_boundaries.json"
    plan_file = base_dir / "split_plan.json"
    
    # 步骤 1: 拉取缓存
    if not args.step or args.step == "pull":
        logger.info("[1/5] 拉取缓存文件...")
        file_count = pull_and_sort_cache(str(cache_dir))
        logger.info(f"✓ 拉取完成: {file_count} 个文件")
        if args.step:
            return
    
    # 步骤 2: 合并全集
    if not args.step or args.step == "merge":
        logger.info("[2/5] 合并全集视频...")
        merge_videos(str(cache_dir), str(full_dir), args.drama_name)
        duration = get_mp4_duration(str(full_video_path))
        size_mb = full_video_path.stat().st_size / 1024 / 1024
        logger.info(f"✓ 全集视频: {duration/60:.1f} 分钟 | {size_mb:.1f}MB")
        if args.step:
            return
    
    # 步骤 3: OCR 识别
    if not args.step or args.step == "ocr":
        logger.info("[3/5] OCR 识别集数边界...")
        boundaries = detect_episode_boundaries(str(full_video_path), args.sample_interval)
        
        with open(boundaries_file, "w", encoding="utf-8") as f:
            json.dump(boundaries, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✓ 检测到 {len(boundaries)} 个集数边界")
        logger.info(f"✓ 置信度: {'高' if len(boundaries) > args.expected_episodes * 0.9 else '中'}")
        if args.step:
            return
    
    # 步骤 4: 生成切分计划
    if not args.step or args.step == "split":
        logger.info("[4/5] 生成切分计划...")
        
        with open(boundaries_file, "r", encoding="utf-8") as f:
            boundaries = json.load(f)
        
        duration = get_mp4_duration(str(full_video_path))
        split_plan = generate_split_plan(boundaries, duration, args.expected_episodes)
        
        with open(plan_file, "w", encoding="utf-8") as f:
            json.dump(split_plan, f, indent=2, ensure_ascii=False)
        
        estimated_count = sum(1 for p in split_plan if p['confidence'] == 'estimated')
        if estimated_count > 0:
            logger.warning(f"⚠ 缺失集数: {estimated_count} 个 (使用插值估算)")
        
        logger.info("✓ 保存切分计划: split_plan.json")
        
        # 执行切分
        logger.info("[5/5] 切分独立集数...")
        success = split_episodes(str(full_video_path), split_plan, str(episodes_dir))
        logger.info(f"✓ 切分完成: {success}/{args.expected_episodes} 集")
        
        if args.step:
            return
    
    # 步骤 5: 验证输出
    if not args.step or args.step == "validate":
        logger.info("验证输出...")
        issues = validate_output(str(episodes_dir), args.expected_episodes)
        
        if issues:
            logger.warning(f"⚠️  需要人工检查:")
            for issue in issues[:5]:
                logger.warning(f"  - {issue}")
        
        # 生成报告
        report_file = base_dir / "REPORT.md"
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(f"# {args.drama_name} - 处理报告\n\n")
            f.write(f"## 输出目录\n")
            f.write(f"- 全集: {full_video_path}\n")
            f.write(f"- 独立集数: {episodes_dir}\n\n")
            if issues:
                f.write(f"## 问题列表\n")
                for issue in issues:
                    f.write(f"- {issue}\n")
        
        logger.info(f"✅ 处理完成！详细报告: {report_file}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 测试主脚本帮助信息**

Run: `python scripts/extract_drama_from_cache.py --help`
Expected: 显示帮助信息

- [ ] **Step 3: 提交**

```bash
git add scripts/extract_drama_from_cache.py
git commit -m "feat: 实现主入口脚本

编排完整流程: 拉取→合并→OCR→切分→验证

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 集成测试

**Files:**
- Create: `tests/test_cache_extraction.py`

- [ ] **Step 1: 编写端到端集成测试**

Create `tests/test_cache_extraction.py`:

```python
import pytest
from pathlib import Path
from scripts.extract_drama_from_cache import main

def test_end_to_end_extraction(mocker, tmp_path, monkeypatch):
    """端到端集成测试"""
    # Mock 命令行参数
    test_args = [
        "extract_drama_from_cache.py",
        "--drama-name", "测试短剧",
        "--output", str(tmp_path),
        "--expected-episodes", "3"
    ]
    monkeypatch.setattr("sys.argv", test_args)
    
    # Mock 各个模块
    mock_pull = mocker.patch('scripts.cache_puller.pull_and_sort_cache')
    mock_pull.return_value = 2
    
    mock_merge = mocker.patch('scripts.video_merger.merge_videos')
    mock_merge.return_value = str(tmp_path / "测试短剧/全集/测试短剧_全集.mp4")
    
    mock_ocr = mocker.patch('scripts.ocr_detector.detect_episode_boundaries')
    mock_ocr.return_value = [
        {"episode": 1, "start_time": 0, "confidence": 0.95},
        {"episode": 2, "start_time": 65, "confidence": 0.92},
        {"episode": 3, "start_time": 130, "confidence": 0.88},
    ]
    
    mock_split = mocker.patch('scripts.video_splitter.split_episodes')
    mock_split.return_value = 3
    
    mock_duration = mocker.patch('scripts.output_validator.get_mp4_duration')
    mock_duration.return_value = 195.0
    
    # 执行主流程
    main()
    
    # 验证调用
    assert mock_pull.called
    assert mock_merge.called
    assert mock_ocr.called
    assert mock_split.called
```

- [ ] **Step 2: 运行集成测试**

Run: `pytest tests/test_cache_extraction.py -v`
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add tests/test_cache_extraction.py
git commit -m "test: 添加端到端集成测试

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 文档和最终验证

**Files:**
- Create: `docs/cache-extraction-usage.md`

- [ ] **Step 1: 编写使用文档**

Create `docs/cache-extraction-usage.md`:

```markdown
# 红果短剧缓存提取使用指南

## 快速开始

### 1. 安装依赖

\`\`\`bash
pip install -r requirements.txt
\`\`\`

### 2. 确保手机已连接

\`\`\`bash
adb devices
\`\`\`

### 3. 运行提取脚本

\`\`\`bash
python scripts/extract_drama_from_cache.py \
    --drama-name "西游，错把玉帝当亲爹" \
    --output videos/西游错把玉帝当亲爹
\`\`\`

## 输出结构

\`\`\`
videos/西游错把玉帝当亲爹/
├── cache/                          # 原始缓存备份
├── 全集/
│   └── 西游错把玉帝当亲爹_全集.mp4
├── 独立集数/
│   ├── episode_001.mp4
│   └── ...
├── split_plan.json
└── REPORT.md
\`\`\`

## 分步执行

\`\`\`bash
# 只拉取缓存
python scripts/extract_drama_from_cache.py --step pull --drama-name "剧名" --output videos

# 只合并全集
python scripts/extract_drama_from_cache.py --step merge --drama-name "剧名" --output videos

# 只 OCR 识别
python scripts/extract_drama_from_cache.py --step ocr --drama-name "剧名" --output videos

# 只切分集数
python scripts/extract_drama_from_cache.py --step split --drama-name "剧名" --output videos
\`\`\`

## 故障排除

### OCR 识别率低

调整采样间隔：

\`\`\`bash
python scripts/extract_drama_from_cache.py --sample-interval 15 ...
\`\`\`

### 手动调整切分点

编辑 `split_plan.json`，然后重新运行：

\`\`\`bash
python scripts/extract_drama_from_cache.py --step split ...
\`\`\`
```

- [ ] **Step 2: 运行所有测试**

Run: `pytest tests/ -v`
Expected: 所有测试通过

- [ ] **Step 3: 提交文档**

```bash
git add docs/cache-extraction-usage.md
git commit -m "docs: 添加缓存提取使用指南

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: 最终验证 - 运行主脚本**

Run: `python scripts/extract_drama_from_cache.py --drama-name "西游，错把玉帝当亲爹" --output videos/西游错把玉帝当亲爹`
Expected: 完整流程执行成功，生成全集和独立集数

---

## 自审清单

**规格覆盖检查**：
- ✅ 阶段 1（拉取缓存）→ Task 2
- ✅ 阶段 2（合并全集）→ Task 3
- ✅ 阶段 3（OCR 识别）→ Task 4
- ✅ 阶段 4（切分计划）→ Task 5
- ✅ 阶段 5（执行切分）→ Task 6
- ✅ 阶段 6（质量验证）→ Task 7
- ✅ 命令行接口 → Task 8
- ✅ 错误处理 → 各模块内置
- ✅ 依赖项 → Task 1

**占位符检查**：无 TBD、TODO 或未完成代码

**类型一致性**：
- `pull_and_sort_cache` 返回 `int`
- `merge_videos` 返回 `str`
- `detect_episode_boundaries` 返回 `List[Dict]`
- `generate_split_plan` 返回 `List[Dict]`
- `split_episodes` 返回 `int`
- `validate_output` 返回 `List[str]`

所有函数签名在各任务中保持一致。