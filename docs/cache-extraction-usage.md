# 红果短剧缓存提取使用指南

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 确保手机已连接

```bash
adb devices
```

### 3. 运行提取脚本

```bash
python scripts/extract_drama_from_cache.py \
    --drama-name "西游，错把玉帝当亲爹" \
    --output videos/西游错把玉帝当亲爹
```

## 输出结构

```
videos/西游错把玉帝当亲爹/
├── cache/                          # 原始缓存备份
├── 全集/
│   └── 西游错把玉帝当亲爹_全集.mp4
├── 独立集数/
│   ├── episode_001.mp4
│   └── ...
├── split_plan.json
└── REPORT.md
```

## 分步执行

```bash
# 只拉取缓存
python scripts/extract_drama_from_cache.py --step pull --drama-name "剧名" --output videos

# 只合并全集
python scripts/extract_drama_from_cache.py --step merge --drama-name "剧名" --output videos

# 只 OCR 识别
python scripts/extract_drama_from_cache.py --step ocr --drama-name "剧名" --output videos

# 只切分集数
python scripts/extract_drama_from_cache.py --step split --drama-name "剧名" --output videos
```

## 故障排除

### OCR 识别率低

调整采样间隔：

```bash
python scripts/extract_drama_from_cache.py --sample-interval 15 ...
```

### 手动调整切分点

编辑 `split_plan.json`，然后重新运行：

```bash
python scripts/extract_drama_from_cache.py --step split ...
```
