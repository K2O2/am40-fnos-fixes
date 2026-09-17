# AM40 专用飞牛 SD 启动镜像 —— 交付与刷机说明

> 生成日期 2026-09-16。基于 `rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288.img.gz`
> 解压后的原始镜像修改而成。

> ## 🚀 v6 交付版（2026-09-17）—— 拿到新镜像先看这段
>
> **交付物**：`rom/fnos_1.2.0302_am40_SD-boot_v6.img`（卡启）、
> `rom/fnos_1.2.0302_am40_eMMC_v6.img`（eMMC 直刷）、`rom/am40-eMMC-flash_v6/`（瑞芯微工具包）。
>
> **刷完机只需要一条命令**：
> ```sh
> sudo bash /boot/am40-apply-fixes
> ```
> 它会按顺序把 4 件事补齐：满编 MPP 模块（含「每帧 WARN」性能修复）、mediasrv 兼容垫片
> （AV1 软解 / HEVC 编码回退）、服务与权限、文档；幂等可反复跑，失败自动回滚，
> 系统更新之后**再跑一次**即可。只体检用 `sudo am40-check`，只看不改用 `--status`。
>
> **镜像本身只比 v5 多了一个修正设备树 + 这个脚本和它的 payload，rootfs 逐字节未改**。
> 设计理由、payload 清单、刷完机状态对照、回滚办法见 **§1.11**。

> **此前状态（09-16 21:00，实机复查）**：板子已恢复并跑在飞牛 **1.2.0604**
> （内核 `6.18.18.c1090-trim`），**从 SD 卡自启动**（启动链 `0x8000~0x1000000`
> md5 `53e488ce…` 校验通过），MPP 硬编/硬解实测正常。
> 系统更新后的自检方法、以及"系统放 SD 还是放 eMMC"的实测取舍见 **§1.10**。

---

## 0. ✅ 实机验证通过（2026-09-16 晚）

v3 镜像在 AM40 上**从 SD 卡启动成功**，运行状态实测：

| 项 | 实测 |
|---|---|
| 内核 | `6.18.18.c951-trim`（镜像自带，非现役的 c1090） |
| 板型 | `Theobroma am40`，加载的是 **stock 原版 DTB**（`gpu@ff9a0000` 下无 `clock-names`，已核实） |
| 网络 | `eth0` UP，`<board-ip>/24`，**1000 Mbps**；`net.ifnames=0` 生效 |
| rootfs | `/dev/mmcblk1p2` btrfs，3.2 G → **14 G 自动扩容成功**（`resize-rootfs.service` 正常） |
| 飞牛 | 1.2.0302；Web UI 80/443、SMB 139/445、WSD 3702、API 5666/5667 全部在线 |
| GPU 内核侧 | `midgard_kbase` 已加载，`/dev/mali0` 存在 |
| MPP | `rk_vcodec` 已加载（refcnt 0），**`/dev/mpp_service` 不存在** ← 符合预期，见 §5 |
| eMMC 旧系统 | 未被改动，但被飞牛自动挂成 `/vol00/RemovableDisk{,_1}`，而且是 **rw** |

### ⭐ MPP 硬件编解码：已验证可用（本次最大的收获）

把 DTB 切到 `rk3399-smart-am40-mpp.dtb` 后 —— **没有换内核、没有换系统、没有重编任何东西**：

```
mpp_service mpp-srv: probe success
mpp_vdpu2 ff650400.vdpu: probing finish     ← H.264/VP8 硬解
mpp_vepu2 ff650000.vepu: probing finish     ← H.264 硬编
/dev/mpp_service 出现
```

功能实测（`/usr/bin/ffmpeg`，其 `-hwaccels` 里本来就有 `rkmpp`）：

| 测试 | 结果 |
|---|---|
| `-c:v h264_rkmpp` 编码 720p×90 | ✅ 90 帧，CBR 3.8 Mbps，输出 1 411 604 B |
| `-c:v h264_rkmpp` 编码 1080p×60 | ✅ 输出 1 422 578 B |
| `-c:v h264_rkmpp` 解码 720p / 1080p | ✅ 60/90 帧全部解出，speed 6~7× |

`/proc/mpp_service/supports-device` = `VDPU2` / `VDPU2_PP` / `VEPU2`
→ **H.264 硬解 + H.264 硬编**都拿到了；HEVC/4K 解码仍走 mainline 的 V4L2 rkvdec（`/dev/video0`）。

> 这直接**推翻了上一轮"为了 MPP 必须换 BSP 6.1 内核"的结论**：
> fnOS 的 `rk_vcodec.ko` 就是 MPP 服务驱动（out-of-tree），一直加载着，
> 缺的只是 DTB 里的 `mpp_srv` + `rockchip,srv` + `rockchip,taskqueue-node`。

**两个注意事项：**

1. **权限**：`/dev/mpp_service` 默认是 `0600 root`，非 root 进程打不开。
   本机已加 `/etc/udev/rules.d/99-am40-mpp.rules`（`mpp_service`/`rga`/`mali0`/`renderD*`
   → `video` 组 0660）并把当前用户加进 `video` 组。
   **重刷镜像后要再做一次**（规则文件已放进 BOOT 分区 `99-am40-mpp.rules`）：
   ```sh
   sudo cp /boot/99-am40-mpp.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules && sudo udevadm trigger --action=change
   sudo usermod -aG video $USER
   ```
2. **飞牛自带的 `mediasrv` 会崩 —— 已定位到根因（2026-09-16 实测）**：
   它启动时探测硬件编解码能力，会去 MPP 要 **HEVC 那一路**；而我们的 mpp DTB 只让 MPP
   绑到 H.264 两路 → 用户态库报 `mpp_platform: client 9 driver is not ready!`，
   **mediasrv 不检查返回值、拿空句柄继续走 → SIGSEGV**，被 systemd 每 5 秒重启（实测 500+ 次）。
   证据链：
   - `journalctl -u mediasrv`：`Started` → `mpp[...] client 9 driver is not ready!` → `status=11/SEGV`；
   - `/sys/bus/platform/drivers/mpp_*` 只有两路有设备：
     `mpp_vdpu2 → ff650400.vdpu`（`rockchip,vpu-decoder-v2`，H.264/VP8 解）、
     `mpp_vepu2 → ff650000.vepu`（`rockchip,vpu-encoder-v2`，H.264 编）；
     其余 11 个（`mpp_rkvdec`/`rkvenc`/`jpgdec`/`av1dec`/`vdpp`/`iep2`/`vdpu1`/`vepu1`…）bound 全为空；
   - 用 fnOS 自带 ffmpeg 复现同一行：`-c:v hevc_rkmpp` → `client 9 driver is not ready!`
     + `mpp: unable to create enc h265 for soc rk3399 unsupported`；同一时刻 `h264_rkmpp` 编码成功；
   - 原因：RK3399 的 HEVC/VP9/4K 解码器（`ff660000.video-codec`）在本内核里由 **mainline V4L2**
     驱动接管（`/dev/video0`，名字就叫 `rkvdec`），**不是** 瑞芯微 MPP 的客户端；
   - 且 RK3399 **没有 HEVC 硬编**（`soc rk3399 unsupported`）→ 补 DTB 也不保证它满意。
   三种处理：
   - **止血（推荐）**：`sudo systemctl mask mediasrv` —— 放弃飞牛自带转码/缩略图；
     Jellyfin 用自带的 ffmpeg 走 `/dev/mpp_service`，**不受影响**；
   - 想保住 mediasrv：`fnEnv.txt` 的 `fdtfile` 改回 `rk3399-smart-am40.dtb`（走 V4L2/软解，放弃 MPP）；
   - 想两者都要：给 DTB 补 vendor 版 `rkvdec` MPP 节点（会顶掉 mainline `/dev/video0`，属实验）。

   > HEVC 硬解即使没有 MPP 也有路：fnOS 自带 ffmpeg 有 `hevc_v4l2m2m`（走 `/dev/video0` 的
   > mainline rkvdec）。Jellyfin 里可以把 HEVC 解码指定成 `hevc_v4l2m2m`，H.264 用 `h264_rkmpp`。

3. **实验记录：给 DTB 补 vendor `rkvdec` MPP 节点 —— 失败，已回退，别再用（2026-09-16 21:45）**

   目的：让 MPP 拿到 HEVC 那一客户端（client 9），看能否既不崩 mediasrv、又让 HEVC 走 MPP。
   做法：把 `/video-codec@ff660000`（mainline `rockchip_vdec`）换成 vendor 风格节点
   （`compatible = "rockchip,rkv-decoder-rk3399", "rockchip,rkv-decoder-v2",
   "rockchip,rkv-decoder", "rockchip,rk3399-vdec"`），生成 `rk3399-smart-am40-mpp-hevc.dtb`
   （文件留在 `/boot/dtb/rockchip/` 备查）。

   结果（实测）：
   - 节点确实绑上了，但绑到的是 **`mpp_rkvdec2`**（RK3568/3588 那代的驱动），不是 `mpp_rkvdec`
     —— `rockchip,rkv-decoder-rk3399` 没匹配上，是第二顺位 `rockchip,rkv-decoder-v2` 生效的；
   - **MPP 解 HEVC：0 帧、卡 30 秒**（寄存器模型不对），并且**把整块板子卡死了**
     （ping 不通，只能断电重启）；
   - **mediasrv 仍然 SIGSEGV**（`rc=139`）→ 它要的不只是那一个客户端，MPP 初始化路径照样踩空；
   - 副作用：mainline 的 `/dev/video0`（`hevc_v4l2m2m` 用的 rkvdec）消失；
   - 启动日志新增 `rockchip-pm-domain … Timed out. Forcing sync_state()`。

   **结论：这条路是死的。** 用回 `rk3399-smart-am40-mpp.dtb`：H.264 走 MPP（`h264_rkmpp`
   编+解，已验证），HEVC/VP9/4K 走 mainline V4L2（`hevc_v4l2m2m`），mediasrv 用
   `sudo systemctl mask mediasrv` 关掉。`sudo am40-check` 已加了对这个坏 DTB 的告警。

### 根因确认：v1 挂在 SD 卡上那套飞牛自带的 U-Boot 上

v1 把整张官方镜像（含 `idbloader`/`uboot`/`trust`）写进了 SD；那套 U-Boot 的控制设备树是
Firefly 的（`firefly,firefly-rk3399`），在 AM40 的板级初始化阶段就挂了 —— 现象正是
**"网口灯完全不亮、什么都没起来"**。v3 把 SD 上那 32 MB 裸区清零、只留 GPT + 两个分区之后，
板子改用 eMMC 上那套已验证的 Armbian U-Boot，问题消失。

> **结论：这个官方 ARM 镜像的内核 / rootfs / DTB 在 AM40 上完全可用；
> 唯一不能直接拿来用的是它自带的 U-Boot（Firefly 板级配置）。**

### ⚠️ 三件要注意的事

1. **eMMC 上那套旧系统被 rw 挂载在 `/vol00/RemovableDisk{,_1}`** —— 它是你的回滚盘。
   建议在飞牛 UI 里把这两个"移动磁盘"卸载/弹出，别往上写东西，更不要格式化。
2. **`/vol1`–`/vol4` 目前都没挂载**：
   - `/vol1` 原本就在被覆盖的那张 SD 上，已随镜像写入消失；
   - `/vol3`/`/vol4` 在 USB 硬盘上（换卡时被拔掉了，插回去重启即可）；
   - NVMe 那个阵列（`md127`，即原 `/vol2`，VG `trim_729696fc…`）组装起来了，
     但 LVM 卷没自动挂载 —— 可能要在飞牛 UI 里手动"挂载/导入"。
3. **两个失败单元来自板级差异**：`led-set.service`、`set_gpio-init.service`
   （镜像按 Firefly 的 GPIO 布局初始化，AM40 对不上）。无害但要留意。
   另外 `exim4` / `nut-monitor` / `systemd-modules-load` 是老系统上本来就失败的历史项。

---

## 1. 交付物

| 项 | 值 |
|---|---|
| **卡启版**（SD 卡用） | `rom/fnos_1.2.0302_am40_SD-boot.img`<br>3 847 225 344 字节 · sha256 `9038244f6e845694708763b2a89ef71e7b6360240ea18861bd536b7e801bab0a` |
| **eMMC 整盘版** | `rom/fnos_1.2.0302_am40_eMMC.img`<br>3 965 714 432 字节 · sha256 `dcef3d3f48568cae3b0cfba097a2e4e6b82761d0274eddb445c53844d4dc358d` |
| **eMMC 直刷包**（瑞芯微工具） | `rom/am40-eMMC-flash/`（loader + parameter + 分区文件 + `update.img`）<br>`update.img` 3 797 684 696 字节 · sha256 `e42a921d70f79112aa00e9dffa3df18c694cf08cf68883e1d67342834f584481` |
| 以上三份的同一内核（v5） | `rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40.img`<br>3 847 225 344 字节 · sha256 `9038244f6e845694708763b2a89ef71e7b6360240ea18861bd536b7e801bab0a` |
| v1（网卡故障版，留档） | `rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40_v1.img`<br>sha256 `997412aa678009160257925e726d74baa38013405af9923d607ca5575988ad61` |
| 原始未改镜像（对照） | `rom/fnos.img`（sha256 见下） |
| 原始未解压 | `rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288.img.gz` |

> 三份交付物的**系统和启动链完全一样**，区别只在"怎么落到盘上"和 GPT 分区位置，
> 详见 §1.9。


刷完后可以这样核对：

```sh
# Linux / WSL
sudo dd if=/dev/sdX bs=1M count=3666 2>/dev/null | sha256sum     # 前 3.58 GiB
# 或直接比对整盘（卡更大时会多出零填充，用上面那条按长度截断的方法）
```

---

## 1.5 ⚠️ v1 故障记录 → v2 修正（2026-09-16）

**v1（sha256 `997412aa…`，已改名 `..._am40_v1.img`）在 AM40 上启动后"网卡没反应"。**
根因尚未确认（等串口日志）。v2 做了三处**保守化**调整：

| # | v1 | v2 |
|---|---|---|
| 1 | 默认 DTB = 加过 `clock-names="clk_mali"` 的 GPU 修复版 | **默认 DTB = 与 AM40 现役逐字节相同的原版**（md5 `3373c03f…`）；GPU 修复版改名 `rk3399-smart-am40-gpu.dtb` 变成可选项 |
| 2 | 没有 `net.ifnames=0` | `extraboardargs=net.ifnames=0 max_loop=128`，与 AM40 现役 `armbianEnv.txt` 一致 |
| 3 | `verbosity=1`（串口几乎看不到内核日志） | `verbosity=7`（串口打印全部内核日志，便于定位） |

理由：

1. `clock-names` 是我唯一没有实机验证过的 DTB 改动，而交接日志 §11.1 本来就结论
   **"这条不需要做"**；所依据的 `(飞牛修复GPU).dtb` 本身还带断链 bug，可信度存疑。
   先把它从默认路径上摘掉。
2. AM40 现役 cmdline 里有 `net.ifnames=0`。实测 `udevadm info /sys/class/net/eth0`
   显示 `ID_NET_NAME_ONBOARD=end0`、`ID_NET_NAME=eth0` —— 说明**不加这一项，网卡不会叫
   `eth0`**（会叫 `end0`）。镜像原本没有这一项。

### 不用重新刷卡也能试（在 v1 卡上就地改）

把 v1 的 SD 卡插到电脑上，编辑 BOOT 分区根目录的 `fnEnv.txt`，改成：

```
verbosity=7
bootlogo=false
console=both
extraargs=cma=256M
extraboardargs=net.ifnames=0 max_loop=128
fdtfile=rockchip/rk3399-smart-am40-stock.dtb
kernelfile=vmlinuz-6.18.18.c951-trim
```

（v1 卡上"原版 DTB"的文件名是 `rk3399-smart-am40-stock.dtb`。）

---

## 1.6 v3：清零 SD 启动链 + 内置诊断包（**当前交付版**）

第一次实机结果：**网口灯完全不亮、ping 不通**。灯不亮说明 **PHY 根本没起来**，
这不是 IP/命名层面的问题，而是"板子没启动"或"GMAC 没绑上"。

**v1 的一个重大疏漏**：我把整张官方镜像（含 `idbloader` / `uboot` / `trust`）写进了 SD。
那套 U-Boot 是给 **Firefly-RK3399** 编的（控制设备树是 `firefly,firefly-rk3399`）。
如果 RK3399 的 BootROM 优先从 SD 启动，跑起来的就是这套**从没在 AM40 上验证过**的
U-Boot，很可能在板级初始化阶段就挂了 —— 现象正好是"灯不亮、什么都没起来"。

v3 的交付文件：

```
rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40.img   (v3)
  sha256 15b4df144253d0d6762746992692cebe75630505c7ca9c9c5651b980002f9a6b
  大小   3847225344 字节
```

改动：

| # | 内容 |
|---|---|
| 1 | **SD 上 0x8000 ~ 0x2000000（32 MB 裸区，即 idbloader/uboot/trust）全部清零**，只保留 GPT + 两个分区。板子只能用 eMMC 上那套已验证能启动的 Armbian U-Boot；它的 `boot_targets=usb0 mmc1 mmc0 …` 里 `mmc1` 就是 SD，会优先从 SD 引导本镜像。<br>**这同时是二分法**：v3 能起来 ⇒ v1 挂在飞牛自带 U-Boot 上；v3 还是灯不亮 ⇒ 问题在内核/rootfs/DTB。 |
| 2 | 默认 DTB = `rk3399-smart-am40.dtb` = 与 AM40 现役**逐字节相同**的原版（md5 `3373c03f…`） |
| 3 | `extraboardargs=net.ifnames=0 max_loop=128`（与现役 `armbianEnv.txt` 一致） |
| 4 | `verbosity=7`（串口/显示器能看到完整内核日志） |
| 5 | BOOT 分区新增 **`am40-diag.cpio.gz`**（987 KB 诊断 initramfs，默认不启用） |

### 诊断 initramfs 怎么用（不用串口、不用显示器）

它自带静态 busybox，启动后会：等 12 秒让存储就绪 → 把 `dmesg`、`/sys/class/net`、
`ip link`、块设备列表、`/proc/cmdline`、以太网 DT 节点、`/proc/mounts` 写到
**BOOT 分区根目录的 `AM40-DIAG.txt`** → 同步 → 自动关机。

用法（v3 卡插到电脑上改一行即可，不用重刷）：

```
# 编辑 BOOT 分区根目录 fnEnv.txt，加最后一行
initrdfile=am40-diag.cpio.gz
```

插卡上机开机，等约 30 秒它会自己关机；拔卡插电脑看 `AM40-DIAG.txt`。

**判读**：

| 现象 | 结论 |
|---|---|
| 生成了 `AM40-DIAG.txt`，里面有 `eth0` / `Link is Up` | 内核和驱动都没问题 → 问题在启动器或 rootfs 的网络服务 |
| 生成了 `AM40-DIAG.txt`，但 dmesg 里没有 `rk_gmac-dwmac` 或没有 PHY attach | 内核起来了但 GMAC 没绑 → DTB / 驱动 / 供电问题，日志里能直接看到原因 |
| **完全没生成文件** | 内核根本没跑起来 → 问题在启动器/内核加载阶段，与驱动无关 |

取证完把 `initrdfile` 那行删掉即可恢复正常启动。

---

## 1.7 v4：默认切到 MPP

```
rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40.img   (v4)
  sha256 c4d794bdaacad9a6fb5ea8bcbbe6fa0ab975a5b5529dae1d2805080f7ca0aa12
  大小   3847225344 字节
```

在 v3 基础上：

| # | 内容 |
|---|---|
| 1 | 默认 `fdtfile` 改为 `rk3399-smart-am40-mpp.dtb`（MPP 已实测可用，见 §0） |
| 2 | BOOT 分区新增 `99-am40-mpp.rules` —— 重刷后 `cp` 一次，给非 root 进程开放 `/dev/mpp_service` |
| 3 | `AM40-NOTES.txt` 重写，含 MPP 结果、`mediasrv` 副作用、DTB 三选一、两种看日志的办法 |

其余与 v3 相同：SD 启动链已清零（用 eMMC 上已验证的 Armbian U-Boot）、
`net.ifnames=0 max_loop=128`、`verbosity=7`、stock/gpu 两份备选 DTB、诊断 initramfs。

> **不想用 MPP 就这么退**：编辑 BOOT 分区 `fnEnv.txt`，把 `fdtfile` 改回
> `rockchip/rk3399-smart-am40.dtb`（或 `-gpu.dtb`），重启即可。

---

## 1.8 v5：镜像自带 AM40 可用的启动链（历史版本，v6 的基础）

```
rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40.img   (v5)
  sha256 9038244f6e845694708763b2a89ef71e7b6360240ea18861bd536b7e801bab0a
  大小   3847225344 字节
```

v3/v4 都依赖 **eMMC 上那套 U-Boot** 才能启动（SD 的启动链当时被我清零了）。
v5 把 **AM40 上已验证可用的 Armbian U-Boot**（从本机 eMMC 的 `0~16MB` 裸区提取，
md5 `43d1cb8be212412dc8b427ab254307ee`，备份在 `_work/am40-backup/emmc-boot-0-16M.img`）
注入镜像的 `0x8000~0x1000000`，替换掉飞牛自带的 Firefly U-Boot。

现在这份镜像是**自给自足**的：

| 刷到哪 | 结果 |
|---|---|
| SD 卡 | SD 自己就能启动，不再依赖 eMMC |
| eMMC | eMMC 自己就能启动，不再需要 SD |

已校验：`0x8000~0x1000000` 与 eMMC 启动链**逐字节一致**；`0x1000000~0x2000000` 全零；
GPT / 保护 MBR / p1(ext4) / p2(btrfs) 全部完好；与原版镜像的差异仅落在
启动链裸区和 BOOT 分区内容上。

### 刷进 eMMC（不需要 maskrom）

```sh
sudo dd if=<本镜像> of=/dev/mmcblk0 bs=4M conv=fsync status=progress
```

⚠️ **整盘刷 eMMC 会连它的 GPT 一起重写** —— 如果 eMMC 现在放着飞牛存储空间
（本机现状：`/vol1`，md0 RAID1 + LVM），先在飞牛 UI 里**删掉那个存储空间**再刷，
否则 LVM/md 元数据残留，飞牛下次可能把这块盘认成"损坏的旧阵列"。

⚠️ **不要用"只补启动链"的窄写把 eMMC 变成可启动盘**：AM40 的启动链要占
`0x8000~0x1000000`（≈16 MB），而飞牛建存储空间后 eMMC 的 p1 从 **LBA 2048 = 1 MB** 就开始，
`1 MB ~ 16 MB` 这段已经被根分区/md 元数据占用 —— 往里写链会**直接打坏存储空间**。
所以 eMMC 一旦当过存储盘，就**只能整盘重刷**才能再当启动盘。

⚠️ **刷完必须把 SD 拔掉或清掉**：两份镜像的文件系统 UUID 与 GPT PARTUUID 完全相同，
同时插着会撞车（`root=PARTUUID=…` 可能挂到另一块盘上）。这两块盘不是两个不同的系统，
是同一份镜像的两个副本，不能当"互为备份"。
⚠️ eMMC 上原来的旧飞牛会被覆盖。首次启动 `resize-rootfs` 会把 rootfs 扩到 eMMC 的 99%（≈29 GB）。

### 两条互斥的路线

| 路线 | 做法 | eMMC 的命运 |
|---|---|---|
| **A. SD 当系统盘** | 保持现状（或把 v5 刷 SD），在飞牛 UI 里把 eMMC 格式化成存储空间 | 变成存储空间 |
| **B. eMMC 当系统盘** | 把 v5 `dd` 进 `/dev/mmcblk0`，然后**拔掉 SD** | 变成系统盘 |

选 A：eMMC 随便格式化都不影响开机（因为 v5 起 SD 已自带启动链）。
选 B：不要再格式化 eMMC，且必须拔掉 SD。

> **maskrom 路线**（`rkdeveloptool db <loader>` + `rkdeveloptool wl 0 <img>`）技术上也能
> 整盘写，但要能进 maskrom（短接/按键）+ OTG 口接 PC + 一个可用的 `db` loader，
> **没必要** —— 从 SD 上的系统直接 `dd` 更简单。

---

## 1.9 两个镜像：卡启版 / eMMC 直刷包

> **v6 的交付物**（详见 §1.11）：`fnos_1.2.0302_am40_SD-boot_v6.img`、
> `fnos_1.2.0302_am40_eMMC_v6.img`、`rom/am40-eMMC-flash_v6/`。
> 与 v5 的唯一差别是 BOOT 分区多了修正 DTB + `am40-apply-fixes` + payload，**rootfs 逐字节相同**。
> 下面这段 v5 的说明对 v6 同样适用（只是文件名带 `_v6`）。

ophub 那种"一个 AIO 镜像走天下"是为了省事。按瑞芯微工具的用法拆成两份更清楚：
**卡启 = 自包含的整盘镜像；直刷 = loader（MiniLoaderAll）+ parameter + 分区文件分开给工具**。
两份的**系统和启动链是同一份**（都来自 v5，`0x8000~0x1000000` = AM40 eMMC 原生启动链，
逐字节一致），差别在 **GPT 分区位置**和**刷写方式**。

| # | 交付物 | 目标盘 | 刷法 |
|---|---|---|---|
| 1 | `rom/fnos_1.2.0302_am40_SD-boot.img` | SD 卡 | 整盘写卡：`dd` / balenaEtcher / 工具 `wl 0` |
| 2 | `rom/fnos_1.2.0302_am40_eMMC.img` | eMMC（整盘） | `dd` 到 `/dev/mmcblk0`，或 `rkdeveloptool wl 0` |
| 3 | `rom/am40-eMMC-flash/` | eMMC（瑞芯微工具分区刷） | RKDevTool「下载镜像」/ `upgrade_tool`；loader 用包里的 `MiniLoaderAll.bin` |

### 1.9.1 三者的差别（就两点）

| | 卡启版 (1) | eMMC 整盘版 (2) | 直刷包 (3) |
|---|---|---|---|
| 启动链 `0x8000~0x1000000` | AM40 eMMC 原生（自包含） | 同左，逐字节一致 | 拆成 `uboot.img`(LBA 0x4000) / `trust.img`(0x6000) + `MiniLoaderAll.bin`(LBA 0x40) |
| p1 `BOOT` | @32MB，360MB（飞牛原版布局） | **@16MB，512MB**（AM40 eMMC 原生布局） | 由 `parameter.txt` 现场生成：@16MB，512MB |
| p2 `rootfs` | @408MB，3253MB | @528MB，3253MB | `-@0x00108000(rootfs:grow)` → 工具直接建到盘尾（≈28GB） |
| GPT PARTUUID | 飞牛原版值 | 沿用本机 eMMC 现有值 | 由 `parameter.txt` 里 `uuid:rootfs=` 指定 |
| 首次启动扩容 | `resize-rootfs` 撑到卡容量 | 同左（撑到 eMMC 99%） | 同左 |

`boot.cmd` 里是 `part uuid ${devtype} ${devnum}:${distro_rootpart} rootuuid` ——
**PARTUUID 是启动时现场读的**，所以上面这些 GPT 差异都不影响启动；
但 `distro_bootpart` 默认 1、`distro_rootpart` 默认 2，**boot 必须是第 1 个分区、rootfs 第 2 个**
（三份都满足）。

### 1.9.2 刷 eMMC —— 三种刷法

**A. 从 SD 上的系统整盘写（最省事，推荐）**

```sh
sudo dd if=fnos_1.2.0302_am40_eMMC.img of=/dev/mmcblk0 bs=4M conv=fsync status=progress
sudo sync
sudo poweroff          # 关机后把 SD 拔掉
```

**B. PC 上用 `rkdeveloptool` / `upgrade_tool` 整盘写（工具会先要一个 miniloader）**

```sh
rkdeveloptool db MiniLoaderAll.bin                       # 进 maskrom，下载启动(含 DDR 初始化)
rkdeveloptool wl 0 fnos_1.2.0302_am40_eMMC.img           # 从 LBA0 整盘写
# 等价：upgrade_tool db MiniLoaderAll.bin && upgrade_tool wl 0 fnos_1.2.0302_am40_eMMC.img
```
`MiniLoaderAll.bin` 用 `miniloader/RK3399_MiniLoaderAll.bin`（就是你之前刷 eMMC 用的那个，
官方 rkbin，md5 `eda42d2cd839f65967000c76391fd46d`）或包里的同名文件。

**C. 瑞芯微工具分区刷（"镜像和 boot 分开"的那种用法）**

`am40-eMMC-flash/` 里的对应关系：

| 工具里的行 | 文件 | 写到 |
|---|---|---|
| Loader / bootloader | `MiniLoaderAll.bin` | LBA 0x40 |
| parameter | `parameter.txt` | （只用来建 GPT） |
| uboot | `uboot.img` | LBA 0x4000（8MB） |
| trust | `trust.img` | LBA 0x6000（12MB） |
| boot | `boot.img` | LBA 0x8000（16MB），512MB 分区 |
| rootfs | `rootfs.img` | LBA 0x108000（528MB），分区涨到盘尾 |

- RKDevTool：**「下载镜像」** 页 → 先把 `Loader` 行设成 `MiniLoaderAll.bin`，再 `parameter`
  行选 `parameter.txt`（选完工具会自动列出 uboot/trust/boot/rootfs 各行），逐个指到同名文件 → 执行。
- 一键更省事：RKDevTool **「升级固件」** → 选 `am40-eMMC-flash/update.img`
  （RKFW 包，已内含 loader + 全部 6 个分区，末尾 32 字节 MD5 自校验通过）。
- 若工具报校验/解析失败，退回上面的分区方式即可 —— 分区方式不依赖任何私有封装字段。

`update.img` 结构（逆向厂商 ROM 得到，已用厂商 ROM 做往返比对验证）：

```
RKFW头(0x66) + MiniLoaderAll + RKAF(package-file/bootloader/parameter/uboot/trust/boot/rootfs)
             + 4字节未知字段(照抄厂商值) + 32字节 ASCII MD5(前面所有内容的 md5)
```

### 1.9.3 刷完必须注意

1. **同时插着 SD 和 eMMC 会撞车**：两份镜像的文件系统 UUID 完全一样（同一个 btrfs/ext4 的
   副本），`fstab` 里按 `UUID=` 挂 `/boot` 时可能挂到另一块盘上。刷完 eMMC 请**把 SD 拔掉**。
2. **eMMC 上原来的旧飞牛会被覆盖**（那份是回滚用的，覆盖前请确认不再需要）。
3. 首次启动看：`eth0` 起来（1000M）、`/` 自动扩容到 ≈29GB、飞牛的 80/443/5666/5667 端口。
4. 时间同步/主机名等跟 SD 版一致。

<details>
<summary>可选：想让 SD 和 eMMC 两份共存（熟练再动）</summary>

在 **SD 上的系统**里执行（eMMC 保持未挂载），给 eMMC 那份换一套 UUID 并同步 `fstab`：

```sh
sudo tune2fs -U random /dev/mmcblk0p1          # boot 分区 ext4
sudo btrfstune -U random /dev/mmcblk0p2        # rootfs btrfs（必须未挂载）
sudo mount /dev/mmcblk0p2 /mnt
sudo sed -i "s/UUID=[0-9a-f-]\{36\}/UUID=$(sudo blkid -s UUID -o value /dev/mmcblk0p2)/" /mnt/etc/fstab  # 按实际值改全
sudo grep UUID /mnt/etc/fstab                  # 核对：/ 用新 btrfs UUID，/boot 用新 ext4 UUID
sudo umount /mnt && sync
# GPT PARTUUID 不冲突即可（boot.cmd 启动时现场读），也可用 sgdisk -G /dev/mmcblk0 重新随机
```
</details>

---



## 1.10 系统更新之后：brick 复盘 + 卡启/eMMC 实测取舍（2026-09-16 21:00 复查）

**事件**：跑飞牛的自动系统更新（1.2.0302 → 1.2.0604，内核 c951 → c1090），之后板子起不来
（网口灯亮 = BootROM 掉 maskrom）。救回方式：把 `_work/am40-backup/am40-chain-32K-16M.bin`
写回 SD 的 `0x8000`（`dd ... bs=512 seek=64 count=32704`）。

### 复查的结论

1. **卡上启动链当时确实是全 0**：从 `_work/am40-backup/sd-updated-head16M.img` 数出来，
   `0x8000~0x1000000` 共 16,744,448 字节、**非零字节 0 个**。
   而三份交付镜像 + `am40-chain-32K-16M.bin` 在这段都是完整的 AM40 启动链
   （md5 `53e488ce68d573274c35730385bd2d01`，已逐份回读校验）→ **不是镜像缺链**。
2. **飞牛的更新流程里没有任何写启动区的东西**（逐项查证）：
   - 根分区找不到 `uboot.img`/`trust.img`/`MiniLoaderAll.bin`/`idbloader`；
   - `updatemgr`、`liveupdate` 二进制里没有 `dd`/`blkdiscard`/`wipefs`/`/dev/mmcblk` 字样；
   - 内核更新只挂 3 个钩子：`/etc/kernel/postinst.d/10-sync-dtb`（内核自带的 rockchip dtb
     rsync 进 `/boot/dtb/rockchip/`）、`15-update-ukernel-ver`（改 `/boot/fnEnv.txt` 的
     `kernelfile=` 与 `vmlinuz` 软链）、`zz-update-grub`（重建 `grub.cfg`）；
   - `/var/log/updatemgr/info.log` 只有应用层 `lazy apply`，没有写盘动作。
   → **这一版更新不背这个锅**。真实触发点已经无法回溯（卡后来被重刷过），
   所以对策不是猜原因，而是**自检 + 可选自愈**（见下）。
3. **更新确实会动 `/boot`**：`10-sync-dtb` 会用内核包里的 `rockchip/` 覆盖 `/boot/dtb/rockchip/`。
   本次 3 个 AM40 DTB 更新后 md5 未变（包里没有同名文件），但**换个内核包就可能被覆盖** ——
   MPP 版的 md5 是 `1eecee2862120de4bf4bb63ce83fc32c`，每次更新后核对。
4. **eMMC 的启动区现在是空的**：`0x8000~0x100000` 全 0，p1 从 LBA 2048（1 MB）开始。
   → 这块 eMMC **已经不能再当启动盘**（启动链要占 1~16 MB，被根分区占了），
   SD 自启动是唯一活路，所以启动链必须保住。

5. **RK3399 编解码能力全量实测（2026-09-16 22:16，`am40-codec-matrix`）**

   | 能力 | 走谁 | 实测（1080p testsrc2 合成片，仅作相对比较） |
   |---|---|---|
   | H.264 解码 | MPP `h264_rkmpp` | ✅ 2.72x（CPU utime 0.22s，真硬件） |
   | H.264 编码 | MPP `h264_rkmpp` | ✅ 1080p 1.22x；4K 0.309x |
   | MJPEG/JPEG 解码 | MPP `mjpeg_rkmpp` | ✅ |
   | MJPEG 编码 | MPP `mjpeg_rkmpp` | ✅ 1.32x |
   | MPEG-1/2/4、H.263 解码 | MPP `mpeg{1,2,4}_rkmpp` / `h263_rkmpp` | ✅ 2.7–11.9x |
   | **HEVC** | ❌ 硬解不可用 | MPP：`client 9 driver is not ready!`；主线 rkvdec 这个内核**不暴露 HEVC** |
   | **VP9（8bit）** | ✅ **主线 rkvdec + `-hwaccel v4l2request`** | utime 0.31s vs 软解 1.50s（真硬件） |
   | **VP8** | ❌ | MPP 把它路由到 client 9；主线 rkvdec 不暴露 VP8（`hantro` 驱动这个内核没编） |
   | **AV1** | ❌ 无硬件 | MPP：`unable to create dec av1 for soc rk3399 unsupported`；软解 `libdav1d` ✅ 4.48x |
   | HEVC 编码 | ❌ 不存在 | `unable to create enc h265 for soc rk3399 unsupported` |
   | 缩放 RGA | ❌ | 有 `scale_rkrga` 滤镜但 `/dev/rga` 不存在 |
   | VC-1 | 未测 | ffmpeg 无 VC-1 编码器，造不出样片 |

   **⚠️ 更正（2026-09-16 22:45）—— 主线 rkvdec 其实是能用的，上一版写错了。**
   直接 ioctl 问 `/dev/video0`（rkvdec，`rockchip-vdec.ko`，Boris Brezillon/Collabora，
   `of:rockchip,rk3399-vdec`）得到的是：
   ```
   OUTPUT_MPLANE : S264 = "H.264 Parsed Slice Data"   VP9F = "VP9 Frame"
   CAPTURE_MPLANE: NV12 (Y/UV 4:2:0)      caps: VIDEO_M2M_MPLANE|EXT_PIX_FORMAT|STREAMING
   ```
   它是 **stateless（V4L2 Request API）** 设备 → 所以 `h264_v4l2m2m`/`hevc_v4l2m2m`
   （stateful 包装）当然报 `Could not find a valid device`；正确用法是 **`-hwaccel v4l2request`**，
   实测**确实走硬件**（日志：`Using V4L2 media driver rkvdec for S264`）：
   ```
   H.264: 软解 utime 0.950s → v4l2request 0.374s   ✅
   VP9  : 软解 utime 1.503s → v4l2request 0.309s   ✅
   HEVC : v4l2request 2.020s ≈ 软解 → 驱动不暴露 HEVC，回落软解 ✗
   ```
   主线在这块板上只覆盖 RKV（H.264 + VP9 8bit 解码）；`hantro_vpu`（VDPU2/VEPU2 的
   mainline 驱动）**这个内核没编**（`modinfo hantro_vpu` 找不到）→ 主线给不了硬件编码。
   **但两栈不能混用**：`-hwaccel v4l2request` 出的帧喂给 `h264_rkmpp` 编码器会失败
   （`hwmap=derive_device=rkmpp` 直接 rc=139，`hwdownload+hwupload` rc=218）→ 转码管线请用
   单一栈：全 MPP（H.264）或「软解 + MPP 硬编」。

   可用转码管线实测（H.264 为目标码）：

   | 管线 | 实测 |
   |---|---|
   | 1080p H.264(MPP 解) → 720p → H.264(MPP 编) | ✅ 1.43x |
   | 1080p H.265(软解) → 720p → H.264(MPP 编) | ✅ 1.54x（≈46fps） |
   | 1080p H.265(软解) → 1080p → H.264(MPP 编) | ✅ 1.07x |
   | 1080p VP9(软解) → 720p → H.264(MPP 编) | ✅ 2.03x |
   | 1080p AV1(软解 dav1d) → 720p → H.264(MPP 编) | ✅ 2.24x |
   | 4K H.265(软解) → 1080p → H.264(MPP 编) | ⚠ 0.576x（不实时；瓶颈是软缩放，纯 4K 软解 0.99x） |
   | 对照：1080p libx264 软编 / libx265 软编 | 0.41x / 0.036x（硬编比 x264 快约 3 倍） |

   **结论：这块板子上的可用方案 = 「软解 + H.264 硬编」**（1080p 各种来源都能实时转 H.264），
   H.264 那一路可以全硬件（解+编）。4K 转码不实时，建议直通/Jellyfin 直接播原码。
   命令模板：

   ```bash
   ffmpeg -c:v hevc -i in.mkv -vf scale=1280:720 -c:v h264_rkmpp \
          -b:v 3M -maxrate 3M -bufsize 6M -f mpegts udp://...
   ```

   测试脚本：板子 `/usr/local/sbin/am40-codec-matrix`（副本 `BOOT 分区 /am40-codec-matrix.sh`），
   日志 `/tmp/codec_matrix.log`。


   → 这块 eMMC **已经不能再当启动盘**（启动链要占 1~16 MB，被根分区占了），
   SD 自启动是唯一活路，所以启动链必须保住。

### ★ 2026-09-16 22:50 更新：满编 MPP 已落地（HEVC/VP9/4K 硬解全部走 MPP）

上面第 5 条里"HEVC/VP9 只能软解"的结论**已作废** —— 根因不是内核，而是 fnOS 的 MPP 模块
编译时没开 `CONFIG_CPU_RK3399`（所以模块的 compatible 列表里没有 `rkv-decoder-rk3399`）。
用 `kernel/kernel-develop-6.1.zip` 的同源 MPP 源码重编了
`/lib/modules/<ver>/updates/trim/rk_vcodec/rk_vcodec.ko`（板子原生 gcc + 内核头文件编译，
源码与补丁留在板子 `~/mppbuild`），再配 `rk3399-smart-am40-mpp-full.dtb`
（= 官方 6.1 BSP 风格的 `rkvdec@ff660000` 节点 + 300MHz `normal/advanced-rates`；
mainline 的 `video-codec@ff660000` 按 BSP 做法 disable）：

| 能力 | 实测（1080p testsrc2） |
|---|---|
| H.264 解码 / 编码 | ✅ 1.37x / ✅ 0.67x（4K 编码 0.25x） |
| **H.265 8bit / 10bit 解码** | ✅ **1.42x / 1.37x** |
| **H.265 4K 解码** | ✅ **1.14x** |
| **VP9 解码（含 10bit）** | ✅ 1.26x / 1.00x |
| MPEG-1/2/4、H.263、MJPEG/JPEG | ✅（同前） |
| 全 MPP 转码：4K H.265 → 1080p → H.264 | ✅ 0.48x |
| 全 MPP 转码：1080p H.265 → 720p → H.264 | ✅ 0.51x |
| VP8 / AV1 | ❌ 仍无硬件 |
| HEVC 编码 | ❌ RK3399 没有 |

> ⚠️ **上面这张表是 2026-09-16 22:50 那版模块（v2）的数字，已经过时。**
> 00:15 修掉"每帧一次内核 WARN"之后，同一个 DTB、同一个 295MHz 时钟下的实测是：
> H.264 解 **10.8x** / 编 **1.22x**（4K 编 0.311x）、H.265 8bit **9.09x** / 10bit **7.18x**、
> **4K H.265 2.52x**、VP9-10bit 3.66x、H.263 12.2x、MPEG1/2/4 2.7~3.0x、
> MJPEG 解 0.95x / 编 1.34x；管线 H.265(软解)→H.264 1.12x、H.264→H.264 1.17x。
> **没有超频**。详见下面"★★ 2 倍变慢真凶"那一节（`rk_vcodec.ko.rk3399-full-v3`）。

编译要点（可复现）：`-DCONFIG_CPU_RK3399` + `-DCONFIG_ROCKCHIP_MPP_{RKVDEC,VDPU2,VEPU2,JPGDEC,JPGENC,PROC_FS}`；
6.18 API 适配 5 处：`class_create(name)`、`MODULE_IMPORT_NS("DMA_BUF")`、`fd_file(f)`、
`iommu_map(..., GFP_KERNEL)`、去掉 `asm/dma-iommu.h`；补齐 vendor 头
（`dma-buf-cache.h`/`rockchip_sip.h`/`rockchip_opp_select.h`/`uapi/linux/rk-mpp.h`）。

遗留一项：
**MPP 编码比 fnOS 原模块慢**（1080p H.264 0.67x vs 1.22x；clock 已提到 400MHz，原因未完全定位）——实时够用。
→ **已于 2026-09-17 00:15 解决，真因是每帧一次的内核 WARN，见下面"★★ 2 倍变慢真凶"那一节。**

（原"遗留第 2 项：mediasrv 崩溃的真因是 librga 而不是 MPP"已于 23:05 修好，见下一节。）

**回滚**（两份备份都在 BOOT 分区）：
```sh
sudo cp /boot/rk_vcodec.ko.orig /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo reboot
# DTB：把 /boot/fnEnv.txt 的 fdtfile 换回 rockchip/rk3399-smart-am40-mpp.dtb
```

### ★ 2026-09-16 23:05 更新：mediasrv 崩溃已修复（根因 = RGA 的 compatible 写错了）

**结论：mediasrv 现在正常运行，`/dev/rga` 有了，`systemctl enable mediasrv` 已开。**

#### 根因链（一句话：DTB 里的 RGA 节点没有任何驱动认领）

```
DTB 里 rga@ff680000  compatible = "rockchip,rk3399-rga"   ← 上游 mainline 的写法
        ↓
fnOS 内核 CONFIG_VIDEO_ROCKCHIP_RGA is not set（没有 mainline RGA 驱动）
fnOS 只提供 vendor 版 rga3.ko，它的 of_match 只有：
        "rockchip,rga2" / "rockchip,rga2_core0" / "rockchip,rga3" / "rockchip,rga3_core0" / "rockchip,rga3_core1"
        ↓
rga3 模块 by name 能加载，但 rga2_driver/rga3_driver 都匹配不上 DT 节点 → num_of_scheduler = 0
        ↓
rga_drv.c: rga_iommu_bind() 三个 index 全是 -1 → "binding map scheduler failed!" → -EFAULT → 模块 init 整个回退
     dmesg: rga_iommu: rga_iommu_bind, binding map scheduler failed!
            rga: rga iommu bind failed!
     modprobe: ERROR: could not insert 'rga3': Bad address
        ↓
没有 /dev/rga → librga.so.2.1.0 一调用就 SEGV（DABT level 0 translation fault）→ mediasrv status=11/SEGV
```

**证据（原始 BSP 也是这么写的）**：`kernel-develop-6.1/arch/arm64/boot/dts/rockchip/rk3399-linux.dtsi`
的 rga 节点就是 `compatible = "rockchip,rga2"; clock-names = "aclk_rga","hclk_rga","clk_rga";`，
`rockchip_linux_defconfig` 里开的也是 `CONFIG_ROCKCHIP_MULTI_RGA=y`（= 编出这个 rga3.ko）。
4.4 firefly 的 `rk3399-firefly-android.dts` 同样是 `"rockchip,rga2"`。
**是 fnOS/AM40 这份 DTB 从上游 mainline 抄了 `rockchip,rk3399-rga`，跟它自己发的模块对不上。**

#### 修法：一个属性，一条命令（板子上就能做，不用重编内核）

新增 DTB **`rk3399-smart-am40-mpp-rga.dtb`**（= `-mpp-full` + 只改 RGA 一个属性），
md5 `21d879e3618f6f49498d629139a6d0c3`，`/boot/fnEnv.txt` 已指过去：

```sh
# ① 原地生成（板子上有 dtc/fdtput）
sudo cp /boot/dtb/rockchip/rk3399-smart-am40-mpp-full.dtb \
        /boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb
sudo fdtput -t s /boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb \
        /rga@ff680000 compatible "rockchip,rga2"
# ② 切过去
sudo sed -i 's#fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40-mpp-rga.dtb#' /boot/fnEnv.txt
sudo reboot
# ③ 重启后
sudo modprobe rga3          # 其实开机就自动加载了
ls -l /dev/rga              # crw-rw---- root video
sudo systemctl enable --now mediasrv
```

#### 修完的自检输出（对照）

```text
# dmesg
rga2 ff680000.rga: probe successfully, irq = 83, hw_version:3.2.18218
rga_iommu: IOMMU binding successfully, default mapping core[0x4]
rga: Module initialized. v1.3.4
```

```text
# mediasrv 日志 /usr/trim/logs/mediasrv.log（新增的两行是以前从来没有的）
[09-16 22:59:02.658] [info] gpu in working, device_id 0, device '', vendor 'Rockchip', card_path ''
[09-16 22:59:02.665] [info] gpu enable: true, selected sequence: 1
[09-16 22:59:02.668] [info] webserver listen on '/var/run/mediasrv.socket'
# systemctl status mediasrv → active (running)，NRestarts=0
# /proc/<pid>/fd → 19 -> /dev/rga   +  /dev/dma_heap/{system,cma}-uncached
```

#### 顺手验证 RGA 真的能算（不是只开了个设备节点）

硬件版本 `3.2.18218` 落到驱动的兜底档 `rga2e_data`，`mmu = RGA_MMU`（RGA2 自带页表，不需要 IOMMU），
所以才会打印 "IOMMU binding successfully, default mapping core[0x4]"。

```bash
# ffmpeg 的 rkrga 滤镜现在能用了（以前直接崩在 librga）
ffmpeg -init_hw_device rkmpp=hw -filter_hw_device hw -i in.mp4 -t 2 \
  -vf "format=nv12,hwupload,scale_rkrga=w=1280:h=720:format=nv12,hwdownload,format=nv12" \
  -pix_fmt yuv420p -f rawvideo rga.yuv
ffmpeg -i in.mp4 -t 2 -vf "scale=1280:720,format=yuv420p" -pix_fmt yuv420p -f rawvideo sw.yuv
# 两者逐像素比：SSIM All:0.9961 (Y 0.9987 / U 0.9901 / V 0.9918)  → RGA 缩放结果正确
```

MPP + RGA 全硬件转码链路（解码→RGA 缩放→编码，实测 1080p 约 **0.49x**，
瓶颈在 H.264 编码器 0.67x，RGA 缩放本身几乎不额外花时间：去掉缩放 20.3s vs 带 RGA 19.8s / 10s 素材）：

```bash
ffmpeg -hwaccel rkmpp -hwaccel_output_format drm_prime -i in.mp4 \
       -vf "scale_rkrga=w=1280:h=720:format=nv12" -c:v h264_rkmpp -b:v 6M out.mp4
```

> 注意：`scale_rkrga` 需要 `drm_prime` 帧，纯软件输入要先 `hwupload`/`hwmap`；
> 直接拿 `testsrc2` 喂它会报 `Function not implemented (-38)`，那是格式协商问题，不是 RGA 坏了。

#### 这一节新增的文件 / 回滚

| 文件 | md5 | 说明 |
|---|---|---|
| `/boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb` | `21d879e3…` | **当前默认**（满编 MPP + RGA 修正） |
| `/boot/dtb/rockchip/rk3399-smart-am40-mpp-full.dtb` | `a916a522…` | 满编 MPP，无 RGA 修正（回滚用） |
| `/boot/fnEnv.mpp-full.txt` | — | 上一版 fnEnv.txt 备份 |
| `/boot/rk3399-smart-am40-mpp-rga.dtb` | 同上 | BOOT 分区根目录副本，方便 Windows 下取 |

回滚 RGA（只影响 RGA，MPP 照常）：

```sh
sudo sed -i 's#fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40-mpp-full.dtb#' /boot/fnEnv.txt
sudo reboot     # 之后 mediasrv 会重新开始 SEGV，记得 systemctl disable mediasrv
```

WSL 侧源码也已同步：`_work/dts_am40/rk3399-smart-am40-mpp-rga.dts`（含注释说明为什么改）与同名 `.dtb`。

### ★ 2026-09-16 23:40 更新：AV1 视频也能播了（mediasrv 兼容垫片）

RGA 修好以后 mediasrv 能起来了，但**播 AV1 片子还是报错**。用 `-l debug` 抓到真实链路后，
发现是两件跟 RK3399 硬件能力有关的事叠在一起，都不是飞牛的 bug：

#### 问题 1：RK3399 没有 AV1 / VP8 硬解，mediasrv 却硬要开

```text
[debug] codec_id    225, Rockchip MPP (Media Process Platform) AV1 decoder
[debug] hw devices  decoder=rkmpp, filter=rkmpp
[error] avcodec_open2 for video decoder error
[error] [ffmpeg] errno -542398533, msg 'Generic error in an external library'   ← AVERROR_EXTERNAL
# journalctl -u mediasrv 里的原话：
[av1_rkmpp] Failed to init MPP context: -1
mpp: unable to create dec av1 for soc rk3399 unsupported
```

也就是说 **MPP 自己知道 rk3399 不支持 AV1**（打印得清清楚楚），但 mediasrv 挑解码器时是
拿 `av_codec_iterate()` 遍历所有解码器、按"有没有 rkmpp 后缀"来选的——它只看到"有个
av1_rkmpp"，就以为能用。

> 顺带说明：mediasrv 的二进制里连 `av1_rkmpp` 这个字符串都没有，名字是运行时拼出来的；
> 它也不用 `avcodec_find_decoder_by_name()`，所以想在 `find_decoder*` 里做手脚是没用的。

#### 问题 2：RK3399 没有 HEVC 硬编，前端却要 HEVC

解码修好之后管线走到了编码器，又断了：

```text
[debug] codec_id  173, Rockchip MPP (Media Processing Platform) HEVC encoder
[hevc_rkmpp] Failed to init MPP context: -1
mpp: unable to create enc h265 for soc rk3399 unsupported
```

RK3399 的 VEPU2 **只能编 H.264（和 MJPEG）**。而浏览器（Edge/Chrome 装了 HEVC 扩展）会告诉
飞牛前端"我能播 HEVC"，前端就发 `"videoEncoder":"hevc"`，mediasrv 拿这个名字拼出
`hevc_rkmpp` 去开 → 必然失败。**这跟源文件是不是 AV1 无关**：任何需要转码的片子
（比如 AV1/HEVC 源 + ASS 字幕要烧进去）在这个浏览器上都会撞到。

#### 解决办法：一个 LD_PRELOAD 垫片（不碰飞牛二进制，可随时摘掉）

`_work/rktools/mediasrv-shim.c`（约 250 行，纯 libc + dl）做三件事：

| 拦截点 | 动作 |
|---|---|
| `av_codec_iterate()` | 把 `av1_rkmpp` / `vp8_rkmpp` 藏起来 → mediasrv 自己就会去用软件解码器 `libdav1d` / `libvpx` |
| `avcodec_find_encoder_by_name()` | 凡是 `*_rkmpp` 且不是 `h264_rkmpp`/`mjpeg_rkmpp` 的（即 `hevc_rkmpp`/`av1_rkmpp`/`vp9_rkmpp`…）一律改写成 `h264_rkmpp` |
| `avcodec_open2()` / `avcodec_find_decoder*` | 只记日志到 `/tmp/mediasrv-shim.log`，方便以后排查 |

**为什么不能在 `avcodec_open2` 里偷换解码器**：ffmpeg 8 会拒绝——
`This AVCodecContext was allocated for av1_rkmpp, but libdav1d passed to avcodec_open2()`。
必须在"分配上下文之前"就让它选对，所以只能从 `av_codec_iterate` 下手。

一条命令安装 / 查看 / 卸载（脚本同时放了一份在 BOOT 分区，系统更新后重跑一次即可）：

```sh
sudo am40-fix-mediasrv            # 装/重装（优先现场 gcc 重编，失败则用 /boot 里的预编译版）+ 重启 mediasrv
sudo am40-fix-mediasrv --status   # 只看状态
sudo am40-fix-mediasrv --remove   # 摘掉垫片，恢复原样
```

#### 实测结果（用的是用户那份真实片源）

片源：`/vol4/1000/HDD-Media/Media/Animate/冰菓/Hyouka 2012 S01E01-[1080p][BDRIP][AV1.OPUS].mkv`
（AV1 Main **10bit** 1920×1080 23.976fps + Opus 5.1 + ASS 字幕 + MJPEG 封面）

```text
请求：{"req":"media.getPlayLink", ..., "videoEncoder":"hevc", "subtitleIndex":2, quality 1080p}
修复前：{"result":"fail","errno":68157450}
修复后：{"result":"succ","playLink":"/media/<hash>/preset.m3u8","hlsTime":4,...}
```

垫片日志（完整链条，全部硬件/软件各就各位）：

```text
[shim] find_encoder_by_name("hevc_rkmpp") -> RK3399 无此硬编，改用 "h264_rkmpp" -> h264_rkmpp
[shim] iterate: 对 mediasrv 隐藏 vp8_rkmpp（RK3399 无此硬解）
[shim] iterate: 对 mediasrv 隐藏 av1_rkmpp（RK3399 无此硬解）
[shim] find_decoder(id=225) -> libdav1d ; open2 OK codec=libdav1d     ← AV1 走软件解码
[shim] open2 OK codec=libfdk_aac / opus / ssa / webvtt                ← 音频/字幕正常
[shim] open2 OK codec=h264_rkmpp                                       ← 视频走硬件 H.264 编码
```

分片实测（`ffprobe` 00000.ts）：`h264 High 1920x1080` + `aac`，字幕烧进去了。

**回归验证**（确认没把 H.264/H.265 硬解搞坏）：

| 源 | 解码器 | 编码器 |
|---|---|---|
| H.265 1080p → 720p | `hevc_rkmpp`（硬件）✓ | `h264_rkmpp`（硬件）✓ |
| H.264 1080p → 720p | `h264_rkmpp`（硬件）✓ | `h264_rkmpp`（硬件）✓ |
| AV1 10bit 1080p → 1080p | `libdav1d`（软件） | `h264_rkmpp`（硬件）✓ |

#### ⚠️ 但速度要注意：AV1 片子转码只有 ~0.36x 实时

单独测过（6 秒素材，`ffmpeg -benchmark`）：

| 环节 | 速度 |
|---|---|
| 只软解 AV1 10bit 1080p（libdav1d） | **1.63x**（不算瓶颈） |
| 软解 + H.264 硬件编码 | **0.49x** ← 瓶颈在编码器 |
| mediasrv 完整链路（+烧 ASS 字幕 +AAC +HLS 切片，片源在机械盘上） | **≈0.36x** |

**瓶颈是 H.264 硬件编码器只有 ~0.67x 实时**，正是前面 MPP 那一节留下的遗留项
（飞牛原版模块能到 1.22x）。24 分钟的番在 RK3399 上要转约 1 小时，网页播放会一直缓冲。
真要顺滑看这类 1080p 10bit AV1 + ASS 字幕的片子，现实的选项是：
① 先让飞牛把整集转码缓存好再看；② 在播放器里选 720p 档（像素少一半多，编码器压力小很多）；
③ 之后有空再回头啃 MPP H.264 编码为什么比原版慢一倍（那是唯一能把 0.36x 提到 0.6x 以上的杠杆）。

#### 这一节新增的文件

| 位置（板子） | 说明 |
|---|---|
| `/usr/local/lib/mediasrv-shim.so` | 垫片本体（`am40-fix-mediasrv` 会用下面的 .c 现场重编） |
| `/etc/systemd/system/mediasrv.service.d/10-am40-shim.conf` | systemd drop-in：`LD_PRELOAD` + `-l debug` |
| `/usr/local/sbin/am40-fix-mediasrv` | 安装/查看/卸载脚本（副本 `/boot/am40-fix-mediasrv.sh`） |
| `/boot/am40-mediasrv-shim.c` | 垫片源码（BOOT 分区，重装用） |
| `/boot/am40-mediasrv-shim.so` | 预编译备份（现场编不出来时兜底） |
| `/boot/10-am40-shim.conf` | drop-in 备份 |
| WSL：`_work/rktools/mediasrv-shim.c`、`_work/rktools/am40-fix-mediasrv.sh` | 源码留档 |

> 日志：垫片日志在 `/tmp/mediasrv-shim.log`（tmpfs，重启即清）。
> mediasrv 本身现在是 `-l debug`（drop-in 里那行 `ExecStart=`），觉得吵就把 `-l debug` 去掉再
> `systemctl daemon-reload && systemctl restart mediasrv`。
> `am40-check` 已加这一节的检查项，现在是 **OK=26 FAIL=0**。

### ★★ 2026-09-17 00:15 更新：2 倍变慢的真凶找到了 —— 每帧一次的内核 WARN

这一节把前面挂了很久的"MPP 比飞牛原模块慢一倍"彻底结案，**而且不需要超频**。

#### 症状与错误方向

| | 满编 MPP 模块（旧） | 飞牛原版模块 | 修好后 |
|---|---|---|---|
| 1080p H.264 编码 | 0.66x | **1.25x** | **1.25x** |
| 1080p H.264 解码 | 1.37x | **3.33x** | **5.5~9.7x** |

两边跑在**同一个 DTB、同一个 297MHz 时钟**下，差一倍，所以一开始我怀疑是时钟。
把 `aclk_vcodec` 用 procfs 调上去确实能提回来（`/proc/mpp_service/vepu/aclk`）：

| aclk_vcodec | 编码速度 |
|---|---|
| 297MHz（厂商默认） | 0.66x |
| 400MHz | 0.76x |
| 594MHz | 0.89x |
| 800MHz | 1.23x ← 跟原版一样 |
| 1000MHz+ | **0.03x（直接崩性能）** |

> ⚠️ 结论是**不要超频**：800MHz 只是"用 2.7 倍功耗换回本来就该有的速度"，
> 而且 1GHz 会让编码器掉到 1fps。厂商默认 300MHz 是对的，问题在别处。

#### 真凶：`iommu_set_fault_handler()` 被每帧调用一次

```c
/* drivers/video/rockchip/mpp/mpp_iommu.c: mpp_iommu_dev_activate() */
info->dev_active = dev;
iommu_set_fault_handler(info->domain, dev->fault_handler ?
                        dev->fault_handler : mpp_iommu_handle, dev);   /* ← 这里 */
```

6.18 里这个 API 已经废弃（`domain->ops->set_fault_handler` 是 NULL），所以它第一件事就是

```
WARNING: CPU: 5 PID: 7972 at drivers/iommu/iommu.c:2015 iommu_set_fault_handler+0x14/0x38
Modules linked in: rk_vcodec(O) xt_conntrack ... （一行 1.5KB 的模块清单）
CPU: 5 UID: 0 PID: 7972 Comm: mpp_worker_0 Tainted: P W O ...
Call trace: ...
```

而 `mpp_iommu_dev_activate()` 是**每次给任务激活设备时都调用**的 —— 等于**每帧一次**：

```text
实测：编码 300 帧 → dmesg 里 287 条 iommu_set_fault_handler WARN
      解码 300 帧 → 同样是 287 条
```

`/boot/fnEnv.txt` 里是 `console=both`，于是每一帧都要往**串口(1.5Mbps) + HDMI 控制台**
刷 ~2KB 调用栈。一帧的实际编码时间被这堆 printk 拖掉一倍以上。

**验证**（不重编模块，只把控制台输出关掉就立刻见分晓）：

```sh
sudo dmesg -n 1     # 只让 emergency 上控制台
# 编码 600 帧: 0.66x -> 1.17x      解码: 1.37x -> 9.2x
sudo dmesg -n 7     # 改回来
```

#### 修法：把这个调用编译掉（一个补丁，重编模块）

`_work/rktools/am40-patch-iommu.py`（板子 `/boot/am40-patch-iommu.py`）把那段调用
`#if 0` 掉，并给 `mpp_iommu_handle` 加 `__maybe_unused`；6.18 的 rockchip-iommu
自己会在中断里报 page fault，不需要驱动再注册一遍。

```sh
# 板子上原地重编（源码在 ~/mppbuild）
python3 /boot/am40-patch-iommu.py
cd ~/mppbuild && make -C /lib/modules/$(uname -r)/build M=$PWD modules \
     KCFLAGS="-Wno-error=incompatible-pointer-types"
sudo systemctl stop mediasrv
sudo cp ~/mppbuild/rk_vcodec.ko /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo modprobe -r rk_vcodec && sudo modprobe rk_vcodec
sudo systemctl start mediasrv
```

模块版本号也一起改成了 `custom-rk3399-full-v3`（`/proc/mpp_service/version` 能看到），
所以 `am40-check` 会直接告诉你有没有装错版本。

#### 修完的实测

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 1080p H.264 编码（600 帧，默认 295MHz） | 0.66x | **1.25x** |
| 1080p H.264 解码 | 1.37x | **5.5 ~ 9.7x** |
| AV1 10bit 软解 → H.264 硬编（纯 ffmpeg） | 0.49x | **1.49x** |
| **mediasrv 全链路**：1080p AV1 10bit + 烧 ASS 字幕 + AAC + HLS | 0.36x | **0.80x** |
| 同上但播放器选 **720p** 档 | — | **1.07x（实时）** |
| dmesg 噪声（300 帧编 + 一遍解） | 2588 行 / 287 条 WARN | **0 行** |

现在这块板子看 1080p 10bit AV1 + ASS 字幕的番：选 720p 档就是实时的；
1080p 档 0.8x，会缓冲但能看（瓶颈已经不是编码器，是 libass 烧字幕那部分）。

#### 回滚

```sh
sudo systemctl stop mediasrv
sudo cp /boot/rk_vcodec.ko.rk3399-full-v2 /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
# 或退回飞牛原版（只剩 H.264 硬解/硬编，没有 HEVC/VP9）：
# sudo cp /boot/rk_vcodec.ko.orig /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo modprobe -r rk_vcodec && sudo modprobe rk_vcodec
sudo systemctl start mediasrv
```

| 位置 | 说明 |
|---|---|
| `/boot/rk_vcodec.ko.rk3399-full-v3` | **当前版本**（含 WARN 修复，217,992 B） |
| `/boot/rk_vcodec.ko.rk3399-full-v2` | 上一版（每帧 WARN，慢一倍）—— 回滚用 |
| `/boot/rk_vcodec.ko.rk3399-full` | v1（第一次满编，同上慢） |
| `/boot/rk_vcodec.ko.orig` | 飞牛原版（只有 H.264，无 HEVC/VP9 硬解） |
| `/boot/am40-patch-iommu.py` + `/boot/am40-mpp_iommu-patched.c` | 补丁脚本 + 打过补丁的源码 |
| WSL：`_work/rktools/am40-patch-iommu.py` | 补丁留档 |

> 关于用户给的 `rkmpp/mpp-develop.zip`（MPP 1.1.0 / `kmpp` 新一代内核模块）：
> 翻过了，它的 `kmpp/` 里**已经没有 fault handler 这套东西**，说明上游同样是把它去掉了
> —— 跟我们这个补丁方向一致。它的内核部分是新一代对象模型，跟板上飞牛的
> `librockchip_mpp.so`（a9380ef / jellyfin-mpp-next）不是一套接口，不能直接拿来换。
> 真正需要的那一行差异（去掉 `iommu_set_fault_handler`）已经在 v3 里落地了。


## 1.11 v6：只烘焙设备树 + 一个统一修复脚本（**当前交付版**）

### 为什么这么设计

前面（23:05 / 23:40 / 00:15）那些修复里，**只有设备树是"必须在开机前就位"的**：
AB 两个 DTB 变体（RGA compatible + 满编 MPP 节点）不进镜像，板子起来就没 `/dev/rga`，
mediasrv 必崩。其余修复（MPP 模块、mediasrv 垫片、服务 enable）**都能在系统跑起来之后热处理**。

所以 v6 的策略是：**镜像只多一个 DTB + 一个脚本 + 一个 payload，rootfs 一个字节都不动**。
好处：镜像尽可能接近原版（好审计、好回滚），所有"侵入性"改动集中在一个你能读完的脚本里；
系统更新之后重跑同一条命令就能补回来。

> 顺带：**只烘焙 DTB 就已经把"mediasrv 一启动就崩"这个最显眼的问题修好了** ——
> 那个崩溃纯粹是 RGA 节点 compatible 的事，跟模块/垫片无关。

### 刷完机做什么（一条命令）

```sh
# 从新卡启动后
sudo bash /boot/am40-apply-fixes

# 只想看状态 / 只看会做什么：
sudo bash /boot/am40-apply-fixes --status
sudo bash /boot/am40-apply-fixes --dry-run
```

它会依次处理（**幂等，可以反复跑**）：

| 步骤 | 做什么 | 失败时 |
|---|---|---|
| 1. 设备树 | 校验 `rk3399-smart-am40-mpp-rga.dtb`；把 `fnEnv.txt` 的 fdtfile 切到它；必要时用 `fdtput` 修 RGA compatible | 只提示，不动 stock DTB |
| 2. MPP 模块 | 按 `uname -r` 装带 WARN 修复的满编 `rk_vcodec.ko`（预编译优先，没有就现场 `make`），热重载并校验版本 | 自动回滚到原模块 |
| 3. mediasrv 垫片 | 现场编译/安装 `mediasrv-shim.so` + systemd drop-in，重启 mediasrv 并确认真的加载了 | 自动摘掉 drop-in 回滚 |
| 4. 服务与权限 | `enable mediasrv`、修正 `/dev/mpp_service` 权限、更新 udev 规则 | — |
| 5. 收尾 | 装 `am40-check`/`am40-fix-mediasrv`/`am40-apply-fixes`，同步文档到 /boot，最后跑一遍自检 | — |

**只有第 1 步（设备树）需要重启才生效**，其余都是热生效。脚本最后会明确告诉你
`>>> 设备树/模块有需要重启才生效的项：sudo reboot`。
原始文件备份都在 `/boot/am40-backup-<时间戳>/`。

### v6 镜像里到底比 v5 多了什么

BOOT 分区（`/boot`）新增/更新（**rootfs 完全没动**）：

| 文件 | 说明 |
|---|---|
| `dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb` | **新的默认设备树**（满编 MPP + RGA 修正，md5 `21d879e3…`） |
| `dtb/rockchip/rk3399-smart-am40-mpp-full.dtb` | 满编 MPP、无 RGA 修正（回滚/对照用） |
| `fnEnv.txt` | `fdtfile` 改成 `rockchip/rk3399-smart-am40-mpp-rga.dtb`（`kernelfile` 不动） |
| `am40-apply-fixes` | **统一修复脚本**（唯一需要你执行的入口） |
| `am40-fixes.tar.gz` | payload（283 KB）：两份预编译模块 + MPP 源码 + 垫片 + 脚本 + 文档 |
| `am40-check.sh` / `AM40-NOTES.txt` / `AM40-刷机说明.md` | 更新到最新版 |

payload 里有什么（`tar tzf /boot/am40-fixes.tar.gz` 可看）：

```
rk_vcodec-6.18.18.c951-trim.ko    ← 镜像自带内核的补丁版模块（预编译）
rk_vcodec-6.18.18.c1090-trim.ko   ← 更新到 1.2.0604 后内核的补丁版模块
mpp-src/                          ← 同一份源码（将来换内核可现场编）
am40-patch-iommu.py               ← 「每帧 WARN」那个修复的补丁（留档）
mediasrv-shim.c/.so               ← 兼容垫片（AV1 软解 / HEVC 编码回退）
10-am40-shim.conf / 99-am40-mpp.rules
dtb/*.dtb                         ← 修正设备树（备用）
bin/ doc/                         ← 自检/修复脚本与文档
```

### v6 的刷完机状态对照

| | 只刷 v6、不跑脚本 | 跑完 `am40-apply-fixes` 后 |
|---|---|---|
| mediasrv | ✅ 能启动（/dev/rga 有了） | ✅ 不变 |
| H.264 解/编 | ✅ 可用，但每帧 WARN，慢一倍 | ✅ 快一倍（解 10.8x / 编 1.22x） |
| H.265 / VP9 / 4K 硬解 | ❌ 原版模块没绑 rkvdec | ✅ 9.09x / 7.18x / 2.52x |
| AV1 播放、HEVC 编码回退 | ❌ 报 errno 4096 | ✅ 正常（720p 档实时） |

所以"不跑脚本"不是半成品，而是一个能用的降级形态；跑一下脚本才是完整形态。

### 回滚 v6

```sh
# 设备树：把 fdtfile 换回 stock
sudo sed -i 's#^fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40.dtb#' /boot/fnEnv.txt && sudo reboot
# MPP 模块：换回自己那份备份
sudo bash /boot/am40-apply-fixes --status     # 先看当前状态
sudo cp /boot/am40-backup-*/rk_vcodec.ko.bak /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo reboot
# mediasrv 垫片
sudo am40-fix-mediasrv --remove
```

### v6 的制作方式（可复现）

v6 **不是**从原始镜像重做的，而是在 v5 的现成镜像上"原地补文件"：

```
从 rom/fnos_1.2.0302_am40_SD-boot.img 取出 p1（360MB ext2）
  → debugfs -w 写入 DTB / fnEnv / 脚本 / payload（不碰 p2）
  → 写回镜像 p1 区域  → 得到 v6 卡启镜像
  → 同一份 p1 + 原 p2 → 重排 GPT 得到 v6 eMMC 镜像 + 瑞芯微直刷包
```
脚本：`_work/rktools/build_am40_images_v6.py`（v5 那份的 v6 版）。
所以 v6 与 v5 的**唯一差别就是 BOOT 分区的这几个文件**，rootfs 逐字节相同。



### 现在的布局与实测速度

| 位置 | 设备 | 内容 | 实测 |
|---|---|---|---|
| 系统 | SD `mmcblk1` 14.4G（"UC0C3"） | 1.2.0604，root btrfs 14G，/boot 336M | 顺序读 **20.8**、4K 读 8.0、顺序写 **9.7** MB/s |
| 存储 | eMMC `mmcblk0` 29.1G | `/vol1` md0 RAID1 + LVM ext4（空） | 顺序读 **202**、4K 读 13.1、顺序写 **103** MB/s |
| 存储 | 外置 `nvme0n1` 13.4G | `/vol2` | 写 112 MB/s |

**eMMC 比这张卡快 10 倍**（顺序读 202 vs 20.8，写 103 vs 9.7）。

### 系统放哪儿？

| | 系统在 SD（现状） | 系统在 eMMC（7×24 推荐） |
|---|---|---|
| 好处 | eMMC 29G 全部可当存储（29+13.4 都能用）；拔卡即可换系统/救砖 | 系统快 10 倍、随机 I/O 好、不怕掉卡、掉电更稳 |
| 代价 | 杂牌低速卡，随机 I/O 差（实测 iowait ≈20%），长期 7×24 有掉卡风险 | rootfs 吃满 eMMC，存储只剩 13.4G 外置盘 |
| 适合 | 折腾 / 临时 / 容量优先 | 稳定 / 性能优先 |

结论：**长期跑就搬到 eMMC**（它是空的，代价只是可用存储从 ≈42G 变 ≈13G；
真要存东西早晚要上 USB 硬盘）。**要继续卡启也行，但请换一张 A2/U3 正牌卡** ——
现在这张连 20 MB/s 都不到。

搬家的做法（**先在飞牛 UI 里删掉 `/vol1` 存储空间**，否则 md/LVM 元数据残留）：

```sh
# 1) 从 SD 上的系统整盘写 eMMC（最省事）
sudo dd if=<repo>/rom/fnos_1.2.0302_am40_eMMC.img \
        of=/dev/mmcblk0 bs=4M conv=fsync status=progress && sudo sync && sudo poweroff
#    关机拔 SD → 从 eMMC 启动 → 跑一次系统更新到 1.2.0604 → sudo am40-check
# 2) 或用瑞芯微工具直刷 rom/am40-eMMC-flash/update.img（Loader = 包里的 MiniLoaderAll.bin）
# 3) 或保持卡启，只把卡换成好卡（用 rom/fnos_1.2.0302_am40_SD-boot.img 重刷）
```

### 更新后必做（一条命令）

```sh
sudo am40-check                    # 只体检，不改任何东西
sudo bash /boot/am40-apply-fixes   # 体检 + 把缺的修复补齐（幂等，可反复跑）
```

`am40-check` 逐项核对：**启动链 md5（最关键）**、`fnEnv.txt` 的 fdtfile/kernelfile 是否存在且不重复、
**当前 fdtfile 是不是"满编 MPP + RGA 修正"那版**、5 个 AM40 DTB 的 md5、MPP 三个驱动绑定
（VDPU2/VEPU2/RKV）与 **MPP 模块版本（必须是 `custom-rk3399-full-v3`）/ 有没有每帧 iommu WARN**、
`/dev/mpp_service` 权限、**RGA（rga3 加载 + rga2 绑定 + `/dev/rga`）**、
**mediasrv 垫片（AV1 软解 / HEVC 编码回退）**、网口千兆、/boot 余量、md/温度、
mediasrv 是否在崩溃重启。全 OK 才算这次更新安全落地。

**系统更新（换内核、覆盖 `/usr/lib/modules/*/updates/trim/rk_vcodec/`）之后，再跑一次
`sudo bash /boot/am40-apply-fixes` 即可** —— 它会：
1. 按 `uname -r` 选对应内核的预编译 `rk_vcodec.ko`（没有就用 payload 里的源码现场编译）；
2. 装好 mediasrv 垫片 + systemd drop-in，并 `enable mediasrv`；
3. 把 `fnEnv.txt` 切回修正 DTB（万一被改回去）；
4. 最后跑一遍 `am40-check`，有需要重启的项会明确提示。

每一步的原始文件都备份在 `/boot/am40-backup-<时间戳>/`；`--status` 只看不改，`--dry-run` 只打印。


<details>
<summary>可选：让板子开机自愈启动链（装了就没法再用"清链"法测 eMMC 独立启动）</summary>

```sh
sudo cp <repo>/_work/am40-backup/am40-chain-32K-16M.bin /boot/am40-chain.bin
sudo tee /usr/local/sbin/am40-chain-guard >/dev/null <<'EOF'
#!/bin/bash
DEV=$(lsblk -no PKNAME "$(findmnt -no SOURCE /boot)" | head -1)
[ -b "/dev/$DEV" ] || exit 0
want=53e488ce68d573274c35730385bd2d01
got=$(dd if="/dev/$DEV" bs=512 skip=64 count=32704 status=none | md5sum | cut -d' ' -f1)
[ "$got" = "$want" ] && exit 0
logger -t am40-chain-guard "chain md5 $got != $want, restoring"
dd if=/boot/am40-chain.bin of="/dev/$DEV" bs=512 seek=64 conv=fsync status=none
EOF
sudo chmod 755 /usr/local/sbin/am40-chain-guard
sudo tee /etc/systemd/system/am40-chain-guard.service >/dev/null <<'EOF'
[Unit]
Description=AM40 boot chain guard
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/am40-chain-guard
[Install]
WantedBy=sysinit.target
EOF
sudo systemctl enable --now am40-chain-guard.service
```
</details>

---

## 2. 改了什么（就三处）

### 2.1 `/dtb/rockchip/` 新增 3 个设备树

| 文件 | 内容 | 用途 |
|---|---|---|
| `rk3399-smart-am40.dtb` | AM40 原版 DTB **逐字节原样**（md5 `3373c03f…`） | 最保守，随时退回这个 |
| `rk3399-smart-am40-gpu.dtb` | 原版 + **GPU 节点修复**（md5 `b34787af…`） | 要用飞牛自带 `midgard_kbase` 时用 |
| `rk3399-smart-am40-mpp.dtb` | 原版 + **Rockchip MPP 节点**（md5 `1eecee28…`） | **v5 起的默认**（`fnEnv.txt` 指向它），见 §5 |

> 文件名以本表为准（早期草稿里写的 `-stock.dtb` 就是现在的 `rk3399-smart-am40.dtb`，
> 已按交付镜像里的实际文件名核实）。

`rk3399-smart-am40.dtb` 相对 stock 的**唯一**改动就是 GPU 节点（已反编译逐行 diff 验证）：

```diff
 	gpu@ff9a0000 {
-		compatible = "rockchip,rk3399-mali", "arm,mali-t860";
+		compatible = "arm,mali-t860", "rockchip,rk3399-mali";
 		reg = <0x00 0xff9a0000 0x00 0x10000>;
-		interrupts = <0x00 0x14 0x04 0x00 0x00 0x15 0x04 0x00 0x00 0x13 0x04 0x00>;
-		interrupt-names = "job", "mmu", "gpu";
+		interrupts = <0x00 0x13 0x04 0x00 0x00 0x14 0x04 0x00 0x00 0x15 0x04 0x00>;
+		interrupt-names = "GPU", "JOB", "MMU";
 		clocks = <0x08 0xd0>;
+		clock-names = "clk_mali";
 		#cooling-cells = <0x02>;
 		power-domains = <0x18 0x23>;
 		operating-points-v2 = <0xab>;
 		mali-supply = <0xac>;
 		status = "okay";
 		phandle = <0x5a>;
 	};
```

要点：

- `clocks = <0x08 0xd0>` 里的 `0xd0 = 208 = ACLK_GPU`（mainline 叫 `ACLK_GPU`，
  厂商 kbase 按名字找的是 `clk_mali`，**是同一个门控**）。加上 `clock-names` 之后
  fnOS 的 `midgard_kbase` 就能自己开关 GPU 时钟，不再打印
  `no mali clock control, no need to enable.`。
- 中断号顺序跟着名字一起调了，**映射不变**（GPU=19 / JOB=20 / MMU=21），
  只是写成 kbase 优先尝试的大写形式。
- **没有用 panfrost**，走的是 fnOS 自带的 `midgard_kbase`（镜像里本来就有
  `updates/trim/rk_gpu/midgard_kbase.ko`，且镜像没有 blacklist 它）。

### 2.2 ⚠️ 顺手修掉了 `(飞牛修复GPU).dtb` 里的一个断链 bug

工作目录里那份 `dtb/rk3399-smart-am40-Kernal6.18.18(飞牛修复GPU).dtb` **本身是坏的**：
它删掉了 GPU 节点的 phandle，但热管理里还在引用它。

```
867:   cooling-device = <0x5a 0xffffffff 0xffffffff>;   ← GPU 降温映射
2377:  phandle = <0x5a>;                                 ← no_dp 有；(飞牛修复GPU) 里被删了
```

（`0x5a` 在 1939 行还出现在 `clocks = <0x08 0x168 0x08 0x5a>`，那是**时钟 ID 不是 phandle**，
容易看漏。）本镜像保留了 `phandle = <0x5a>`，所以热管理引用是完整的。已用反编译验证：

```
gpu phandle 定义: 1        cooling-device 引用: 1
```

### 2.3 `/fnEnv.txt`：`fdtfile` 指向 AM40

```diff
-fdtfile=rockchip/rk3399-firefly.dtb
+fdtfile=rockchip/rk3399-smart-am40.dtb
```

其余行（`verbosity` / `bootlogo` / `console=both` / `extraargs=cma=256M` /
`kernelfile=vmlinuz-6.18.18.c951-trim`）一字未动。

### 2.4 额外：BOOT 分区根目录放了 `AM40-NOTES.txt`

内容就是本说明的摘要，卡插到电脑上直接能看。

### 2.5 没有动的东西

内核 `6.18.18.c951-trim`、rootfs（btrfs）、`boot.scr`/`boot.cmd`、GPT 分区表、
飞牛的 trim 补丁、`/etc/fstab`（里面的 UUID 是镜像自带的，整盘 dd 后依然有效）。
**eMMC 和 /vol1–/vol4 一个字节都没碰。**

---

## 3. 刷机

### 3.1 ⚠️ 先认清设备名

```sh
lsblk -o NAME,SIZE,TYPE,TRAN,MODEL,MOUNTPOINT
```

- **本机（WSL）的根盘是 `/dev/sdd`（1007G）——千万不要写它。**
- SD 卡通过读卡器接到 Windows 的话，WSL 默认**看不到**，需要用 `usbipd` 挂进来，
  或者在 Windows 侧直接用 balenaEtcher / Rufus 刷。

### 3.2 刷

```sh
# WSL / Linux（把 sdX 换成你的卡）
sudo dd if=rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40.img \
        of=/dev/sdX bs=4M conv=fsync status=progress
sync
```

Windows：balenaEtcher「Flash from file」选这个 `.img` 即可（不用解压，本来就是 raw）。

> 镜像只有 3.58 GiB，刷到 32 GB 卡上之后**剩余空间是未分配的**，第一次启动
> 由 `resize-rootfs.service` 自动扩容（盘 >32 GB 扩到 28 GB，否则扩到卡的 99%）。
> 扩容失败的话手动：
> ```sh
> parted /dev/mmcblk1 resizepart 2 100%
> btrfs filesystem resize max /
> ```

---

## 4. 第一次启动要看什么

串口控制台和原来一样：**`ttyS2`，1500000 8N1**。

| 检查 | 命令 | 期望 |
|---|---|---|
| 起没起来 | 串口日志 | `Boot script loaded from mmc 1:1` → `fdtfile` 指向 am40 |
| DTB 对不对 | `cat /proc/device-tree/model` | `Theobroma am40` |
| GPU 时钟 | `dmesg \| grep -i mali` | 不再出现 `no mali clock control` |
| GPU 设备 | `ls /dev/mali0` | 存在 |
| SD 是不是根 | `findmnt -no SOURCE /` | `/dev/mmcblk1p2` |
| 扩容 | `df -h /` | ~28 G 或卡的 99% |
| 现有卷 | `cat /proc/mdstat`；`pvs; vgs; lvs` | 四个阵列 + 四个 VG |
| 网 | `ip -br a` | `eth0` 起来 |

**挂现有卷务必先只读**（`-o ro`）再考虑读写——新内核的 trim 补丁修订号与现役不同
（镜像 `trim_acl__594` vs 现役 `trim_acl__593`），先确认 xattr 都能正常解释。

---

## 5. MPP 实验版（`rk3399-smart-am40-mpp.dtb`）

我上一条报告里发现的：**AM40 上 Rockchip MPP 的服务驱动 `rk_vcodec.ko` 其实一直在跑**
（`/etc/modules-load.d/trim-rk_vcodec.conf` 加载的，`modinfo` 描述就是
`Rockchip mpp service driver`），用户态 `librockchip_mpp.so.0` / `librga.so.2.1.0` /
带 32 处 rkmpp 的 `libavcodec` / `jellyfin-ffmpeg` 也都在，
**唯一缺的就是 DTB 里的 MPP 节点**，所以 `/dev/mpp_service` 不存在、模块 refcnt=0 白跑。

这一版就补上这三个节点（严格照 BSP 6.1 `rk3399.dtsi` 1408–1460 行翻译）：

```dts
mpp_srv: mpp-srv {                      /* compatible = "rockchip,mpp-service" */
    rockchip,taskqueue-count  = <2>;
    rockchip,resetgroup-count = <2>;
};
vepu@ff650000  { compatible = "rockchip,vpu-encoder-v2";  /* H.264 硬编 */
                 rockchip,srv = <&mpp_srv>; rockchip,taskqueue-node = <0>; ... };
vdpu@ff650400  { compatible = "rockchip,vpu-decoder-v2";  /* H.264/VP8 硬解 */
                 rockchip,srv = <&mpp_srv>; rockchip,taskqueue-node = <0>; ... };
```

设计取舍：

- 原 `video-codec@ff650000`（`rockchip,rk3399-vpu`）**本来就没有任何驱动绑它**
  （`CONFIG_VIDEO_HANTRO is not set`），所以换成 MPP 节点是**纯增量、零损失**。
- `video-codec@ff660000` **故意不动**，保持 mainline 的 `rockchip,rk3399-vdec`
  → 主线的解码路径不受影响。
- **没有加 rkvdec（HEVC/4K）**：fnOS 的 `rk_vcodec.ko` 里根本没注册
  `rockchip,rkv-decoder-rk3399`（整个模块连 `3399` 字符串都搜不到），
  只有通用的 `rkv-decoder-v1`，风险太大，这次不碰。
- 没写 `resets`/`reset-names`：读源码确认 `mpp_reset_control_get()` 找不到时
  只打印 `No aclk reset resource define` 然后**继续 probe**（不是致命错误），
  比瞎填 reset ID 安全。
- 编译产物已验证：`mpp_srv` phandle = `0x107`，`vepu`/`vdpu` 的
  `rockchip,srv = <0x107>` 都正确指过去。

**怎么切**：编辑 BOOT 分区根目录的 `fnEnv.txt`：

```
fdtfile=rockchip/rk3399-smart-am40-mpp.dtb
```

重启后看 `ls /dev/mpp_service` 是否出现；出现就说明绑上了，再测：

```sh
ffmpeg -hwaccels | grep rkmpp
ffmpeg -c:v h264_rkmpp -i test.mp4 -f null -            # 硬解
ffmpeg -c:v h264 -i test.mp4 -c:v h264_rkmpp -y out.mp4 # 硬编
```

**这一版没有实机验证过**（我手上没有板子），所以默认不启用。出任何问题就把
`fdtfile` 改回 `rk3399-smart-am40.dtb` 即可。

---

## 6. 已知不确定项（刷之前先知道）

| # | 事项 | 说明 / 备选 |
|---|---|---|
| 1 | **SD 启动依赖 eMMC 上现役的 Armbian U-Boot** | 它的默认环境是 `boot_targets=usb0 mmc1 mmc0 nvme0 …`，`mmc1` 就是 SD，排在 eMMC 前面，所以应该会优先从 SD 起。若 U-Boot 因板级 DTB 差异**认不到 SD 卡**，串口日志里会卡在扫 mmc1。 |
| 2 | `resize-rootfs.sh` 用 `fdisk` 的交互序列改 GPT 分区 | 这段脚本是飞牛自己写的，在 GPT 上未见得百分百可靠。失败只会导致"没扩容"，不会导致起不来；手动扩容命令见 §3.2。 |
| 3 | 镜像内核缺 `CONFIG_USB_DWC3_ROCKCHIP`（现役 c1090 有） | 实测 AM40 上 `dwc3-rockchip` 驱动下没有任何设备，USB3 走的是通用 `dwc3` + `xhci`，所以**大概率无影响**；起来后 `lsusb -t` 看 Bus 04 是否还是 5000M。 |
| 4 | 版本比现役旧 | 镜像是 1.2.0302 / 内核 build 951，现役是 1.2.0604 / build 1090。起来后可以 `apt update && apt upgrade` 从 `repo.fnnas.com` 升回去。 |
| 5 | 没有 libmali | GPU 内核侧 OK（`/dev/mali0`），OpenGL 用户态依然缺（镜像和现役机器都缺）。 |
| 6 | 首次启动会扫到 /vol1–/vol4 的阵列 | 飞牛会自动组装 md/LVM。**先只读挂载确认无误再放开写。** |

**回滚**：这张卡拔掉就行，eMMC 上的飞牛完全没动，一切照旧。

---

## 7. 附：校验值

> **镜像自身的 sha256 在 `rom/SHA256SUMS-v6.txt`**（刷写前在主机上核对）。
> 这里只列"内容固定、不受本文档影响"的那些校验值，避免文档写自己的哈希这种自引用问题。

### 7.1 v6（**当前交付版**，2026-09-17）

```
[1] 卡启版      rom/fnos_1.2.0302_am40_SD-boot_v6.img          3847225344 字节
[2] eMMC 整盘版  rom/fnos_1.2.0302_am40_eMMC_v6.img            3965714432 字节
[3] eMMC 直刷包  rom/am40-eMMC-flash_v6/{update.img,boot.img,rootfs.img,uboot.img,trust.img,…}

布局（与 v5 完全相同）：
  SD 版    GPT: p1 BOOT@32MB/360MB, p2 rootfs@408MB/3253MB
  eMMC 版  GPT: p1 BOOT@16MB/512MB, p2 rootfs@528MB/3253MB
两份镜像的启动链（0x8000~0x1000000）md5 = 53e488ce68d573274c35730385bd2d01
两份镜像的 p2(rootfs) 与 v5 逐字节一致 → sha256 前缀 cb932e1e9501b15dd227780f

v6 相对 v5 的全部改动（rootfs 未动）：
  BOOT 新增   dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb   md5 21d879e3618f6f49498d629139a6d0c3
              dtb/rockchip/rk3399-smart-am40-mpp-full.dtb  md5 a916a5220aa85c7b3a7bd300ae63c813
              am40-apply-fixes        （统一修复脚本）
              am40-fixes.tar.gz       （payload，内含两份预编译模块 + 源码 + 垫片 + 脚本 + 文档）
  BOOT 更新   fnEnv.txt（fdtfile→-mpp-rga）/ am40-check.sh / AM40-NOTES.txt / AM40-刷机说明.md
  payload 内  rk_vcodec-6.18.18.c951-trim.ko   md5 29b990926f036620b2c6232cfb0ff00e
              rk_vcodec-6.18.18.c1090-trim.ko  md5 d8cfc97c21d1903b723127c7ec4745c7
  eMMC 底层   MiniLoaderAll.bin  391502 B  md5 eda42d2cd839f65967000c76391fd46d（官方 rkbin RK3399）
```

### 7.2 v5（历史版本）

```
原始镜像（未改） rom/fnos.img
  sha256 f787d669f797d9900c51f403d68060f2b0968308febe4c94c05719377d3d4d0e

v5 内核（三份交付物都由它生成）
  rom/fnos_Mainland-PE_arm_1.2.0302_firefly-rk3399_2288_am40.img
  sha256 9038244f6e845694708763b2a89ef71e7b6360240ea18861bd536b7e801bab0a   3847225344 字节

[1] 卡启版  rom/fnos_1.2.0302_am40_SD-boot.img
  sha256 9038244f6e845694708763b2a89ef71e7b6360240ea18861bd536b7e801bab0a   3847225344 字节
    GPT: p1 BOOT@32MB/360MB, p2 rootfs@408MB/3253MB

[2] eMMC 整盘版  rom/fnos_1.2.0302_am40_eMMC.img
  sha256 dcef3d3f48568cae3b0cfba097a2e4e6b82761d0274eddb445c53844d4dc358d   3965714432 字节
    GPT: p1 BOOT@16MB/512MB(PARTUUID 53a82267-7cfb-4db1-acc0-46b761ab419e)
         p2 rootfs@528MB/3253MB(PARTUUID e175b376-fd45-ff49-a91d-e7cfe90ee7f0)
    0x8000~0x1000000 与 eMMC 备份逐字节一致；p1/p2 与 v5 逐字节一致

[3] eMMC 直刷包  rom/am40-eMMC-flash/
  MiniLoaderAll.bin 391502 B  md5 eda42d2cd839f65967000c76391fd46d  (官方 rkbin RK3399)
  parameter.txt     381 B
  uboot.img         4194304 B   与 eMMC@8MB  逐字节一致
  trust.img         4194304 B   与 eMMC@12MB 逐字节一致
  boot.img          377487360 B 与 v5 p1 逐字节一致
  rootfs.img        3411017728 B 与 v5 p2 逐字节一致
  update.img        3797684696 B sha256 e42a921d70f79112aa00e9dffa3df18c694cf08cf68883e1d67342834f584481
                    RKFW 末尾 MD5 自校验通过，包内 7 项已回读比对一致
```

三个 DTB 的 md5（**已从最终镜像里回读比对，逐字节一致**，也与更新后的机器上 `/boot/dtb/rockchip/` 实测一致）：

| 镜像内文件 | md5 | 说明 |
|---|---|---|
| `rk3399-smart-am40.dtb` | `3373c03f7a971d98b7ea1584906567ee` | 原版逐字节原样（最保守） |
| `rk3399-smart-am40-gpu.dtb` | `b34787af0c8f2de9dab4172d04d48625` | 原版 + GPU 节点修复 |
| `rk3399-smart-am40-mpp.dtb` | `1eecee2862120de4bf4bb63ce83fc32c` | 原版 + MPP 节点（**v5 起默认**） |

启动链（`0x8000~0x1000000`，16,744,448 字节）的 md5 —— 三份交付物 + 备份文件**全部一致**：

```
53e488ce68d573274c35730385bd2d01   am40-chain-32K-16M.bin / SD-boot.img / eMMC.img / 直刷包
```

自检命令里的期望值就是它：`sudo am40-check`（见 §1.10）。

源 `.dts` 与工具都在 `_work/dts_am40/`：

```
rk3399-smart-am40-gpu.dts / .dtb      GPU 修复版源码与产物（b34787af…）
rk3399-smart-am40-mpp.dts / .dtb      MPP 版源码与产物（1eecee28…）
rk3399-smart-am40-stock.dtb           原版逐字节原样（3373c03f…，从厂商镜像/机器上取回）
rK3399-smart-am40-*.dts               4 个原始 DTB 的反编译文本（含 diff 对照）
verify-*.dts                          反编译回来做逐行校验的中间产物
```

---

> 本文档描述的所有修复（分层定位、补丁、脚本与实测）是在
> [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`@deepseek-ai/dsh`）协助下完成的。
