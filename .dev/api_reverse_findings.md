# 红果 App 剧集列表 API 逆向发现

研究时间：2026-04-17
目标：绕过 UI 自动化，直接拿全剧 vid 清单。

## 一、已识别接口

| 接口 | 作用 |
|------|------|
| `https://reading.snssdk.com/novel/player/video_detail/v1/` | 返回剧详情 + 全集 video_list（含 biz_vid） |
| `https://reading.snssdk.com/novel/player/multi_video_model/v1/` | 批量返回 video_model（含 tt_vid + CDN URL） |
| `https://reading.snssdk.com/novel/player/multi_video_detail/v1/` | 多视频详情 |
| `https://reading.snssdk.com/reading/distribution/book_pack_fields/series_detail_info/v1` | 剧集 pack 字段 |

## 二、关键数据结构

### video_detail 响应（核心）

```json
{
  "BaseResp": {"StatusCode": 0},
  "code": 0,
  "data": {
    "video_data": {
      "episode_cnt": 83,
      "series_id": "...",
      "video_list": [
        {
          "vid_index": 1,
          "vid": "7622962558860807193",   // 业务 vid（19 位）
          "duration": 87,
          "title": "..."
        },
        ...  // 共 83 项
      ]
    }
  }
}
```

### multi_video_model 响应

```json
{
  "data": {
    "7622962551189408793": {           // biz_vid 为 key
      "expire_time": 1776412384,
      "video_height": 720,
      "video_model": "{\"video_id\":\"v02ebeg10000d770cr2ljht6dvr9r2tg\",...,\"video_list\":[{\"main_url\":\"https://...\",\"backup_url\":\"https://...\"}]}"
    }
  }
}
```

关键字段：
- `biz_vid`（外层 key）：19 位数字，业务 ID
- `tt_vid`（video_model.video_id）：`v02xxx` 格式，TTVideoEngine 消费
- `main_url` / `backup_url`：CDN MP4（CENC 加密）

## 三、Hook 技术要点

### TTNet 栈

- 上层：`com.bytedance.retrofit2.SsHttpCall.execute()` 返回 `SsResponse`
- body 包装：`SsResponse.body()` 返回 `com.bytedance.frameworks.baselib.network.http.impl.a$a`
- 读取：需 `Java.cast(bodyObj, a$a_cls)`，再调 `in()` 获取 `InputStream`
- 注意：直接调用 `body.in()` 报 `TypeError: not a function`，必须先 cast

### 流复用问题

`a$a.in()` 只能读一次。实际使用需 tee：读全流→ByteArrayOutputStream→重建 TypedByteArray 回写 body。当前实现会让业务层读空。

## 四、AES Key 缺口

API 响应含 CDN URL 但**不含 AES key**。key 仍需要在播放时由 `av_aes_init`（libttffmpeg.so）捕获。

## 五、主动触发 setVideoID 测试

```js
TTVideoEngine.setVideoID(biz_vid);  // ok
TTVideoEngine.play();               // ok (returns)
// 但实际未触发 fetchInfo，因 playerClient 缺 Surface
// logcat: "setScreenOnWhilePlaying(true) is ineffective without a SurfaceHolder"
```

结论：空闲实例直接主动调用无效，需提供 Surface 或使用已绑定 Surface 的实例。

## 六、下一步可选方向

1. **一体化 Hook**（推荐）：同时 Hook `multi_video_model`（biz_vid→tt_vid+url）和 `av_aes_init`（tt_vid→key）；让 App 自然播放，持续采集完整映射。
2. **修改 download_hongguo.py**：把 `video_detail` 返回的 83 集 vid 列表作为下载目标清单，精准切集。
3. **攻克主动触发**：拿已绑定 Surface 的实例（state=1/2），调 setVideoID 切换视频。

## 七、相关脚本

- `scripts/hook_ttnet.py` — 枚举 TTNet 请求 URL
- `scripts/hook_multi_video_model.py` — Hook 响应对象（只拿包装类字段）
- `scripts/hook_mvm_response.py` — 读取响应字节流 + 分块 send
- `scripts/inspect_body_class.py` — 枚举 body 包装类方法
- `scripts/probe_ttengine_instances.py` — 枚举 TTVideoEngine 实例
- `scripts/explore_ttengine.py` — 列 TTVideoEngine 方法签名
- `scripts/test_active_trigger.py` — 测试主动 setVideoID + play

## 八、本次会话产物

- `d:/tmp/episodes_83.json` — 全 83 集 biz_vid 清单（vid_index 排序）
- `d:/tmp/mvm_response_log.txt` — 原始响应 dump
