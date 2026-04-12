# 红果免费短剧 视频抓包解密技术报告

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| 设备 | 小米6, Android 9, 已 Root (Magisk) | |
| Python | 3.6+ | |
| Frida | **16.5.9** | 17.x 在 Android 9 上 Java bridge 不可用 |
| frida-server | **16.5.9** arm64 | 版本必须与 pip 包完全一致 |
| PyCryptodome | any | `pip install pycryptodome` |
| OpenCV | any (可选) | 验证用 |

## 技术栈分析

```
┌─────────────────────────────────────────────────────────┐
│  App: com.phoenix.read (红果免费短剧)                     │
├─────────────────────────────────────────────────────────┤
│  Java 层                                                │
│  ├── TTVideoEngine (视频播放控制)                         │
│  │   ├── setVideoModel() → 传入视频元数据                 │
│  │   │   ├── mMainUrl (CDN 下载链接)                     │
│  │   │   ├── mKid (CENC Key ID)                         │
│  │   │   ├── mSpadea (Intertrust DRM 加密令牌)           │
│  │   │   └── mEncrypt = true                            │
│  │   └── play() → 触发播放                               │
│  └── TTVideoEngineImpl._initIntertrustDrm()             │
│      └── 解密 spadea → 提取 AES 密钥 → 传给 native       │
├─────────────────────────────────────────────────────────┤
│  Native 层                                              │
│  ├── libttmplayer.so (播放器主库)                        │
│  │   └── 通过 JNI 接收密钥,传给 FFmpeg                   │
│  ├── libttffmpeg.so (FFmpeg 独立库)                      │
│  │   ├── av_aes_init(ctx, key, 128, 0) ← 密钥在这里!    │
│  │   └── av_aes_crypt(ctx, dst, src, 1, NULL, 0) CTR    │
│  ├── libttboringssl.so (TLS)                            │
│  └── libsscronet.so (Cronet 网络栈)                     │
├─────────────────────────────────────────────────────────┤
│  加密方案                                                │
│  ├── CENC (Common Encryption, ISO 23001-7)               │
│  ├── AES-128-CTR (encrypt 方向用于 CTR 加解密)           │
│  ├── 8 字节 IV + 8 字节计数器                             │
│  ├── 无 PSSH (非 Widevine/PlayReady)                     │
│  └── Intertrust ExpressPlay DRM (spadea 令牌)            │
└─────────────────────────────────────────────────────────┘
```

## 操作流程

### 第一步: 安装环境

```bash
pip install frida==16.5.9 frida-tools==12.5.0 pycryptodome requests

# 下载 frida-server-16.5.9-android-arm64.xz
# 解压后推送到设备:
adb push frida-server /data/local/tmp/frida-server
adb shell "su -c 'chmod 755 /data/local/tmp/frida-server'"
adb shell "su -c '/data/local/tmp/frida-server -D &'"
```

### 第二步: 捕获视频元数据 (URL + KID + spadea)

```bash
python scripts/auto_capture.py --duration 120
# 会 spawn App, hook TTVideoEngine.setVideoModel
# 在手机上打开一部短剧并播放
# 输出: videos/captured_videos.json
```

### 第三步: 捕获 AES 解密密钥

```bash
python scripts/capture_key.py
# 监控 dlopen 等待 libttffmpeg.so 加载
# hook av_aes_init 捕获 128-bit AES 密钥
# 在手机上播放视频
# 输出: 解密密钥 (hex)
```

### 第四步: 下载并解密

```bash
python scripts/decrypt_video.py --key <hex_key> --url <cdn_url> -o output.mp4
# 下载 CENC 加密的 MP4
# 解析两个 trak (vide + soun) 的 stsz/stco/stsc/senc
# AES-CTR 解密所有 sample
# 修复 encv→hvc1, enca→mp4a 元数据
```

## 关键 Frida Hook 点

### 1. 视频元数据 (Java 层)

```javascript
// TTVideoEngine.setVideoModel → 获取 VideoRef → mVideoList
Java.use("com.ss.ttvideoengine.TTVideoEngine")
  .setVideoModel.overloads.forEach(ov => {
    ov.implementation = function(model) {
      // 反射读取 model.vodVideoRef.mVideoList[i].mMainUrl / mKid / mSpadea
    };
  });
```

### 2. AES 解密密钥 (Native 层)

```javascript
// 必须等 libttffmpeg.so 加载后才能 hook
var resolver = new ApiResolver("module");
var m = resolver.enumerateMatches("exports:*libttffmpeg*!av_aes_init");
Interceptor.attach(m[0].address, {
  onEnter: function(args) {
    // args[1] = 16 字节 AES-128 密钥指针
    // args[2] = 128 (key bits)
    // args[3] = 0 (encrypt, CTR 模式用 encrypt)
  }
});
```

## MP4 CENC 解密算法

```python
from Crypto.Cipher import AES
from Crypto.Util import Counter

# 对每个 trak (视频 + 音频) 分别处理:
for each_sample in trak:
    iv = senc_ivs[sample_index]          # 8 字节 IV
    ctr = Counter.new(64, prefix=iv, initial_value=0)
    cipher = AES.new(key_16bytes, AES.MODE_CTR, counter=ctr)
    decrypted = cipher.decrypt(encrypted_sample)

# 修复元数据:
# encv → 原始编码 (frma 中的 hvc1/bvc2)
# enca → 原始编码 (frma 中的 mp4a)
# tenc.isProtected = 0, tenc.ivSize = 0
```

## 踩坑记录

1. **r0capture 无法抓到流量**: App 使用 `libttboringssl.so` 而非系统 `libssl.so`，需修改 `script.js` 让 `initializeGlobals()` 使用 `libname` 变量
2. **Frida Java bridge 不可用**: Frida 17.x 在 Android 9 上 `typeof Java === "undefined"`，降级到 16.5.9
3. **Module.findExportByName 不工作**: Frida 17.x 的 bug，改用 `ApiResolver("module")` 
4. **CDN 视频不是明文**: 虽然 `ftyp` 头有效，但 sample 仍然是 CENC 加密的
5. **av_aes_init 捕获时机**: `libttffmpeg.so` 是延迟加载的，必须先 spawn App，监控 `dlopen`，等库加载后再 hook
6. **解密只处理一个 track**: MP4 有视频+音频两个 trak，需要分别解密，否则播放器会因音频解析失败而卡住
7. **stco 是 chunk 偏移不是 sample 偏移**: 需要结合 stsc (sample-to-chunk) + stsz (sample size) 计算实际 sample 偏移
