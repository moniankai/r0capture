# 红果短剧缓存提取与切分设计文档

## 项目概述

**目标**：从红果 App 离线缓存中提取《西游，错把玉帝当亲爹》60 集视频，生成全集合并版本和独立集数文件。

**用户需求**：
- 手机上已通过 App 离线缓存功能缓存了 60 集
- 需要复制到电脑并生成两种格式：
  1. 全集合并视频（单个文件）
  2. 独立集数文件（60 个独立 MP4）

**技术挑战**：
- App 缓存是 45 个 .mdl 文件（非 1 集 = 1 文件）
- 文件大小从 128KB 到 14MB 不等
- 每集时长不固定（40-200 秒）
- 需要自动识别集数边界

## 核心策略

**方案选择**：先合并后切分

**理由**：
1. 合并全集更简单（按时间排序 + ffmpeg concat）
2. 从全集切分更精确（连续 OCR 扫描，不漏边界）
3. 容错性更好（全集视频始终可用）

## 系统架构

```
手机缓存 (45 个 .mdl)
    ↓ ADB Pull + 按时间排序
本地缓存目录 (cache/)
    ↓ ffmpeg concat
全集视频 (全集/xxx_全集.mp4)
    ↓ OCR 扫描识别集数边界
切分计划 (split_plan.json)
    ↓ ffmpeg split
独立集数 (独立集数/episode_001.mp4 ~ 060.mp4)
```

## 详细设计

### 阶段 1：拉取并排序缓存

**输入**：手机 `/sdcard/Android/data/com.phoenix.read/cache/short/*.mdl`
**输出**：`videos/西游错把玉帝当亲爹/cache/`

**实现**：
1. 使用 ADB 列出所有 .mdl 文件及其修改时间
2. 按修改时间升序排序（假设播放顺序 = 缓存顺序）
3. 批量拉取到本地，重命名为 `001_xxx.mdl`, `002_xxx.mdl`...
4. 生成 ffmpeg concat 列表文件

**关键代码**：
```python
def pull_and_sort_cache(output_dir: str):
    remote_files = list_remote_mdl()  # 获取文件列表
    sorted_files = sorted(remote_files, key=lambda x: x['date'])
    
    for i, f in enumerate(sorted_files):
        local_name = f"{i+1:03d}_{f['name']}"
        run_adb(['pull', f['path'], f"{output_dir}/{local_name}"])
    
    # 生成 concat 列表
    with open(f"{output_dir}/concat_list.txt", "w") as f:
        for i in range(len(sorted_files)):
            f.write(f"file '{i+1:03d}_*.mdl'\n")
```

### 阶段 2：合并全集视频

**输入**：`cache/concat_list.txt` + 45 个 .mdl 文件
**输出**：`全集/西游错把玉帝当亲爹_全集.mp4`

**实现**：
```bash
ffmpeg -f concat -safe 0 -i cache/concat_list.txt \
       -c copy \
       全集/西游错把玉帝当亲爹_全集.mp4
```

**参数说明**：
- `-f concat`：使用 concat 协议合并文件
- `-safe 0`：允许任意路径
- `-c copy`：流复制，不重新编码（最快，无质量损失）

**预期结果**：
- 单个 MP4 文件，约 129MB
- 时长约 60-120 分钟（取决于每集长度）

### 阶段 3：OCR 识别集数边界

**输入**：`全集/西游错把玉帝当亲爹_全集.mp4`
**输出**：集数边界列表（JSON）

**采样策略**：
- 每 30 秒提取一帧画面
- 对每帧进行 OCR 文字识别
- 匹配集数模式：`第X集`、`EPX`、`X/60`

**实现**：
```python
import cv2
import easyocr
import re

def detect_episode_boundaries(video_path: str):
    reader = easyocr.Reader(['ch_sim', 'en'])
    cap = cv2.VideoCapture(video_path)
    
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
    boundaries = []
    current_episode = 0
    
    for timestamp in range(0, int(duration), 30):
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ret, frame = cap.read()
        if not ret:
            continue
        
        results = reader.readtext(frame)
        for (bbox, text, confidence) in results:
            match = re.search(r'第\s*(\d+)\s*集|EP\s*(\d+)|(\d+)\s*/\s*60', text)
            if match and confidence > 0.7:
                episode_num = int(match.group(1) or match.group(2) or match.group(3))
                if episode_num > current_episode:
                    boundaries.append({
                        'episode': episode_num,
                        'start_time': timestamp,
                        'confidence': confidence
                    })
                    current_episode = episode_num
    
    return boundaries
```

**输出示例**：
```json
[
  {"episode": 1, "start_time": 0, "confidence": 0.95},
  {"episode": 2, "start_time": 65, "confidence": 0.92},
  {"episode": 3, "start_time": 130, "confidence": 0.88}
]
```

### 阶段 4：生成切分计划

**输入**：OCR 边界列表 + 全集视频总时长
**输出**：`split_plan.json`

**处理逻辑**：
1. 遍历 1-60 集，查找每集的起始时间
2. 结束时间 = 下一集的起始时间（最后一集到视频结尾）
3. 对于 OCR 未检测到的集数，使用插值估算

**实现**：
```python
def generate_split_plan(boundaries: list, total_duration: float):
    split_plan = []
    
    for i in range(60):
        episode_num = i + 1
        start_time = next((b['start_time'] for b in boundaries 
                          if b['episode'] == episode_num), None)
        
        if i < 59:
            end_time = next((b['start_time'] for b in boundaries 
                           if b['episode'] == episode_num + 1), None)
        else:
            end_time = total_duration
        
        # 处理缺失边界
        if start_time is None:
            prev = boundaries[i-1]['start_time'] if i > 0 else 0
            next_b = boundaries[i+1]['start_time'] if i < len(boundaries)-1 else total_duration
            start_time = (prev + next_b) / 2
            confidence = 'estimated'
        else:
            confidence = 'detected'
        
        split_plan.append({
            'episode': episode_num,
            'start': start_time,
            'end': end_time,
            'duration': end_time - start_time if end_time else None,
            'confidence': confidence
        })
    
    return split_plan
```

### 阶段 5：执行切分

**输入**：`split_plan.json` + 全集视频
**输出**：`独立集数/episode_001.mp4` ~ `episode_060.mp4`

**实现**：
```python
def split_episodes(full_video: str, split_plan: list, output_dir: str):
    import subprocess
    from tqdm import tqdm
    
    os.makedirs(output_dir, exist_ok=True)
    
    for item in tqdm(split_plan, desc="切分集数"):
        episode_num = item['episode']
        start = item['start']
        end = item['end']
        output_file = f"{output_dir}/episode_{episode_num:03d}.mp4"
        
        cmd = [
            'ffmpeg', '-i', full_video,
            '-ss', str(start), '-to', str(end),
            '-c', 'copy',
            '-avoid_negative_ts', '1',
            '-y', output_file
        ]
        
        subprocess.run(cmd, capture_output=True, check=True)
```

**参数说明**：
- `-ss` / `-to`：指定起始和结束时间
- `-c copy`：流复制，不重新编码
- `-avoid_negative_ts 1`：避免时间戳问题

### 阶段 6：质量验证

**验证项**：
1. 文件完整性：检查 60 个文件是否都存在
2. 文件大小：检测异常小的文件（< 100KB）
3. 时长检查：检测异常短（< 10秒）或异常长（> 5分钟）的集数
4. 可播放性：尝试读取 MP4 元数据

**实现**：
```python
def validate_output(output_dir: str):
    issues = []
    
    for i in range(1, 61):
        filepath = f"{output_dir}/episode_{i:03d}.mp4"
        
        if not os.path.exists(filepath):
            issues.append(f"缺失: 第 {i} 集")
            continue
        
        size = os.path.getsize(filepath)
        if size < 100_000:
            issues.append(f"异常: 第 {i} 集文件过小 ({size/1024:.1f}KB)")
        
        duration = get_mp4_duration(filepath)
        if duration < 10:
            issues.append(f"异常: 第 {i} 集时长过短 ({duration:.1f}秒)")
        elif duration > 300:
            issues.append(f"警告: 第 {i} 集时长过长 ({duration:.1f}秒)")
    
    return issues
```

## 命令行接口

### 主入口脚本

**脚本名称**：`scripts/extract_drama_from_cache.py`

**使用方式**：
```bash
# 一键执行完整流程
python scripts/extract_drama_from_cache.py \
    --drama-name "西游，错把玉帝当亲爹" \
    --output videos/西游错把玉帝当亲爹

# 分步执行
python scripts/extract_drama_from_cache.py --step pull      # 只拉取缓存
python scripts/extract_drama_from_cache.py --step merge     # 只合并全集
python scripts/extract_drama_from_cache.py --step ocr       # 只做 OCR
python scripts/extract_drama_from_cache.py --step split     # 只切分集数
python scripts/extract_drama_from_cache.py --step validate  # 只验证结果
```

### 执行流程输出

```
[1/5] 拉取缓存文件...
  ✓ 检测到 45 个 .mdl 文件
  ✓ 按修改时间排序
  ✓ 拉取进度: 45/45 [████████████] 129MB 100%
  ✓ 保存到: videos/西游错把玉帝当亲爹/cache/

[2/5] 合并全集视频...
  ✓ 生成 concat 列表
  ✓ ffmpeg 合并中... (预计 30 秒)
  ✓ 全集视频: 92 分钟 | 129MB
  ✓ 保存到: 全集/西游错把玉帝当亲爹_全集.mp4

[3/5] OCR 识别集数边界...
  ✓ 加载 EasyOCR 模型...
  ✓ 扫描进度: 184/184 帧 [████████████] 100%
  ✓ 检测到 57 个集数边界
  ✓ 置信度: 高 (95%)

[4/5] 生成切分计划...
  ✓ 检测到的集数: 57 / 60
  ⚠ 缺失集数: 第 15, 37, 52 集 (使用插值估算)
  ✓ 保存切分计划: split_plan.json

[5/5] 切分独立集数...
  ✓ 切分进度: 60/60 [████████████] 100%
  ✓ 保存到: 独立集数/episode_001.mp4 ~ episode_060.mp4

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ 处理完成！

📁 输出目录:
  - 全集/西游错把玉帝当亲爹_全集.mp4 (129MB, 92分钟)
  - 独立集数/ (60 个文件)

⚠️  需要人工检查:
  - 第 15 集: 时长 8 秒 (可能切分不准)
  - 第 37 集: 时长 245 秒 (可能包含多集)

📄 详细报告: REPORT.md
```

## 最终输出结构

```
videos/西游错把玉帝当亲爹/
├── cache/                          # 原始缓存备份
│   ├── 001_92797a546db1c2bfb865d61254f9c41d.mdl
│   ├── 002_07b95a3196140088af7aa33eec556c7f.mdl
│   ├── ...
│   ├── 045_xxx.mdl
│   └── concat_list.txt
├── 全集/
│   └── 西游错把玉帝当亲爹_全集.mp4   # 完整 60 集合并
├── 独立集数/
│   ├── episode_001.mp4
│   ├── episode_002.mp4
│   ├── ...
│   └── episode_060.mp4
├── split_plan.json                 # 切分计划（含时间戳）
├── ocr_boundaries.json             # OCR 原始检测结果
└── REPORT.md                       # 处理报告
```

## 错误处理

### OCR 识别失败

**场景**：某些集数的标题无法识别（字体特殊、遮挡、无标题）

**处理**：
1. 使用插值估算缺失边界
2. 在报告中标记为 `confidence: estimated`
3. 建议用户手动检查这些集数

### 切分异常

**场景**：某集时长异常（过短或过长）

**处理**：
1. 仍然生成文件，但在报告中标记
2. 提供手动调整建议
3. 保留 `split_plan.json` 供用户编辑后重新切分

### 文件缺失

**场景**：某些缓存文件损坏或缺失

**处理**：
1. 跳过损坏文件，继续处理其他文件
2. 在报告中列出缺失的集数范围
3. 建议用户重新缓存缺失部分

## 依赖项

### Python 包

```txt
opencv-python>=4.8.0      # 视频帧提取
easyocr>=1.7.0            # OCR 文字识别
tqdm>=4.66.0              # 进度条
loguru>=0.7.0             # 日志
```

### 系统工具

- **ffmpeg**：视频合并和切分
- **ADB**：从手机拉取缓存文件

## 性能估算

**处理时间**（基于 60 集，129MB）：
- 拉取缓存：2-3 分钟（取决于 USB 速度）
- 合并全集：30-60 秒
- OCR 识别：5-10 分钟（取决于 CPU/GPU）
- 切分集数：2-3 分钟
- **总计**：约 10-15 分钟

**磁盘空间**：
- 原始缓存：129MB
- 全集视频：129MB
- 独立集数：129MB（共享相同数据）
- **总计**：约 387MB

## 技术权衡

### 为什么先合并后切分？

**优势**：
1. **简化逻辑**：不需要精确知道每个缓存文件包含哪些集数
2. **提高准确性**：连续 OCR 扫描不会漏掉边界
3. **容错性好**：即使切分失败，全集视频仍可用
4. **易于调试**：可以手动在全集视频中定位问题

**劣势**：
1. **磁盘空间**：需要额外 129MB 存储全集视频
2. **处理时间**：多一次合并操作（但只需 30-60 秒）

### 为什么使用 OCR 而非元数据？

**原因**：
1. MP4 元数据通常不包含集数信息
2. 缓存文件名是 MD5 hash，无规律
3. 文件修改时间只能推断顺序，无法确定边界
4. OCR 是唯一能从视频内容识别集数的方法

**替代方案**：
- 用户手动标注（最准确，但耗时）
- 场景切换检测（不可靠，转场不一定是集数边界）

### 为什么使用 EasyOCR？

**优势**：
1. 支持中文识别
2. 无需训练，开箱即用
3. GPU 加速支持
4. 返回置信度评分

**替代方案**：
- Tesseract：中文识别较弱
- PaddleOCR：性能更好，但依赖更重

## 未来优化

1. **并行处理**：OCR 和切分可以并行执行
2. **增量更新**：支持只处理新增的缓存文件
3. **智能采样**：检测场景切换，减少 OCR 帧数
4. **批量处理**：支持一次处理多部短剧
5. **Web UI**：提供可视化界面，方便手动调整边界

## 风险与限制

### 已知限制

1. **OCR 准确率**：取决于视频质量和字幕样式，预计 90-95%
2. **集数时长不固定**：无法通过时长推断，完全依赖 OCR
3. **跨文件集数**：如果某集被分散在多个缓存文件中，合并后可正常处理
4. **缓存完整性**：假设 App 显示的"已缓存 60 集"是准确的

### 潜在风险

1. **OCR 完全失败**：如果视频无字幕或字幕位置特殊，可能无法识别
   - **缓解**：提供手动编辑 `split_plan.json` 的机制
2. **缓存文件损坏**：某些文件可能无法播放
   - **缓解**：验证阶段检测并报告
3. **磁盘空间不足**：需要约 400MB 空间
   - **缓解**：处理前检查磁盘空间

## 成功标准

1. **功能完整性**：
   - ✅ 生成完整的全集视频
   - ✅ 生成 60 个独立集数文件
   - ✅ 所有文件可正常播放

2. **准确性**：
   - ✅ OCR 识别率 > 90%
   - ✅ 切分边界误差 < 5 秒
   - ✅ 无缺失集数

3. **用户体验**：
   - ✅ 一键执行，无需手动干预
   - ✅ 清晰的进度提示
   - ✅ 详细的错误报告

4. **性能**：
   - ✅ 总处理时间 < 20 分钟
   - ✅ 磁盘占用 < 500MB
