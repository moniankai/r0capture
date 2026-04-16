# 取证报告：批量下载中断 (凡人仙葫第一季)

**时间：** 2026-04-15 18:55 UTC+8  
**命令：** `python scripts/download_drama.py -n "凡人仙葫第一季" --search -b 5`  
**结果：** 仅下载第7集，目标第1-5集（或7-11集）全部跳过，手机被推至第12集

---

## 证据清单

| 证据 | 内容 |
|------|------|
| `videos/凡人仙葫第一季/session_manifest.jsonl` | 仅一条记录，episode=7，ui_total_episodes=60 |
| `videos/凡人仙葫第一季/` 目录 | 仅有 `_episode_007_uvqj29b0.mp4`（13.1 MB） |
| `meta_ep007_uvqj29b0.json` | captured_video_url_count=20, captured_key_count=4 — 首轮捕获正常 |
| 手机当时状态 | 正在播放第12集（两个 Python 进程仍在运行） |

---

## 根因分析（按严重度排序）

### 根因 A：上滑路径捕获窗口过短（12秒）

**代码位置：** `download_drama.py:1993`

```python
swipe_next_episode()
time.sleep(3)          # 固定等 3 秒
if wait_for_capture(timeout_seconds=12):   # ← 仅 12 秒
```

**时序分析：**

| 步骤 | 耗时 |
|------|------|
| `state.clear()` + `sleep(1)` | 1s |
| `get_current_activity()` (10s timeout) | 1–5s |
| `read_ui_xml_from_device()` (20s timeout) | 2–8s |
| `swipe_next_episode()` + `sleep(3)` | ~3.5s |
| **从 state.clear 到 wait_for_capture 开始** | **~7–17s** |

上滑后 App 需要：手势识别(0.5s) → 动画过渡(1-2s) → CDN 请求新集元数据 → `setVideoModel` 触发 URL hook → `av_aes_init` 触发 key hook。全程耗时 **5–20 秒**（受网络质量影响）。

12 秒的窗口在网络稍慢时必然超时。

### 根因 B：选集恢复路径捕获窗口同样过短（12秒）

**代码位置：** `download_drama.py:1962`

```python
time.sleep(2)
if not wait_for_capture(timeout_seconds=12):  # ← 同样 12 秒
```

选集面板点击后 App 加载新集，行为与上滑相同，12 秒不够。

### 根因 C：App 续播记录导致错误起始集（静默接受乱序集）

**发生过程：**

1. 脚本 force-stop → spawn App，搜索"凡人仙葫第一季"，目标起始集=1
2. App 的"继续观看"记录让播放器自动跳至第7集
3. `wait_for_target_episode_on_device(name, 1, timeout=20s)` 轮询20秒，UI 始终显示第7集 → 返回 False
4. `search_drama_in_app` 返回 False，脚本打印警告并继续等待捕获
5. Hook 捕获到第7集数据，`download_and_decrypt(ep_num=1)` 被调用
6. `actual_episode=7 > expected=1`，`should_accept_out_of_order_episode(7, 1, 60, ...)` 返回 True
7. 第7集被静默接受为"填补缺口"，下载成功
8. `current_ep = 7`，批量循环从第8集开始（而非用户预期的第1集）

### 根因 D：恢复链每次尝试都额外推进手机集数

上滑 → 失败 → 选集面板（内部调用 `tap`+UI reads） → 失败 → search（HOME+deeplink+搜索+点击）

search 路径虽有 180s 完整超时，但 `wait_for_target_episode_on_device` 在 search 内部只有 **20秒**（`download_drama.py:1382`）。如果 App 过渡动画或 CDN 响应偏慢，该检查返回 False，搜索路径整体返回 False，批量循环立即 `break`。

---

## 异常类型检查

| 类型 | 状态 | 证据 |
|------|------|------|
| 捕获超时循环 | ✅ 确认 | 12s 窗口 × 2处，视频加载需 5–20s |
| 错误起始集（App 续播） | ✅ 确认 | manifest 显示 episode=7 而非 1 |
| 恢复链累积推进手机 | ✅ 确认 | 手机被推至第12集，仅下载1集 |
| 搜索恢复过早退出 | ✅ 确认 | `wait_for_target_episode_on_device` 仅20s |
| Hook 注入失败 | ❌ 排除 | 首集 URL×20 + key×4 正常捕获 |
| 解密失败 | ❌ 排除 | sample_count=6282 说明解密完成 |

---

## 修复方案

| 修复 | 文件:行号 | 改动 |
|------|-----------|------|
| F1：增大上滑后等待 | `download_drama.py:1991` | `sleep(3)` → `sleep(5)` |
| F2：增大上滑捕获超时 | `download_drama.py:1993` | `timeout_seconds=12` → `timeout_seconds=30` |
| F3：增大选集恢复捕获超时 | `download_drama.py:1962` | `timeout_seconds=12` → `timeout_seconds=25` |
| F4：增大搜索内集数确认超时 | `download_drama.py:1382` | `timeout_seconds=20` → `timeout_seconds=40` |

**不修复（已知限制）：** App 续播记录导致从第7集而非第1集开始——这需要更大的架构改动（在搜索成功后强制导航到第1集，并确认UI），纳入后续计划。
