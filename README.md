# AM40 (RK3399) 飞牛 fnOS 硬件编解码 / 2D 加速修复

让 **SMART-AM40（RK3399）** 上跑飞牛 fnOS 时，硬件编解码（MPP）和 2D 加速（RGA）真正可用的一组补丁与脚本。

> **English TL;DR** — Fixes for Rockchip MPP (video codec) and RGA (2D) on the SMART-AM40 / RK3399
> running fnOS. The vendor kernel module, the device tree and the vendor userspace library ship in
> mutually inconsistent flavours: the DTBs use upstream mainline `compatible` strings that no shipped
> driver matches, the MPP module was built without `CONFIG_CPU_RK3399`, and mediasrv eagerly requests
> hardware codecs RK3399 does not have (AV1 decode / HEVC encode). This repo contains the DTS changes,
> the kernel-module patches, a small `LD_PRELOAD` shim for mediasrv, and idempotent apply/verify
> scripts. No overclocking. See `docs/TECHNICAL-REPORT.md`.

---

## 修好之前 vs 修好之后

| | 修复前 | 修复后 |
|---|---|---|
| `mediasrv`（飞牛影视/相册的转码服务） | **一启动就 SEGV**，每 5 秒重启一次 | 正常运行 |
| H.264 解码 | 1.37x | **10.8x** |
| H.264 编码（1080p） | 0.66x | **1.22x** |
| H.265 8bit / 10bit 解码 | ❌ 不可用 | **9.09x / 7.18x** |
| H.265 4K 解码 | ❌ 不可用 | **2.52x** |
| VP9 / VP9-10bit 解码 | ❌ 不可用 | **1.26x / 3.66x** |
| 播 AV1 10bit + ASS 字幕（转码） | ❌ 报错，播不了 | **0.80x**（播放器选 720p 档 **1.07x 实时**） |
| dmesg 噪声（编 300 帧 + 解一遍） | 2588 行 / 287 条 WARN | **0 行** |

以上为 1080p 合成片实测，**未超频**（VPU 保持厂商默认 300 MHz 档）。

---

## 四个症状，四个根因

飞牛这套系统里，**设备树、内核模块、用户态库来自三个不同的上游**，它们在 RK3399 上互相不认识：

| 症状 | 根因 | 修在哪 |
|---|---|---|
| `mediasrv` 一启动就崩（`librga.so` 里 DABT） | DTB 的 RGA 节点写的是上游 mainline 的 `compatible = "rockchip,rk3399-rga"`，而飞牛只发了 vendor 版 `rga3.ko`，它只认 `rockchip,rga2`/`rockchip,rga3*` → 没有任何驱动认领该节点 → 模块 init 直接 `-EFAULT` → 没有 `/dev/rga` | **设备树**（1 个属性） |
| H.265/VP9/4K 只能软解 | `rk_vcodec.ko` 编译时没开 `CONFIG_CPU_RK3399`，RKV 解码器的匹配表被裁掉；同时 mainline 的 VPU 节点缺少 vendor MPP 需要的 `rockchip,srv`/`taskqueue-node`/`resetgroup-node` | **设备树 + 内核模块** |
| 编解码速度只有厂商原版的一半 | 模块每帧调用一次 6.18 已废弃的 `iommu_set_fault_handler()`，触发 `WARN_ON` 把整页调用栈（含 1.5 KB 模块清单）刷到串口 + HDMI 控制台 | **内核模块**（1 处 `#if 0`） |
| 播 AV1 报 `errno 4096`；某些片提示编码失败 | RK3399 **没有 AV1 硬解、没有 HEVC 硬编**，但 mediasrv 会无条件请求（浏览器说支持 HEVC，前端就发 `videoEncoder=hevc`） | **用户态垫片** |

详细的分层分析、证据链与代码见 **[`docs/TECHNICAL-REPORT.md`](docs/TECHNICAL-REPORT.md)**。

---

## 快速开始

### A. 用现成镜像（推荐给新装机的）

镜像是基于飞牛官方 `1.2.0302` 改的，**只改了 BOOT 分区**（多一个修正设备树 + 一个脚本 + 它的 payload），
rootfs 逐字节未动。

1. 从 **Releases** 下载 `fnos_1.2.0302_am40_SD-boot_v6.img`（卡启）或 `..._eMMC_v6.img`（eMMC 直刷）；
2. 按 `SHA256SUMS-v6.txt` 核对，然后 `dd` 到目标盘；
3. 开机后跑一次：

```sh
sudo bash /boot/am40-apply-fixes
```

> 只烘焙设备树这一项，就已经把「mediasrv 一启动就崩」修好了（那纯粹是 RGA 节点 compatible 的事）；
> 剩下的 MPP 模块 / 垫片 / 服务由上面这条命令补齐。**不跑脚本也是一个能用的降级形态。**

### B. 已经在跑的系统（包括系统更新之后）

```sh
# 把 scripts/ 里的脚本放到板子上（或用 Release 里的 payload），然后：
sudo bash am40-apply-fixes            # 应用/补齐全部修复（幂等，可反复跑）
sudo bash am40-apply-fixes --status    # 只体检，不改任何东西
sudo bash am40-apply-fixes --dry-run   # 只打印将要做什么
```

脚本会依次处理：**① 设备树 → ② 内核模块 → ③ mediasrv 垫片 → ④ 服务/权限 → ⑤ 自检**。
只有第 ① 步需要重启；每一步失败都会自动回滚，原始文件备份在 `/boot/am40-backup-<时间戳>/`。
**系统更新（换内核）之后要再跑一次** —— 更新会覆盖 `/usr/lib/modules/<ver>/updates/trim/rk_vcodec/`。

### C. 只想用其中某一项

```sh
sudo am40-check                       # 分层体检（只读）
sudo am40-fix-mediasrv --remove       # 单独摘掉 mediasrv 垫片
sudo am40-codec-matrix                # 跑一遍完整的硬编解能力矩阵（约 10 分钟）
sudo bash kernel-mpp/build.sh         # 只重编内核模块
```

---

## 仓库结构

```
.
├── docs/
│   ├── TECHNICAL-REPORT.md       ★ 分层技术报告（DTB / 内核模块 / 用户态 / 服务 / 运维 + 证据速查）
│   ├── USAGE.md                  刷机与应用、逐层校验方法、回滚清单
│   └── BOOT-NOTES.txt            会随镜像放进 BOOT 分区的简要说明
├── scripts/
│   ├── am40-apply-fixes          统一修复入口（幂等 + 自动回滚）
│   ├── am40-check                分层体检
│   ├── am40-fix-mediasrv         单独安装/卸载 mediasrv 垫片
│   ├── am40-codec-matrix         硬编解能力矩阵测试
│   └── patch-mpp-iommu.py        生成「去掉每帧 WARN」这个补丁
├── dts/
│   ├── am40-dtb.patch            ★ 相对 fnOS 出厂 DTB 的完整改动（154 行，可直接 review）
│   ├── rk3399-smart-am40-mpp-rga.dts    修正后的设备树源码（本仓库默认使用）
│   └── rk3399-smart-am40-mpp-full.dts   满编 MPP、无 RGA 修正（对照/回滚用）
├── kernel-mpp/
│   ├── mpp/                      ★ 可直接 out-of-tree 编译的 vendor MPP 源码（已含下述两个补丁）
│   ├── patches/                  相对 vendor 原版的补丁（可 review / 可移植到新内核）
│   ├── build.sh                  一条命令编译出 rk_vcodec.ko
│   └── README.md                 改动清单、内核版本约束、如何适配更新后的内核
├── mediasrv-shim/
│   ├── mediasrv-shim.c           LD_PRELOAD 垫片（三个钩子，约 250 行）
│   └── 10-am40-shim.conf         systemd drop-in
├── udev/99-am40-mpp.rules        /dev/mpp_service 权限
└── tools/
    ├── build-fixes-payload.sh    把上面这些打成 am40-fixes.tar.gz（交付 payload）
    ├── build-am40-images.py      维护者工具：从官方基础镜像生成交付镜像
    └── README.md
```

---

## 从源码构建

### 内核模块 `rk_vcodec.ko`

```sh
# 目标机（板子）上，或交叉编译时指定 ARCH/CROSS_COMPILE
sudo apt install -y build-essential linux-headers-$(uname -r)   # 镜像里已自带
bash kernel-mpp/build.sh
# 产物 kernel-mpp/build/rk_vcodec.ko，版本串应为 custom-rk3399-full-v3
```

> ⚠️ 模块与内核 **vermagic 强绑定**（`6.18.18.c951-trim` 与 `c1090-trim` 互不通用）。
> 换了内核必须重编，或使用 payload 里对应版本的预编译模块。
> 本仓库已知的内核：`6.18.18.c951-trim`（镜像自带）、`6.18.18.c1090-trim`（系统更新后）。
> 12 个移植细节（6.18 API 漂移）见 `kernel-mpp/README.md`，补丁在 `kernel-mpp/patches/`。

### 设备树

```sh
dtc -I dts -O dtb -o rk3399-smart-am40-mpp-rga.dtb dts/rk3399-smart-am40-mpp-rga.dts
sudo cp rk3399-smart-am40-mpp-rga.dtb /boot/dtb/rockchip/
sudo sed -i 's#^fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40-mpp-rga.dtb#' /boot/fnEnv.txt
sudo reboot
```

### mediasrv 垫片

```sh
gcc -shared -fPIC -O2 -o mediasrv-shim.so mediasrv-shim/mediasrv-shim.c -ldl -lpthread
# 或直接 sudo am40-fix-mediasrv   （会写 .so + drop-in + 重启服务）
```

---

## 已知限制

* **RK3399 没有 AV1 解码器**（`mpp: unable to create dec av1 for soc rk3399 unsupported`）。
  本仓库让 mediasrv 改用 `libdav1d` 软解；1080p 10bit AV1 软解约 1.6x，不是瓶颈。
* **RK3399 没有 HEVC/MJPEG 以外的编码器**（VEPU2 只支持 H.264 / MJPEG）。
  本仓库把做不到的 `*_rkmpp` 硬编请求**降级为 H.264 硬编**，所以浏览器请求 HEVC 时实际拿到 H.264 分片。
* 1080p 10bit AV1 + 烧 ASS 字幕的全链路约 **0.80x**；**播放器里选 720p 档是实时的（1.07x）**。
* 不要为了提速去拉高 `aclk_vcodec`：800 MHz 只能换回本来就该有的速度（且功耗 2.7 倍），
  **1000 MHz 以上会让编码器掉到 0.03x**。详见技术报告 §2.7。
* 只在 **SMART-AM40** 上验证过。其它 RK3399 板子：内核模块补丁与 mediasrv 垫片通用，
  但 DTB 需要换成各自板型的。
* 飞牛系统更新会覆盖内核模块；`am40-check` 会报出来，重跑 `am40-apply-fixes` 即可。

---

## 免责声明

* 本项目**与飞牛（fnOS）官方、Rockchip、Theobroma 均无关联**，属社区逆向修复。
* 刷机/替换内核模块有风险（尤其刷 eMMC）。**请先备份**，并对自己的数据负责。
* 仓库里的 `kernel-mpp/mpp/` 是 Rockchip vendor MPP 源码（GPL-2.0）加上本项目补丁后的一份快照，
  详见 [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。
* 本项目以 GPL-2.0 发布，**不提供任何担保**。

## 致谢

* **[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)**（`@deepseek-ai/dsh`）——
  本仓库的全部修复工作是在它的协助下完成的：从 `dmesg` / `journalctl` 的零散证据逐层收敛到
  「compatible 不匹配 / 缺 vendor 绑定要素 / 模块编译期裁剪 + API 漂移 / 硬件能力不存在但被请求」
  这四类失配；生成并回归验证内核模块补丁（含那个把吞吐砍半的每帧 WARN）；
  编写幂等修复脚本、用户态垫片与全部文档；并在真机上跑完能力矩阵与前后对比实测。
* **Rockchip / nyanmisaka** —— vendor MPP（内核驱动与用户态库）与本文档引用的 6.1 BSP 设备树绑定方式
* **飞牛 fnOS** —— 该系统本身与 `mediasrv`
* **Boris Brezillon / Collabora** —— 主线 `rkvdec` 驱动（本项目的对照参考）
* 以及所有把 AM40 这类小众板子跑起来的人

## 许可

[GPL-2.0](LICENSE)（与内核模块补丁、设备树保持一致）。第三方组件的归属见
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)。
