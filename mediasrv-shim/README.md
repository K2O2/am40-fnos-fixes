# 用户态层：mediasrv 兼容垫片（LD_PRELOAD）

## 为什么需要它

RK3399 有两个硬件能力缺口，而 mediasrv **不会优雅降级**——它会直接请求不存在的硬件编解码器并报错：

| 缺口 | 触发条件 | 日志 |
|---|---|---|
| 没有 **AV1 解码** | 播任何 AV1 片 | `mpp: unable to create dec av1 for soc rk3399 unsupported` → `[av1_rkmpp] Failed to init MPP context: -1` → `AVERROR_EXTERNAL` → `init_video_stream_map error, errcode 4096` |
| 没有 **HEVC 编码** | 浏览器支持 HEVC 时前端发 `"videoEncoder":"hevc"` | `mpp: unable to create enc h265 for soc rk3399 unsupported` |

第二个缺口**跟源文件是不是 AV1 无关**：任何需要转码的片（例如带 ASS 字幕要烧进去）在这个浏览器上都会撞到。

## mediasrv 是怎么挑解码器的（决定了补丁点）

```console
$ readelf --dyn-syms -W /usr/trim/bin/mediasrv | grep -oE 'av_codec_iterate|avcodec_find_encoder_by_name|avcodec_open2|avcodec_find_decoder(_by_name)?' | sort -u
av_codec_iterate
avcodec_find_decoder
avcodec_find_encoder_by_name
avcodec_open2
```

没有 `avcodec_find_decoder_by_name`，二进制里也没有 `av1_rkmpp` 字符串 ——
mediasrv 是**用 `av_codec_iterate()` 遍历全部解码器、按"名字带 rkmpp"挑硬件解码器**。
所以唯一的切入点是遍历阶段。

## 三个钩子

| 钩子 | 动作 |
|---|---|
| `av_codec_iterate()` | 跳过 `av1_rkmpp` / `vp8_rkmpp` → mediasrv 遍历时就看不到它们，改用 `libdav1d` / `libvpx` |
| `avcodec_find_encoder_by_name()` | 凡是 `*_rkmpp` 且不是 `h264_rkmpp`/`mjpeg_rkmpp` 的（`hevc_rkmpp`/`av1_rkmpp`/`vp9_rkmpp`…）改写成 `h264_rkmpp` |
| `avcodec_open2()` / `avcodec_find_decoder()` | 只记日志到 `/tmp/mediasrv-shim.log`（事后可审计真实调用链） |

**为什么不能在 `avcodec_open2()` 里偷换解码器**：ffmpeg 8 会拒绝——

```text
[av1_rkmpp @ 0x…] This AVCodecContext was allocated for av1_rkmpp, but libdav1d passed to avcodec_open2()
```

必须让 mediasrv 在 `avcodec_alloc_context3()` **之前**就选对，所以只能从 `av_codec_iterate()` 下手。

## 安装 / 卸载

```sh
# 完整安装（编 .so + 写 drop-in + 重启服务 + 验证）
sudo am40-fix-mediasrv
# 只看状态 / 卸载
sudo am40-fix-mediasrv --status
sudo am40-fix-mediasrv --remove
```

手工方式：

```sh
gcc -shared -fPIC -O2 -o /usr/local/lib/mediasrv-shim.so mediasrv-shim.c -ldl -lpthread
sudo install -D -m644 10-am40-shim.conf /etc/systemd/system/mediasrv.service.d/10-am40-shim.conf
sudo systemctl daemon-reload && sudo systemctl enable --now mediasrv
```

## 对 ffmpeg 版本不敏感

垫片按**符号名**拦截，而这些符号在 ffmpeg 5/6/7/8 里名字都不变；飞牛 `1.2.0302` 的旧
`mediasrv` 与 `1.2.0604` 的新版导入的都是同一组符号，因此两边都适用。

## 效果

真实片源（AV1 Main **10bit** 1920×1080 + Opus 5.1 + ASS 字幕）：

```text
修复前  {"result":"fail","errno":68157450}
修复后  {"result":"succ","playLink":"/media/<hash>/preset.m3u8","hlsTime":4,…}

垫片日志：
  find_encoder_by_name("hevc_rkmpp") -> 改用 "h264_rkmpp"
  iterate: 隐藏 vp8_rkmpp / av1_rkmpp
  find_decoder(id=225) -> libdav1d ; open2 OK codec=libdav1d
  open2 OK codec=libfdk_aac / opus / ssa / webvtt
  open2 OK codec=h264_rkmpp
分片 ffprobe: h264 High 1920x1080 + aac（字幕已烧入）
全链路 1080p 0.80x；播放器选 720p 档 1.07x（实时）
```

## 备选方案（不推荐）

`/usr/trim/etc/mediasrv.conf` 里把 `gpu.enable` 改成 `false` 也能绕过报错，但会把**本来可用的**
H.264/H.265 硬解一起关掉，CPU 扛不住 1080p 转码，所以本仓库采用按编解码器外科手术式的垫片方案。
