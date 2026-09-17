# 使用与运维

面向使用者与维护者。技术原理见 [`TECHNICAL-REPORT.md`](TECHNICAL-REPORT.md)。

---

## 0. 适用范围

| | |
|---|---|
| 硬件 | SMART-AM40（RK3399）。其它 RK3399 板子：模块补丁与 mediasrv 垫片通用，**DTB 需换成各自板型的** |
| 系统 | 飞牛 fnOS `1.2.0302`（内核 `6.18.18.c951-trim`）、更新后 `1.2.0604`（`6.18.18.c1090-trim`） |
| 前提 | 需要 root（`sudo`）。所有脚本都是幂等的，可反复执行 |

**先确认你在对的机器上**：

```sh
tr '\0' ' ' < /proc/device-tree/compatible; echo      # 应含 rockchip,rk3399
uname -r                                              # 6.18.18.c951-trim 或 c1090-trim
ls /boot/fnEnv.txt /boot/dtb/rockchip/                # BOOT 分区在
```

---

## 1. 三条使用路径

### 1.1 用现成镜像（新装机）

```sh
# 1) 下载并核对（Release 资产）
sha256sum -c SHA256SUMS-v6.txt

# 2) 刷到 SD 卡（把 sdX 换成你的卡！）
sudo dd if=fnos_1.2.0302_am40_SD-boot_v6.img of=/dev/sdX bs=4M conv=fsync status=progress

# 3) 插卡开机，然后跑一次
sudo bash /boot/am40-apply-fixes
```

`eMMC 直刷` 用 `..._eMMC_v6.img` + `dd ... of=/dev/mmcblk0`；
或用瑞芯微工具包 `am40-eMMC-flash_v6/`（Loader = 包里的 `MiniLoaderAll.bin`）。

> 刷 eMMC 前请确认目标盘是 eMMC 而不是你的系统盘 —— 这个操作不可逆。

### 1.2 已经在跑的系统（含系统更新之后）

把 `scripts/` 里的脚本和 payload 放到板子上（例如 `/boot/`），然后：

```sh
sudo bash am40-apply-fixes              # 应用/补齐全部修复
sudo bash am40-apply-fixes --status      # 只体检，不改任何东西
sudo bash am40-apply-fixes --dry-run     # 只打印将要做什么
```

脚本会：① 设备树 → ② 内核模块 → ③ mediasrv 垫片 → ④ 服务/权限 → ⑤ 自检。
**只有第 ① 步需要重启**；每步失败自动回滚，原始文件备份在 `/boot/am40-backup-<时间戳>/`。

### 1.3 只用其中一项

```sh
sudo am40-check                     # 分层体检（只读）
sudo am40-fix-mediasrv              # 只装/补 mediasrv 垫片（--remove 卸载）
sudo am40-codec-matrix              # 跑完整能力矩阵（约 10 分钟，结果 /tmp/codec_matrix.log）
bash kernel-mpp/build.sh            # 只重编内核模块
```

---

## 2. 逐层校验

每一层都能独立确认。**任何一层没生效，上层都不会好**，所以按顺序查。

### 2.1 设备树层

```sh
# a) fnEnv 指对了没
grep fdtfile /boot/fnEnv.txt
#   fdtfile=rockchip/rk3399-smart-am40-mpp-rga.dtb

# b) DTB 里 RGA 节点的 compatible
fdtget -t s /boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb /rga@ff680000 compatible
#   rockchip,rga2           ← 必须是这个（不是 rockchip,rk3399-rga）

# c) 运行中的设备树 + 设备节点
tr '\0' ' ' < /proc/device-tree/rga@ff680000/compatible; echo
ls -l /dev/rga /dev/mpp_service
#   crw-rw---- root video  /dev/rga  和  /dev/mpp_service
```

### 2.2 内核模块层

```sh
# a) 四个驱动是否都绑定
for d in mpp_vepu2 mpp_vdpu2 mpp_rkvdec rga2; do
  printf '%-12s ' $d; ls /sys/bus/platform/drivers/$d/ 2>/dev/null | grep -vE '^(module|bind|unbind|uevent)$'
done
#   期望：ff650000.vepu / ff650400.vdpu / ff660000.rkvdec / ff680000.rga

# b) 模块版本（关键：区分有没有那个性能补丁）
cat /proc/mpp_service/version
#   custom-rk3399-full-v3    ← v2 及更早没有「每帧 WARN」修复，会慢一倍

# c) 有没有每帧 WARN（旧模块的特征）
dmesg | grep -c iommu_set_fault_handler
#   0    ← 必须是 0（几十以上说明是旧模块）

# d) 能力表
cat /proc/mpp_service/supports-device
#   应含 DEVICE[ 1]:VDPU2 / DEVICE[18]:VEPU2 / DEVICE[9]:RKVDEC HW_ID:0x68761f00
```

### 2.3 用户态垫片层

```sh
systemctl is-active mediasrv                # active
p=$(pgrep -x mediasrv | head -1)
tr '\0' '\n' < /proc/$p/environ | grep LD_PRELOAD
#   LD_PRELOAD=/usr/local/lib/mediasrv-shim.so
cat /tmp/mediasrv-shim.log | tail            # 有 find_decoder/open2 记录

# 端到端：拿一个 AV1 片要一个播放链接（把路径换成你自己的）
python3 - <<'PY' > /tmp/req.json
import json
print(json.dumps({"req":"media.getPlayLink","reqid":"t","file":"/vol4/xxx.mkv",
 "startTimestamp":0,"videoIndex":0,"videoEncoder":"hevc","audioIndex":1,
 "audioEncoder":"aac","channels":2,"subtitleIndex":-1,
 "quality":{"resolution":"720","bitrate":1500000}}, ensure_ascii=False))
PY
curl -s -X POST -H 'Content-Type: application/json' --data-binary @/tmp/req.json \
     --unix-socket /var/run/mediasrv.socket http://localhost/api/v1/media
#   {"result":"succ","playLink":"/media/<hash>/preset.m3u8",...}   ← 成功
```

### 2.4 服务与权限层

```sh
systemctl is-enabled mediasrv               # enabled
stat -c '%a %U:%G' /dev/mpp_service         # 660 root:video
systemctl show mediasrv -p NRestarts --value  # 0（不是反复重启）
```

### 2.5 一条命令全查

```sh
sudo am40-check
# 期望结尾：=================== 结果: OK=28 FAIL=0 ===================
```

---

## 3. 性能验证

```sh
sudo am40-codec-matrix          # 约 10 分钟；结果在 /tmp/codec_matrix.log
grep '^RESULT|' /tmp/codec_matrix.log | awk -F'|' '{printf "%-16s %-14s %-8s %4s %s\n",$2,$3,$4,$5,$6}'
```

对照基线（1080p 合成片，`speed=` 来自 ffmpeg）：

| 项目 | 应当看到 |
|---|---|
| H.264 解码 / 编码 | ≥ 8x / ≥ 1.1x |
| H.265 8bit / 10bit 解码 | ≥ 7x |
| H.265 4K 解码 | ≥ 2x |
| VP9 / VP9-10bit 解码 | ≥ 1x / ≥ 3x |
| HEVC 编码 | FAIL（RK3399 没有 HEVC 硬编，属正常） |
| VP8 / AV1 解码（MPP） | FAIL（无硬件，正常；走软解） |

顺手确认**没有**在烧 CPU 打印：

```sh
dmesg -c >/dev/null
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=1920x1080:rate=30 \
       -frames:v 300 -c:v h264_rkmpp -b:v 4M -y /tmp/t.mkv
dmesg | wc -l      # 期望 0
```

---

## 4. 回滚

### 4.1 逐层回滚

```sh
# ① 设备树：换回出厂 DTB
sudo sed -i 's#^fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40.dtb#' /boot/fnEnv.txt
sudo reboot

# ② 内核模块：三档可选
sudo systemctl stop mediasrv
sudo cp /boot/rk_vcodec.ko.rk3399-full-v2 /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko   # 满编但慢一倍
#   或飞牛原版（只剩 H.264 硬解/硬编）：
# sudo cp /boot/rk_vcodec.ko.orig /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo rmmod rk_vcodec && sudo insmod /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo systemctl start mediasrv

# ③ 垫片
sudo am40-fix-mediasrv --remove
```

### 4.2 用脚本自己的备份

```sh
ls /boot/am40-backup-*/          # 每次 apply 的原始文件都在这里
```

---

## 5. 系统更新之后

飞牛的更新会换内核、并覆盖 `/usr/lib/modules/<ver>/updates/trim/rk_vcodec/`（板上实测发生过）。
另外更新流程带 `10-sync-dtb` 钩子，会碰 `/boot/dtb`。

```sh
sudo am40-check                     # 先看缺什么（会明确报“模块版本不对”/“fdtfile 不是修正版”）
sudo bash am40-apply-fixes          # 一条命令补齐
```

若更新带来的内核是新版本（例如将来 6.19），payload 里没有对应预编译模块时，
脚本会用 payload 里的源码**现场编译**（镜像自带 `aarch64-linux-gnu-gcc-12` + 对应的
`/usr/src/linux-headers-<ver>`）。编不过就是又遇到 API 漂移了，照
`kernel-mpp/patches/0001` 的思路补。

---

## 6. 故障排查

| 症状 | 先查 | 结论 / 处置 |
|---|---|---|
| `mediasrv` 反复重启（`NRestarts` 增长） | `ls -l /dev/rga`；`journalctl -u mediasrv \| tail` | 缺 `/dev/rga` → 设备树没生效（§2.1），跑 apply-fixes 后重启 |
| 日志里 `librga.so.2.1.0` + `DABT ... translation fault` | 同上 | 同上（这是缺 `/dev/rga` 的上游后果） |
| H.265/VP9/4K 只能软解，`supports-device` 里没有 `RKVDEC` | `cat /proc/mpp_service/version`；§2.2 | 模块不是满编版 → apply-fixes 装 `custom-rk3399-full-v3` |
| 编解码速度只有一半；`dmesg \| grep -c iommu_set_fault_handler` 很大 | §2.2 | 模块是 v2 或更早 → 装 v3 |
| 播 AV1 报 `errno 4096` / `init_video_stream_map error` | §2.3 的 `LD_PRELOAD` | 垫片没加载 → `am40-fix-mediasrv` |
| 浏览器里提示编码失败（源是 H.265 或 AV1） | 垫片日志里有没有 `find_encoder_by_name("hevc_rkmpp") -> 改用 "h264_rkmpp"` | 没有说明垫片没生效 |
| `/dev/mpp_service` 打不开（Permission denied） | `stat -c '%a %U:%G' /dev/mpp_service` | 需 `660 root:video`；udev 规则见 `udev/99-am40-mpp.rules` |
| 改了 DTB 后起不来 | — | 从好盘/镜像恢复；`fnEnv.txt` 在 BOOT 分区，Windows 下也能改回 `rk3399-smart-am40.dtb` |

---

## 7. 维护者：发布资产怎么打

```sh
# (1) payload（放 Release，也给镜像用）
bash kernel-mpp/build.sh                                     # 目标内核上编模块
tools/build-fixes-payload.sh --ko "$(uname -r)=build/rk_vcodec.ko" --out am40-fixes.tar.gz

# (2) 校验值清单
sha256sum fnos_1.2.0302_am40_SD-boot_v6.img fnos_1.2.0302_am40_eMMC_v6.img \
          am40-eMMC-flash_v6/update.img am40-fixes.tar.gz > SHA256SUMS-v6.txt

# (3) 镜像（见 tools/README.md「镜像怎么打」）
python3 tools/build-am40-images.py
```

发布前建议核对（每条都能独立跑）：

```sh
# p1 文件系统干净
e2fsck -fn p1-v6.img
# 镜像里的 BOOT 分区内容 == 源文件
dd if=<img> bs=512 skip=65536 count=737280 status=none | md5sum      # == p1-v6.img 的 md5
# 启动链没被动过（左右都应等于出厂链）
dd if=<img> bs=512 skip=64 count=32704 status=none | md5sum
# rootfs 与基础镜像逐字节一致
dd if=<img> bs=512 skip=835584 count=<p2 扇区数> status=none | sha256sum
```
