# 红果 DRM 深度逆向笔记（2026-04-17）

## TL;DR

`libttmplayer.so + 0xac7f4` 是一个**纯函数 F**：接受 37 字节 spadea payload + w4=0，输出 16 字节 raw AES-128 key 的 hex 字符串。已通过 Frida `NativeFunction` 主动 invoke 验证，100% 输出匹配。

由此可**跳过逐集播放等 av_aes_init**，拿到 spadea 立即算 key。

## 关键 Hook 点位

### Java 层
| 位置 | 作用 | 参数/字段 |
|---|---|---|
| `TTVideoEngine.setVideoModel(VideoModel)` | 视频播放入口 | 从 `model.getVideoRef().getVideoInfoList()` 遍历 `mKid` / `mSpadea` |
| `VideoModel.fromMediaInfoJsonString(String)` | JSON 反序列化入口（**未被 App 走**） | - |
| `VideoRef.extractFields(JSONObject)` | JSON → VideoRef populate（**实际走这条**） | JSONObject 含 `dynamic_video_list` |
| `VideoInfo` 字段 | 每画质一条 | `mKid(32hex)` / `mSpadea(base64)` / `mMainUrl` / `mBitrate` / `mVHeight` / `mEncrypt=true` |

### Native 层（libttffmpeg.so）
| 符号 | 作用 |
|---|---|
| `av_base64_decode` | 解码 spadea（52 base64 → 37 bytes） |
| `av_dict_set("decryption_key", <32-char hex>)` | App 把算好的 raw key 注入 FFmpeg 上下文 |
| `av_opt_set("decryption_key", ...)` | 同上 |
| `av_aes_ctr_init` → `av_aes_init` | FFmpeg 内部 AES-CTR 初始化（key 已由上面注入） |

### Native 层（libttmplayer.so）— 破解目标
| 位置 | 作用 |
|---|---|
| `libttmplayer+0x165660` | `av_base64_decode(spadea, ...)` 的 caller |
| `libttmplayer+0x1eb91c` | `av_dict_set("decryption_key", ...)` 的 caller |
| **`libttmplayer+0xac7f4`** | **F 函数：spadea_bytes → raw_key_hex 的解密核心** |

## F 函数详情

### 调用签名（从 caller 反汇编推导）
```c
int F(
    const uint8_t *spadea_bytes,  // x0: av_base64_decode 输出
    int           length,          // w1: 37
    char        **out_str,         // x2: 输出 raw_key 的 hex 字符串 (malloc'd by F, 32 chars + \0)
    char        **aux_str,         // x3: 输出一个辅助字符串（观察值恒为 "1"）
    int           w4_flag          // w4: 恒为 0（来自 caller 的 vtable[9](this, 0x9c, 0)）
);
// return: int (8 = 成功)
```

### 关键算法特征（反汇编 0xac7f4-0xacabc）

1. **前缀校验**：`xor_val = spadea[0] ^ spadea[1] ^ spadea[2]`，`w27 = (xor_val & 0xff) - 0x30`，要求 `w27 >= 1`
2. **派生长度**：`w22 = (37 - xor_val) + 0x2f`
3. **Buffer A 分配**：`malloc(w22)` 作输出缓冲
4. **Buffer B 分配**：`malloc(w27)` 作中间缓冲
5. **动态 XOR 循环**（首轮）：
   - `xor_key = spadea[37-w27-2] ^ spadea[37-w27-1]` （从 spadea 尾部取两字节 XOR）
   - 对 Buffer B 的每个字节：`B[i] = spadea[37-w27+i] ^ xor_key`
6. **字符串比较分支**（看 0x22b000+0xb0a / +0xb11 两个常量字符串）
7. **popcount 混淆循环**：用 `cnt`/`uaddlv` 统计 bit 数 + 魔数 0x55/0xfa/0x15 做偏置
8. **字节交换** pass（两两 swap）
9. **最终输出**：写到 `*out_str`（hex string）和 `*aux_str`（"1"）

**重点**：整个算法**无全局状态读**、**无线程/时间依赖**、**无系统调用**。纯数学变换。w4=0 分支的数据流完全由 `spadea_bytes` 决定。

### 验证：Frida 主动 invoke

```js
var F = new NativeFunction(libmp_base.add(0xac7f4),
    'int', ['pointer', 'int', 'pointer', 'pointer', 'int']);

var spadeaBytes = hexToBytes('93bc1df3568b18f164b72ef8...');  // 37 bytes
var inMem = Memory.alloc(spadeaBytes.length);
// ... copy bytes ...
var outPP = Memory.alloc(8); outPP.writePointer(ptr(0));
var auxPP = Memory.alloc(8); auxPP.writePointer(ptr(0));

var ret = F(inMem, 37, outPP, auxPP, 0);
var rawKeyHex = outPP.readPointer().readCString();  // 32-char hex
```

两组独立样本 invoke 输出 100% 匹配 App 内 `av_dict_set("decryption_key")` 的 value。

## 运行时数据关系

```
VideoInfo.mSpadea (base64, 52 chars)
    ↓ av_base64_decode  (libttffmpeg)
spadea_bytes (37 bytes)
    ↓ F(..., w4=0)      (libttmplayer+0xac7f4)
raw_key hex (32 chars)
    ↓ av_dict_set       (libttffmpeg)
AVFormatContext 的 "decryption_key" option
    ↓ avformat/avcodec 内部读取
av_aes_ctr_init(key, iv, ...)
    ↓
av_aes_init → 实际 AES-CTR-128 解密 MP4 sample
```

## 未解谜团

- **0xac7f4 F 函数的数学本质**：反汇编了结构但没翻译成 Python 实现。主动 invoke 已足够实用，离线实现暂不做。
- **spadea 在 JSON 中的字段名**：Java 层看到 `mSpadea`，但 API 响应的 JSON 字段原名未确认（可能是 `"spadea"`、`"ext_sign"`、`"drm_token"` 等）。由于 App 不走 `fromMediaInfoJsonString`，走的是 `extractFields(JSONObject)`，需抓 JSONObject 的 `keys` 才能确认。

## 落地方案

参见后续 `scripts/download_v4.py`（基于本发现重写的全集下载器）。

## 复现步骤

- Frida Hook 点位实现参考：`scripts/probe_videomodel_key.py`（字段反射）
- 时序观察参考：`scripts/probe_spadea_key.py`（setVideoModel + av_aes_init 对齐）
- Native Hook 参考：`scripts/probe_drm_hook.py`（av_idrm_open、AES_unwrap_key、av_opt_set）
- F 函数定位 + invoke PoC 在 `d:/tmp/*` 下（一次性实验脚本）
