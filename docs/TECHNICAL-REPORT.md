# AM40（RK3399）飞牛镜像分层修复技术报告

| | |
|---|---|
| 适用硬件 | SMART-AM40（`Theobroma am40`）：RK3399（2×A72+4×A53）、4GB LPDDR3、32GB eMMC、无 SATA |
| 适用系统 | 飞牛 fnOS `1.2.0302`（内核 `6.18.18.c951-trim`）→ 系统更新后 `1.2.0604`（`6.18.18.c1090-trim`） |
| 报告日期 | 2026-09-17 |
| 范围 | 让飞牛自带的**硬件编解码链路与 2D 加速链路**在这块板子上可用、且性能对齐厂商预期 |
| 交付形态 | v6 镜像（只烘焙设备树）+ 统一修复脚本 `am40-apply-fixes` + payload |
| 分析工具 | [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)（`@deepseek-ai/dsh`）—— 逐层证据链梳理、补丁生成与回归验证、脚本与文档编写、实机实测 |
| 本文档在仓库中的位置 | `docs/TECHNICAL-REPORT.md`。镜像内暂未包含本文档（以保持已验证的镜像 sha256 不变） |

---

## 0. 结论摘要

飞牛为 ARM 板卡发的这套系统里，**设备树、内核模块、用户态库是三个来源不同的组件**。RK3399 上出现的所有问题，本质都能归到下面四种"不认识"之一：

| # | 失配形态 | 具体表现 | 修复层 |
|---|---|---|---|
| A | **compatible 不匹配**——DTB 写的是上游 mainline 的 compatible，而飞牛只发了 vendor 版模块 | RGA 节点没人认领 → 没有 `/dev/rga` → `librga` 崩 → mediasrv SEGV | 第 2 章 DTB |
| B | **DTB 缺少 vendor 绑定要素**——vendor MPP 需要 `srv/taskqueue/resetgroup`，mainline 节点没有 | RK3399 的 HEVC/VP9/4K 解码器（ff660000）根本没被 MPP 绑定 | 第 2 章 DTB |
| C | **模块编译期裁剪**——飞牛的 `rk_vcodec.ko` 没开 `CONFIG_CPU_RK3399`；且源码与运行内核存在 API 漂移 | RKV 解码器缺失；每帧一次内核 WARN 把吞吐砍半 | 第 3 章 内核模块 |
| D | **硬件能力不存在，但上层仍去请求**——RK3399 无 AV1 解码、无 HEVC 编码，而 mediasrv 会无条件请求 | 播放报 `errno 4096` / `AVERROR_EXTERNAL` | 第 4 章 用户态拦截 |

各层修复后的实测（1080p 合成片，同一 DTB、同一 295 MHz 时钟，**无超频**）：

| 项目 | 修复前 | 修复后 | 参照（厂商原版模块） |
|---|---|---|---|
| H.264 解码 | 1.37x | **10.8x** | 3.33x |
| H.264 编码 | 0.66x | **1.22x** | 1.25x |
| H.265 8bit / 10bit 解码 | 不可用 | **9.09x / 7.18x** | 不可用 |
| H.265 4K 解码 | 不可用 | **2.52x** | 不可用 |
| VP9-10bit 解码 | 不可用 | **3.66x** | 不可用 |
| mediasrv 播 AV1 10bit + 烧 ASS 字幕 | 报错不可播 | **0.80x（720p 档 1.07x 实时）** | 报错不可播 |
| dmesg 噪声（300 帧编+解码一遍） | 2588 行 / 287 条 WARN | **0 行** | 0 行 |

---

## 1. 问题模型

### 1.1 系统里的四套组件与它们各自的"出身"

| 组件 | 路径 | 出身 | 关键特征 |
|---|---|---|---|
| 设备树 | `/boot/dtb/rockchip/rk3399-smart-am40*.dtb` | 上游 mainline `rk3399.dtsi` + 板厂改动 | RGA/VPU 节点用 mainline compatible，**没有** vendor MPP 的绑定属性 |
| 内核编解码模块 | `/lib/modules/<ver>/updates/trim/rk_vcodec/rk_vcodec.ko` | Rockchip vendor MPP（`nyanmisaka` 分支，版本串 `a9380ef 2025-12-26`） | 编译时未开 `CONFIG_CPU_RK3399` → RKV 匹配表被裁掉 |
| 内核 2D 模块 | 同目录 `rga3.ko` | vendor multi-RGA | `of_match` 只认 `rockchip,rga2` / `rockchip,rga3*` |
| 用户态 | `/usr/trim/lib/mediasrv/lib/{librockchip_mpp.so.1, librga.so.2, libavcodec.so.62}` + `/usr/trim/bin/mediasrv` | 飞牛自带（ffmpeg `8.1.1-mediasrv`，`--enable-rkmpp --enable-rkrga`） | 解码器选择走 `av_codec_iterate()`，不看名字表 |

### 1.2 三套组件之间的"接口契约"

```
              ┌──────────────────────────────────────────────┐
   DTB ──────►│ compatible 必须命中 driver.of_match_table    │◄── A / B 两类失配在这里
              │ 且必须提供 vendor 绑定属性(srv/taskqueue/...) │
              └──────────────────────────────────────────────┘
                                   │ probe 成功
                                   ▼
              ┌──────────────────────────────────────────────┐
   内核模块 ──►│ 注册 /dev/mpp_service + /dev/rga             │◄── C 类失配(编译期裁剪/API 漂移)
              │ 通过 ioctl 暴露能力表 (/proc/mpp_service/*)   │
              └──────────────────────────────────────────────┘
                                   │ ioctl
                                   ▼
              ┌──────────────────────────────────────────────┐
   用户态 ────►│ librockchip_mpp / librga / libavcodec(rkmpp) │◄── D 类失配(硬件能力不存在)
              │ mediasrv 按"名字后缀带 rkmpp"挑硬件编解码器    │
              └──────────────────────────────────────────────┘
```

关键点：**这三层之间没有任何一层会优雅降级**。DTB 匹配失败 → 模块 init 直接 `-EFAULT` 退出；用户态请求不存在的编解码器 → `AVERROR_EXTERNAL` 直接报错，不会回退软解。所以每一层都必须显式修。

### 1.3 修复的责任划分

| 层 | 修什么 | 为什么在这一层修 | 载体 |
|---|---|---|---|
| DTB | compatible + vendor 绑定属性 | 这是唯一"必须开机前就位"的一层，运行期无法补节点（`CONFIG_OF_OVERLAY` 未开） | 预编译 DTB（镜像烘焙） |
| 内核模块 | 重编 + 源码补丁 | 只有模块能补上被裁掉的 RKV 支持、以及消除每帧 WARN | payload 里的预编译 `.ko` + 源码 |
| 用户态 | LD_PRELOAD 垫片 | 飞牛二进制不可改；垫片只拦 4 个符号，外科手术式 | payload 里的 `.c` / `.so` |
| 服务/配置 | systemd drop-in + enable + udev | 不改原 unit、不改全局配置，保持可回滚 | 脚本落地 |

---

## 2. 第一层：设备树绑定

### 2.1 目标状态

RK3399 的编解码硬件分三块，修复后分别由 **vendor MPP** 统一接管（与官方 6.1 BSP 的绑定方式一致）：

| 硬件块 | 地址 | 能力 | 绑定到 |
|---|---|---|---|
| VEPU2 | `ff650000` | H.264 / MJPEG 编码 | `mpp_vepu2` |
| VDPU2 | `ff650400` | H.264 / MPEG1/2/4 / H.263 / MJPEG 解码 | `mpp_vdpu2` |
| RKV | `ff660000` | H.265 / VP9 / 4K 解码 | `mpp_rkvdec` |
| RGA2 | `ff680000` | 2D 缩放/合成/格式转换 | `rga2`（vendor multi-RGA） |

同时把 mainline 的 `video-codec@ff660000`（`rockchip,rk3399-vdec`，stateless/Request-API 设备）设为 `status = "disabled"`，避免两个驱动抢同一个地址。

### 2.2 RGA 节点：一个属性决定了 mediasrv 的生死

**故障链**（全部可由日志验证）：

```text
DTB: rga@ff680000 compatible = "rockchip,rk3399-rga"     ← 上游 mainline 写法
  │
  ├─ 飞牛内核 CONFIG_VIDEO_ROCKCHIP_RGA is not set（没有 mainline 版 RGA 驱动）
  ├─ 飞牛只发 vendor 版 rga3.ko，其 of_match 只有：
  │     rockchip,rga2 / rockchip,rga2_core0 / rockchip,rga3 / rockchip,rga3_core0 / rockchip,rga3_core1
  │     （`modinfo -F alias` 为空，连 autoload 别名都没有）
  ▼
rga3 按模块名能 insmod，但两个 platform_driver 都匹配不上 → rga_drvdata->num_of_scheduler = 0
  ▼
rga_drv.c: rga_iommu_bind() 里三个索引全为 -1
     dmesg:  rga_iommu: rga_iommu_bind, binding map scheduler failed!
             rga: rga iommu bind failed!
     modprobe: ERROR: could not insert 'rga3': Bad address
  ▼
没有 /dev/rga → librga.so.2.1.0 一调用就 SEGV（DABT level 0 translation fault）
  ▼
mediasrv status=11/SEGV，每 5 秒重启一次
```

**参考实现**：`kernel-develop-6.1/arch/arm64/boot/dts/rockchip/rk3399-linux.dtsi`（官方 BSP）与 4.4 的
`rk3399-firefly-android.dts` 都写 `compatible = "rockchip,rga2"`，且 `rockchip_linux_defconfig` 里开的正是
`CONFIG_ROCKCHIP_MULTI_RGA=y`（就是编出这个 `rga3.ko` 的开关）。**是这份 DTB 抄错了。**

**patch**（单属性，可运行期用 `fdtput` 完成 —— 脚本里的自愈路径就是它）：

```sh
# 方式 1：就地改（板子上有 fdtput，本修复的自愈路径）
fdtput -t s /boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb \
        /rga@ff680000 compatible "rockchip,rga2"

# 方式 2：从源码编（本报告交付的 DTB 就是这么来的，见 §2.6）
dtc -I dts -O dtb -o rk3399-smart-am40-mpp-rga.dtb rk3399-smart-am40-mpp-rga.dts
```

> 注：该节点保留 mainline 的 `clock-names = "aclk","hclk","sclk"` 与三路 `resets` 即可。
> vendor 驱动的时钟获取用的是 `devm_clk_bulk_get_all()`（不看名字），所以不需要改成
> BSP 的 `aclk_rga/hclk_rga/clk_rga`。

### 2.3 VPU / RKV 节点：vendor MPP 需要的"五件套"

vendor MPP 的每个子驱动（`mpp_vdpu2.c` / `mpp_vepu2.c` / `mpp_rkvdec.c`）在 probe 时要求：

1. `compatible` 命中各自 `of_match_table`；
2. `rockchip,srv = <&mpp_srv>`（指到共享的 MPP 服务设备）；
3. `rockchip,taskqueue-node`（硬件队列号）；
4. `rockchip,resetgroup-node`（复位组号）；
5. `iommus` + `power-domains`。

再加一个 `mpp-srv` 容器节点提供 taskqueue/resetgroup 资源池。**mainline 的 `video-codec@ff650000`
（`rockchip,rk3399-vpu`）这些属性一个都没有**，所以原版 DTB 下 MPP 根本没有可绑定的设备。

### 2.4 为什么不用运行期方案

| 方案 | 结论 |
|---|---|
| device-tree overlay（configfs） | ✗ `CONFIG_OF_OVERLAY is not set`，`/sys/kernel/config/device-tree/overlays` 不存在 |
| 运行期改 compatible | ✗ 只能通过 overlay |
| 只加单个属性 | ✗ `fdtput` 无法创建"带 phandle 的多 cell 属性 + 整棵节点"（`rockchip,srv`、`resets`、`clocks` 都需要 phandle），必须预编译 DTB |

结论：**vendor 节点必须作为预编译 DTB 交付**；只有 RGA 那一个属性是脚本可以在运行期自愈的。

### 2.5 交付与校验

```sh
# 交付文件
/boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb    md5 21d879e3618f6f49498d629139a6d0c3  ← fnEnv 指向它
/boot/dtb/rockchip/rk3399-smart-am40-mpp-full.dtb   md5 a916a5220aa85c7b3a7bd300ae63c813  ← 无 RGA 修正，对照用
/boot/fnEnv.txt                                     fdtfile=rockchip/rk3399-smart-am40-mpp-rga.dtb

# 校验
fdtget -t s /boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb /rga@ff680000 compatible
for d in mpp_vepu2 mpp_vdpu2 mpp_rkvdec rga2; do
  printf '%-12s ' $d; ls /sys/bus/platform/drivers/$d/ | grep -vE '^(module|bind|unbind|uevent)$'
done
# 期望：ff650000.vepu / ff650400.vdpu / ff660000.rkvdec / ff680000.rga
tr '\0' ' ' < /proc/device-tree/rga@ff680000/compatible   # 运行中的 DT
```

### 2.6 完整节点定义（交付 DTB 里的实际内容）

```dts
	mpp_srv: mpp-srv {
		compatible = "rockchip,mpp-service";
		rockchip,taskqueue-count = <0x02>;
		rockchip,resetgroup-count = <0x02>;
		status = "okay";
	};

	vepu@ff650000 {                                   /* H.264 / MJPEG 编码 */
		compatible = "rockchip,vpu-encoder-v2";
		reg = <0x00 0xff650000 0x00 0x400>;
		interrupts = <0x00 0x72 0x04 0x00>;   /* GIC_SPI 114 */
		interrupt-names = "irq_enc";
		clocks = <&cru ACLK_VCODEC>, <&cru HCLK_VCODEC>;
		clock-names = "aclk_vcodec", "hclk_vcodec";
		rockchip,normal-rates   = <300000000 0>;
		rockchip,advanced-rates = <300000000 0>;
		iommus = <&vpu_mmu>;
		power-domains = <&power RK3399_PD_VCODEC>;
		rockchip,srv = <&mpp_srv>;
		rockchip,taskqueue-node  = <0>;
		rockchip,resetgroup-node = <0>;
		status = "okay";
	};

	vdpu@ff650400 {                                   /* H.264 / MPEG / H.263 / MJPEG 解码 */
		compatible = "rockchip,vpu-decoder-v2";
		reg = <0x00 0xff650400 0x00 0x400>;
		interrupts = <0x00 0x71 0x04 0x00>;   /* GIC_SPI 113 */
		interrupt-names = "irq_dec";
		clocks = <&cru ACLK_VCODEC>, <&cru HCLK_VCODEC>;
		clock-names = "aclk_vcodec", "hclk_vcodec";
		rockchip,normal-rates   = <300000000 0>;
		rockchip,advanced-rates = <300000000 0>;
		iommus = <&vpu_mmu>;
		power-domains = <&power RK3399_PD_VCODEC>;
		rockchip,srv = <&mpp_srv>;
		rockchip,taskqueue-node  = <0>;
		rockchip,resetgroup-node = <0>;
		status = "okay";
	};

	rkvdec@ff660000 {                                 /* H.265 / VP9 / 4K 解码 */
		compatible = "rockchip,rkv-decoder-rk3399";
		reg = <0x00 0xff660000 0x00 0x400>;
		interrupts = <0x00 0x74 0x04 0x00>;   /* GIC_SPI 116 */
		interrupt-names = "irq_dec";
		clocks = <&cru ACLK_VCODEC>, <&cru HCLK_VCODEC>,
			 <&cru ACLK_VDU>,    <&cru HCLK_VDU>;
		clock-names = "aclk_vcodec", "hclk_vcodec", "clk_cabac", "clk_core";
		rockchip,normal-rates   = <300000000 0 300000000 300000000>;
		rockchip,advanced-rates = <300000000 0 300000000 300000000>;
		resets = <&cru SRST_H_VDU>, <&cru SRST_A_VDU>, <&cru SRST_H_VDU_NOC>,
			 <&cru SRST_A_VDU_NOC>, <&cru SRST_VDU_CA>, <&cru SRST_VDU_CORE>;
		reset-names = "video_h", "video_a", "niu_h", "niu_a", "video_cabac", "video_core";
		iommus = <&vdec_mmu>;                 /* iommu@ff660480，与 VPU 那个是分开的两块 */
		power-domains = <&power RK3399_PD_VDU>;   /* 上游编号 32，与 VPU 的 PD_VCODEC(31) 不同 */
		rockchip,srv = <&mpp_srv>;
		rockchip,taskqueue-node  = <1>;       /* RKV 用独立队列/复位组 */
		rockchip,resetgroup-node = <1>;
		status = "okay";
	};

	rga@ff680000 {                                    /* 2D */
		compatible = "rockchip,rga2";         /* ★ 本层唯一的属性级修正 */
		reg = <0x00 0xff680000 0x00 0x10000>;
		interrupts = <0x00 0x37 0x04 0x00>;   /* GIC_SPI 55 */
		clocks = <&cru ACLK_RGA>, <&cru HCLK_RGA>, <&cru SCLK_RGA_CORE>;
		clock-names = "aclk", "hclk", "sclk";
		resets = <&cru SRST_RGA_CORE>, <&cru SRST_A_RGA>, <&cru SRST_H_RGA>;
		reset-names = "core", "axi", "ahb";
		power-domains = <&power RK3399_PD_RGA>;
	};

	/* mainline 的两个设备必须关掉，否则与上面的 vendor 节点抢同一块地址 */
	video-codec@ff660000 { status = "disabled"; };   /* rockchip,rk3399-vdec */
	vpu@ff650000         { status = "disabled"; };   /* rockchip,rk3399-vpu  */
```

### 2.7 关于 `rockchip,normal-rates`（时钟）—— 结论：**不要超频**

MPP 驱动按 `clock-names` 的下标读这个数组（`mpp_common.c: mpp_get_clk_info()`），
NORMAL 模式下取 `normal_rate_hz`，取不到则回落到各驱动编译期默认值（VDPU2/VEPU2 都是 **300 MHz**）。
用 `/proc/mpp_service/vepu/aclk`（= `mpp_clk_info.debug_rate_hz` 运行期覆盖）做扫频实测：

| `aclk_vcodec` | 1080p H.264 编码 | 备注 |
|---|---|---|
| 297 MHz（厂商默认） | 0.66x → **修复后 1.22x** | 修复后此档即为厂商原版水平 |
| 400 MHz | 0.76x | |
| 594 MHz | 0.89x | |
| 800 MHz | 1.23x | 用 2.7 倍功耗买回"本来就该有的速度" |
| 1000 MHz+ | **0.03x** | 直接崩性能，禁止 |

**因此交付的 DTB 保持 300 MHz**。性能问题的真正解法在第 3.4 节的 WARN 补丁，而不是提频。
（反之：解码速度对此时钟**完全不敏感**，实测 1.36~1.37x 四档不变，说明解码瓶颈不在 VPU 时钟。）

---

## 3. 第二层：内核模块替换

### 3.1 为什么飞牛自带的 `rk_vcodec.ko` 不够

```console
$ strings /boot/rk_vcodec.ko.orig | grep -oE 'rockchip,rkv-decoder-[a-z0-9-]+' | sort -u
rockchip,rkv-decoder-rk3528
rockchip,rkv-decoder-rk3562
rockchip,rkv-decoder-rk3568
rockchip,rkv-decoder-rk3576
rockchip,rkv-decoder-v1
rockchip,rkv-decoder-v2
rockchip,rkv-decoder-v2-ccu          ← 没有 rk3399

$ strings /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko \
      | grep -c 'rockchip,rkv-decoder-rk3399'
1                                    ← 重编后有了
```

源码依据：`mpp_rkvdec.c` 里 rk3399 那条匹配被 `#ifdef CONFIG_CPU_RK3399` 包着，飞牛编译时没开
（所以 `rk_vcodec.ko` 里连这个字符串都不存在）。绑定结果：

```text
mpp_vdpu2 → ff650400.vdpu   ✓（H.264 / MPEG / H.263 / MJPEG 解码可用）
mpp_vepu2 → ff650000.vepu   ✓（H.264 / MJPEG 编码可用）
mpp_rkvdec → （无）          ✗ → HEVC / VP9 / 4K 全部只能软解
```

关键便利条件：**MPP 是可加载模块**（`/lib/modules/<ver>/updates/trim/rk_vcodec/rk_vcodec.ko`），
所以不需要重编内核，只重编这一个模块。

### 3.2 重编配方

源码：`kernel-develop-6.1/drivers/video/rockchip/mpp/`（与飞牛用户态库同源同版本，版本串
`a9380ef author: nyanmisaka 2025-12-26`）。

```make
# Makefile（out-of-tree 构建，板子上原生 gcc + 内核头文件）
obj-m += rk_vcodec.o
rk_vcodec-objs := mpp_service.o mpp_common.o mpp_iommu.o \
                  mpp_rkvdec.o mpp_vdpu2.o mpp_vepu2.o mpp_jpgdec.o mpp_jpgenc.o

ccflags-y += -DCONFIG_CPU_RK3399 \
             -DCONFIG_ROCKCHIP_MPP_RKVDEC -DCONFIG_ROCKCHIP_MPP_VDPU2 \
             -DCONFIG_ROCKCHIP_MPP_VEPU2  -DCONFIG_ROCKCHIP_MPP_JPGDEC \
             -DCONFIG_ROCKCHIP_MPP_JPGENC -DCONFIG_ROCKCHIP_MPP_PROC_FS \
             -I$(src) -I$(src)/hack
CFLAGS_mpp_service.o += -DMPP_VERSION="\"custom-rk3399-full-v3\""
```

```sh
# 构建 / 安装 / 热重载
make -C /usr/src/linux-headers-$(uname -r) M=$PWD modules \
     KCFLAGS="-Wno-error=incompatible-pointer-types"
sudo systemctl stop mediasrv
sudo cp rk_vcodec.ko /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo rmmod rk_vcodec && sudo insmod <上面那个路径>
sudo systemctl start mediasrv
cat /proc/mpp_service/version        # 期望 custom-rk3399-full-v3
```

### 3.3 6.18 内核 API 漂移清单（从 vendor 6.1 源码迁到 6.18 所需的全部改动）

| # | 文件 | vendor 原写法 | 6.18 写法 |
|---|---|---|---|
| 1 | `mpp_service.c:416` | `class_create(THIS_MODULE, MPP_CLASS_NAME)` | `class_create(MPP_CLASS_NAME)` |
| 2 | `mpp_service.c:546` | `MODULE_IMPORT_NS(DMA_BUF)` | `MODULE_IMPORT_NS("DMA_BUF")` |
| 3 | `mpp_common.c:1558,1579,1582` | `f->f.file` / `struct file *f` 字段直取 | `fd_file(f)` |
| 4 | 各 `*_remove()` 与 `.remove =` | `int (*remove)(struct platform_device *)` | 6.18 为 `void`；统一用 `KCFLAGS=-Wno-error=incompatible-pointer-types` 放行 |
| 5 | `mpp_iommu.c` | `#include <asm/dma-iommu.h>` | 该头在 arm64 6.18 已不存在（`dma_iommu_mapping` 相关代码本就被 `#ifdef CONFIG_ARM_DMA_USE_IOMMU` 排除），删掉 include |
| 6 | 头文件依赖 | 依赖 BSP 内核里的 vendor 头 | 需要补 5 个：`uapi/linux/rk-mpp.h`、`linux/dma-buf-cache.h`、`linux/rockchip/rockchip_sip.h`（**加 include guard**）、`soc/rockchip/rockchip_sip.h`（改成 wrapper）、`soc/rockchip/rockchip_opp_select.h` |

> 第 6 项对**换内核后现场重编**很关键：`linux/rockchip/rockchip_sip.h` 与
> `soc/rockchip/rockchip_sip.h` 内容重叠会触发 `redeclaration of enumerator` 编译错误，
> 交付 payload 里为此专门带了独立的 `mpp-src/`（已含 guard 与 wrapper）。

### 3.4 ★ 性能缺陷：每帧一次的内核 WARN（吞吐减半的真凶）

**代码位置**：`drivers/video/rockchip/mpp/mpp_iommu.c` → `mpp_iommu_dev_activate()`

```c
} else {
	info->dev_active = dev;
	/* switch domain pagefault handler and arg depending on device */
	iommu_set_fault_handler(info->domain, dev->fault_handler ?
				dev->fault_handler : mpp_iommu_handle, dev);   /* ← 这里 */
	dev_dbg(info->dev, "activate -> %p %s\n", dev, dev_name(dev->dev));
}
```

**机理**：这个函数**每次给任务激活设备都调用一次**（等于每帧一次）；而 6.18 起
`iommu_set_fault_handler()` 已废弃 —— `domain->ops->set_fault_handler` 为 NULL，
函数入口就是 `WARN_ON`，于是每帧打印一次完整调用栈，其中还包含一行约 1.5 KB 的
`Modules linked in: …`。

```text
# 实测（修复前，300 帧）
dmesg | grep -c iommu_set_fault_handler   →  287
# 因为 fnEnv.txt 里是 console=both，这些 printk 同时输出到串口(1.5 Mbps)+HDMI 控制台，
# 单帧开销比编码本身还大。
```

**patch**（`am40-patch-iommu.py`，交付号 `v3`）：

```python
old = """		info->dev_active = dev;
		/* switch domain pagefault handler and arg depending on device */
		iommu_set_fault_handler(info->domain, dev->fault_handler ?
					dev->fault_handler : mpp_iommu_handle, dev);
"""
new = """		info->dev_active = dev;
		/*
		 * AM40 patch: 6.18 起 iommu_set_fault_handler() 已废弃 ——
		 * domain->ops->set_fault_handler 为 NULL，每调用一次就 WARN_ON 并
		 * 打印整页调用栈（console=both 时同时刷串口+tty1）。这个函数是
		 * "每次激活设备"都调一次的，实测等于每帧一次。
		 * 6.18 的 rockchip-iommu 自己会在中断里报 page fault，这里不再注册。
		 */
#if 0
		iommu_set_fault_handler(info->domain, dev->fault_handler ?
					dev->fault_handler : mpp_iommu_handle, dev);
#endif
"""
s = s.replace(old, new)
s = s.replace("static int mpp_iommu_handle(struct iommu_domain *iommu,",
              "static int __maybe_unused mpp_iommu_handle(struct iommu_domain *iommu,")
```

**验证**（不重编也能证伪/证实 —— 只关控制台输出即可）：

```sh
sudo dmesg -n 1     # 只放 emergency 到控制台
#   编码 600 帧: 0.66x → 1.17x      解码: 1.37x → 9.2x
sudo dmesg -n 7
```

修复后 dmesg 在 300 帧编码 + 一遍解码后为 **0 行**。

> 上游印证：用户提供的 `mpp-develop`（MPP 1.1.0，新一代 `kmpp/` 内核模块）里
> `fault_handler` / `iommu_set_fault_handler` **一个引用都没有** —— 官方同样是把它删掉了。

### 3.5 版本耦合与加载约束（决定交付形态）

| 约束 | 实测结论 | 对交付的影响 |
|---|---|---|
| vermagic | 必须与 `uname -r` 完全一致（`6.18.18.c951-trim SMP mod_unload aarch64`） | 必须**按内核版本分别预编译**；镜像自带 c951、更新后是 c1090 |
| `CONFIG_MODVERSIONS` | **未开**（c951/c1090 都是） | 不需要 `Module.symvers` 做符号 CRC 校验，降低了构建难度 |
| 头文件 | 镜像自带 `/usr/src/linux-headers-<ver>`（含 `Module.symvers`）与 `aarch64-linux-gnu-gcc-12` | **可在目标板现场重编**（换内核后的兜底路径） |
| 头文件完整度 | c951 头文件树缺 4 个 vendor 头 | payload 的 `mpp-src/` 自带所需头，构建前需补进 headers 树 |

---

## 4. 第三层：用户态 mediasrv（编解码器选择拦截）

### 4.1 mediasrv 选解码器的机制（决定了 patch 点）

```text
# 证据 1：导入符号（readelf --dyn-syms /usr/trim/bin/mediasrv）
av_codec_iterate  avcodec_find_decoder  avcodec_find_encoder_by_name  avcodec_open2
→ 没有 avcodec_find_decoder_by_name；也没有对 av1_rkmpp 的直接引用

# 证据 2：实际调用序列（垫片日志）
[shim] find_decoder(id=225) -> libdav1d          ← 遍历/查找用 avcodec_find_decoder
[shim] open2 codec=av1_rkmpp(id=225)             ← 但最终拿去 open 的是 rkmpp 那个
```

结论：mediasrv **用 `av_codec_iterate()` 遍历全部解码器、按"名字带 rkmpp"挑硬件解码器**，
所以：

* 二进制里连 `av1_rkmpp` 字符串都没有（名字由硬件后端名拼出）；
* 在 `avcodec_find_decoder_by_name()` 里做手脚**没用**（它根本不被调用）。

### 4.2 两个硬件能力缺口

| 缺口 | 触发条件 | 现象（journalctl -u mediasrv 原文） |
|---|---|---|
| **AV1 解码** | 播任何 AV1 片 | `mpp: unable to create dec av1 for soc rk3399 unsupported` → `[av1_rkmpp] Failed to init MPP context: -1` → `AVERROR_EXTERNAL` → `init_video_stream_map error, errcode 4096` |
| **HEVC 编码** | 浏览器支持 HEVC（Edge/Chrome 装了扩展）→ 前端发 `"videoEncoder":"hevc"` | `mpp: unable to create enc h265 for soc rk3399 unsupported` → `[hevc_rkmpp] Failed to init MPP context: -1` |

要点：**第二个跟源文件是不是 AV1 无关**。任何需要转码的片（例如 AV1/HEVC 源 + ASS 字幕要烧进去）
在这个浏览器上都会撞到 —— 因为 RK3399 的 VEPU2 只能编 H.264/MJPEG。

### 4.3 为什么不能在 `avcodec_open2` 里偷换解码器

第一版垫片试过"在 `open2` 里把 `av1_rkmpp` 换成 `libdav1d`"，被 ffmpeg 8 直接拒绝：

```text
[av1_rkmpp @ 0x…] This AVCodecContext was allocated for av1_rkmpp, but libdav1d passed to avcodec_open2()
```

ffmpeg 8 会校验"分配上下文时用的 codec"与"open 时传入的 codec"是否一致。
**必须在 `avcodec_alloc_context3()` 之前就让 mediasrv 选对**，所以唯一的切入点是
`av_codec_iterate()`。

### 4.4 垫片设计（`mediasrv-shim.c`，约 250 行，只依赖 libc + libdl）

| 钩子 | 动作 | 目的 |
|---|---|---|
| `av_codec_iterate()` | 跳过 `av1_rkmpp` / `vp8_rkmpp` | 让 mediasrv 遍历时就看不到这两个硬解 → 自己走 `libdav1d` / `libvpx` |
| `avcodec_find_encoder_by_name()` | 凡是 `*_rkmpp` 且不是 `h264_rkmpp`/`mjpeg_rkmpp` 的（即 `hevc_rkmpp`/`av1_rkmpp`/`vp9_rkmpp`…）改写成 `h264_rkmpp` | 硬编能力缺失时降级到唯一可用的硬编 |
| `avcodec_open2()` / `avcodec_find_decoder()` / `avcodec_find_decoder_by_name()` | 只记日志到 `/tmp/mediasrv-shim.log` | 事后可审计真实调用链 |

核心代码（三个钩子的关键部分）：

```c
/* AVCodec 头部布局（ffmpeg 公共 ABI）: +0 name, +8 long_name, +16 type, +20 id */
static const char *c_name(const AVCodec *c) { return c ? *(const char *const *)c : "(null)"; }
static int c_id(const AVCodec *c) { return c ? *(const int *)((const char *)c + 20) : -1; }

/* 1) RK3399 没有的硬解：请求进来就换成软件解码器 */
static const char *sw_fallback(const char *name) {
	if (!name) return NULL;
	if (!strcmp(name, "av1_rkmpp")) return "libdav1d";
	if (!strcmp(name, "vp8_rkmpp")) return "libvpx";
	return NULL;
}

/* 2) RK3399 的 VEPU2 只能编 H.264(+MJPEG)，其余 *_rkmpp 硬编一律回退 h264_rkmpp */
static const char *enc_remap(const char *name) {
	if (!name) return NULL;
	if (!strcmp(name, "h264_rkmpp") || !strcmp(name, "mjpeg_rkmpp")) return NULL;
	if (strlen(name) > 6 && !strcmp(name + strlen(name) - 6, "_rkmpp")) return "h264_rkmpp";
	return NULL;
}

/* 3) 关键：在遍历阶段就藏掉 —— 必须在 alloc_context3 之前生效 */
const AVCodec *av_codec_iterate(void **opaque) {
	const AVCodec *c;
	if (!real_iterate) real_iterate = dlsym(RTLD_NEXT, "av_codec_iterate");
	if (!real_iterate) return NULL;
	for (;;) {
		c = real_iterate(opaque);
		if (!c) return NULL;
		if (sw_fallback(c_name(c))) {
			lg("[shim] iterate: 对 mediasrv 隐藏 %s（RK3399 无此硬解）\n", c_name(c));
			continue;
		}
		return c;
	}
}

const AVCodec *avcodec_find_encoder_by_name(const char *name) {
	const char *alt = enc_remap(name);
	if (!real_find_enc_by_name) real_find_enc_by_name = dlsym(RTLD_NEXT, "avcodec_find_encoder_by_name");
	if (alt) {
		lg("[shim] find_encoder_by_name(\"%s\") -> RK3399 无此硬编，改用 \"%s\"\n", name, alt);
		return real_find_enc_by_name(alt);
	}
	return real_find_enc_by_name(name);
}
```

垫片对 ffmpeg 版本不敏感（按**符号名**拦截），所以飞牛 `1.2.0302` 的旧 ffmpeg 与
`1.2.0604` 的新 ffmpeg 都适用 —— 两边的 mediasrv 都导入这同一组符号。

### 4.5 部署

```ini
# /etc/systemd/system/mediasrv.service.d/10-am40-shim.conf
[Service]
Environment=LD_PRELOAD=/usr/local/lib/mediasrv-shim.so
ExecStart=
ExecStart=/usr/trim/bin/mediasrv -o /usr/trim/logs/mediasrv.log -a /var/run/mediasrv.socket -l debug
```

```sh
# 编译（镜像自带 gcc，优先现场编译以匹配本机 glibc）
gcc -shared -fPIC -O2 -o /usr/local/lib/mediasrv-shim.so mediasrv-shim.c -ldl -lpthread
systemctl daemon-reload && systemctl restart mediasrv
```

### 4.6 验证（真实片源）

片源：`/vol4/.../Hyouka 2012 S01E01-[1080p][BDRIP][AV1.OPUS].mkv`（AV1 Main **10bit** 1920×1080
23.976fps + Opus 5.1 + ASS 字幕 + MJPEG 封面）

```text
修复前  {"result":"fail","errno":68157450}
修复后  {"result":"succ","playLink":"/media/<hash>/preset.m3u8","hlsTime":4,...}

垫片日志（完整链条）：
  find_encoder_by_name("hevc_rkmpp") -> 改用 "h264_rkmpp"
  iterate: 隐藏 vp8_rkmpp / av1_rkmpp
  find_decoder(id=225) -> libdav1d ; open2 OK codec=libdav1d     ← AV1 软解
  open2 OK codec=libfdk_aac / opus / ssa / webvtt                 ← 音轨/字幕
  open2 OK codec=h264_rkmpp                                       ← 硬编
分片 ffprobe: h264 High 1920x1080 + aac（字幕已烧入）
```

回归（确认没把 H.264/H.265 硬解搞坏）：H.265 1080p→720p 用 `hevc_rkmpp` 硬解 ✓、
H.264 1080p→720p 用 `h264_rkmpp` 硬解 ✓。

---

## 5. 第四层：飞牛服务与影视配置

这一层是"不该改的就不改"。

### 5.1 `mediasrv.service`：用 drop-in 覆写，不动原 unit

* **开启服务**：`systemctl enable mediasrv`（镜像默认是 disabled，因为出厂状态下它会崩）。
* **注入垫片 + 日志级别**：只加 `/etc/systemd/system/mediasrv.service.d/10-am40-shim.conf`
  （见 §4.5），原 `/etc/systemd/system/mediasrv.service` 保持原样 —— 可随时 `rm` 掉 drop-in 回滚。
* **日志级别**：临时开 `-l debug`。它暴露了本次定位最关键的信息：

```text
[debug] === [V] decoder context ===
[debug]   codec_id    225, Rockchip MPP (Media Process Platform) AV1 decoder
[debug]   src_format  yuv420p10le (id=62)
[debug]   hw devices  decoder=rkmpp, filter=rkmpp
[debug]   codec_id   173, Rockchip MPP (Media Process Platform) HEVC encoder
[debug] IO stream map:
[debug]   video: 0(av1) -> 0(h264)   audio: 1(opus) -> 1(aac)   subtitle: 2(ass) -> webvtt
```
（觉得吵就去掉 `-l debug` 再 `daemon-reload` + `restart`。）

### 5.2 `mediasrv.conf`：为什么**不动** `gpu.enable`

`/usr/trim/etc/mediasrv.conf` 实际内容：

```json
{"cpu":{"allowDecoding":true},
 "cache":"/vol1/mediasrv.transcode",
 "gpu":{"selectedGpuSequence":1,"enable":true}}
```

两条可选路线：

| 路线 | 做法 | 评价 |
|---|---|---|
| 全局关硬件解码 | `gpu.enable=false`（配 `cpu.allowDecoding=true`） | ✗ 连 **能用的** H.264/H.265 硬解一起关掉，CPU 扛不住 1080p |
| **本报告采用**：按编解码器外科手术 | 保持 `gpu.enable=true`，用垫片只屏蔽/改写 RK3399 做不到的那几个 | ✓ 硬解能力零损失，只截断会失败的那些请求 |

同一文件里的 `cache` 若是无效路径，mediasrv 启动时会打印
`cache dir '…' invalid, using default cache`，属正常降级，不需要改。

### 5.3 影视/播放链路的几个关键事实（决定了"为什么必须改编码器"）

* 播放请求走 `/api/v1/media`，body 形如
  `{"req":"media.getPlayLink","reqid":…,"file":…,"videoIndex":0,"videoEncoder":"hevc","audioIndex":1,"audioEncoder":"aac","subtitleIndex":2,"quality":{"resolution":"1080","bitrate":2829370}}`；
  另有 `media.queryConfig` / `gpuInfo` / `cpuInfo` / `getCacheDir` 等只读 handler。
* **`videoEncoder` 是前端（浏览器）决定的**，不是服务端能力协商的结果 —— 所以服务端无法"拒绝"，
  只能在用户态把做不到的编码器请求改写成能做的（垫片）。
* `subtitleIndex` 非 -1（烧字幕）会**强制视频重编码**，这也是最常见触发点。
* 实测（1080p 10bit AV1 + ASS 烧字幕，全链路）：**0.80x**；播放器选 **720p 档 → 1.07x（实时）**。

### 5.4 设备权限

```sh
# /etc/udev/rules.d/99-am40-mpp.rules  （镜像 payload 自带；等价于）
KERNEL=="mpp_service", MODE="0660", GROUP="video"
```

`/dev/mpp_service` 需要 `660 root:video`，否则非 root 的媒体进程打不开。

---

## 6. 第五层：持久化与运维（更新后自愈）

### 6.1 设计取舍：为什么修复不烘焙进镜像

只有**设备树必须在开机前就位**（其余都能热生效）。所以交付的 v6 镜像：

* **BOOT 分区**：新增修正 DTB + `fnEnv.txt` 指向它 + `am40-apply-fixes` + `am40-fixes.tar.gz`；
* **rootfs**：**一个字节都没改**（实测与 v5 逐字节相同）；
* 所有"侵入性"动作集中在一个可审计、可回滚的脚本里。

顺带的好处：**只烘焙 DTB 就已经修好了"mediasrv 一启动就崩"**（那纯粹是 RGA compatible 的事）。

### 6.2 `am40-apply-fixes`（统一入口，幂等）

```sh
sudo bash /boot/am40-apply-fixes            # 应用/补齐全部
sudo bash /boot/am40-apply-fixes --status   # 只看
sudo bash /boot/am40-apply-fixes --dry-run  # 只打印将做什么
```

| 步骤 | 动作 | 失败时的行为 |
|---|---|---|
| 1 设备树 | 校验 `-mpp-rga.dtb`；把 `fnEnv.txt` 的 `fdtfile` 切到它；必要时 `fdtput` 修 RGA compatible（**只动我们自己的 DTB，绝不改 stock 文件**） | 只告警并提示重启 |
| 2 内核模块 | 按 `uname -r` 选预编译 `rk_vcodec-<ver>.ko`；没有就用 payload 源码现场 `make`；`depmod` + 热重载 + **校验 `/proc/mpp_service/version`** | 自动回滚到备份模块并重新 `insmod` |
| 3 mediasrv 垫片 | 现场编译（优先）或预编译 `.so`；写 drop-in；`restart` 后**验证 `/proc/<pid>/environ` 里真有 LD_PRELOAD** | 自动摘掉 drop-in 回滚 |
| 4 服务/权限 | `enable mediasrv`、`/dev/mpp_service` 权限、udev 规则 | — |
| 5 收尾 | 安装 `am40-check`/`am40-fix-mediasrv`/`am40-apply-fixes`，同步文档，最后跑 `am40-check` | — |

只有第 1 步需要重启生效；原始文件一律备份到 `/boot/am40-backup-<时间戳>/`。

### 6.3 payload 组成（`/boot/am40-fixes.tar.gz`，约 288 KB）

```text
rk_vcodec-6.18.18.c951-trim.ko    md5 29b990926f036620b2c6232cfb0ff00e   ← 镜像自带内核
rk_vcodec-6.18.18.c1090-trim.ko   md5 d8cfc97c21d1903b723127c7ec4745c7   ← 系统更新后内核
mpp-src/                          8 个 .c + 头 + Makefile + hack/（已含 6.18 适配与 WARN 补丁，用于现场重编）
am40-patch-iommu.py               生成 §3.4 那个修复的补丁（留档/可重放）
mediasrv-shim.c / .so             §4.4 的垫片（源码优先，.so 兜底）
10-am40-shim.conf / 99-am40-mpp.rules
dtb/*.dtb                         修正设备树（备用）
bin/am40-check.sh  am40-fix-mediasrv.sh  am40-codec-matrix.sh
doc/AM40-刷机说明.md  AM40-NOTES.txt
```

### 6.4 `am40-check` 的分层体检项

| 层 | 检查项 |
|---|---|
| 启动 | 启动链 md5（`0x8000~0x1000000`）、`fnEnv.txt` 的 fdtfile/kernelfile 存在且不重复 |
| DTB | 5 个 AM40 DTB 的 md5、当前 fdtfile 是否是满编+RGA 修正版 |
| 内核模块 | `mpp_vdpu2`/`mpp_vepu2`/`mpp_rkvdec` 绑定、`/proc/mpp_service/version` 是否为 `custom-rk3399-full-v3`、**dmesg 里有没有 `iommu_set_fault_handler` WARN**、`/dev/mpp_service` 权限 |
| 用户态 | `rga3` 是否加载、`rga2` 是否绑定、`/dev/rga` 是否存在、垫片与 drop-in 是否在位、mediasrv 进程是否真的带 `LD_PRELOAD`、是否 `active` 且 `NRestarts=0` |
| 其他 | 网口速率、/boot 余量、md/温度 |

### 6.5 系统更新后的标准动作

```sh
sudo am40-check                     # 先看缺什么（更新会换内核→模块失效）
sudo bash /boot/am40-apply-fixes    # 一条命令补齐
```

---

## 7. patch 脚本索引

| 脚本 | 层 | 作用 | 幂等 | 回滚 |
|---|---|---|---|---|
| `am40-apply-fixes.sh` | 编排 | 5 步统一修复入口 | ✓ | 各步自动回滚 + `/boot/am40-backup-*` |
| `am40-patch-iommu.py` | 内核模块 | 去掉每帧 `iommu_set_fault_handler()`（§3.4） | ✓（已打则跳过） | 用 `/boot/rk_vcodec.ko.orig` 等备份换回 |
| `mpp-src/Makefile` | 内核模块 | out-of-tree 构建配方（§3.2） | — | — |
| `mediasrv-shim.c` | 用户态 | 三个钩子（§4.4） | — | `am40-fix-mediasrv --remove` |
| `10-am40-shim.conf` | 服务 | LD_PRELOAD + `-l debug` drop-in | ✓ | `rm` 掉后 `daemon-reload` |
| `am40-fix-mediasrv.sh` | 用户态 | 只装/卸垫片（`--status`/`--remove`） | ✓ | 自带 `--remove` |
| `am40-check.sh` | 运维 | 分层体检 | 只读 | — |
| `build_am40_images_v6.py` | 交付 | 从 v5 镜像原地补出 v6 三件套 | ✓ | 用 v5 镜像即可 |

---

## 附录 A：证据速查

| 看到的字符串 | 含义 / 结论 |
|---|---|
| `rga_iommu: rga_iommu_bind, binding map scheduler failed!` + `rga: rga iommu bind failed!` | DTB 里 RGA 节点没被任何驱动认领（§2.2） |
| `modprobe: ERROR: could not insert 'rga3': Bad address` | 同上，`-EFAULT` 来自 `rga_iommu_bind()` 的三索引全 -1 |
| mediasrv `status=11/SEGV` + `unhandled exception … in librga.so.2.1.0` | 缺 `/dev/rga` 的上游后果 |
| `WARNING: … iommu_set_fault_handler+0x14/0x38`（大量、每帧） | §3.4 的性能缺陷 |
| `mpp: unable to create dec av1 for soc rk3399 unsupported` | RK3399 无 AV1 硬解（§4.2） |
| `mpp: unable to create enc h265 for soc rk3399 unsupported` | RK3399 无 HEVC 硬编（§4.2） |
| `[ffmpeg] errno -542398533` | `AVERROR_EXTERNAL`，硬解 init 失败 |
| `init_video_stream_map error, errcode 4096` | mediasrv 侧的错误码，对应上面的 AVERROR_EXTERNAL |
| `This AVCodecContext was allocated for av1_rkmpp, but libdav1d passed to avcodec_open2()` | ffmpeg 8 的 codec 一致性检查（§4.3） |
| `client 9 driver is not ready!` | 旧版 mediasrv 向 MPP 请求未注册的 client（RKV）后不检查返回值 → 崩溃 |

## 附录 B：分层实测数据

**解码（MPP，1080p testsrc2，修复后）**
`H.264 10.8x` / `H.265-8bit 9.09x` / `H.265-10bit 7.18x` / `4K H.265 2.52x` / `VP9-10bit 3.66x` /
`MPEG1 2.84x` / `MPEG2 2.69x` / `MPEG4 2.98x` / `H.263 12.2x` / `MJPEG 0.95x` / `JPEG 1.63x`

**编码（MPP）**
`H.264 1080p 1.22x` / `H.264 4K 0.311x` / `MJPEG 1.34x`；`HEVC 编码` 硬件不存在

**管线**
`H.265(软解)→H.264(硬编) 1.12x` / `H.264→H.264 1.17x` /
`AV1 10bit(软解)→H.264(硬编) 1.49x`（修复前 0.49x）

**mediasrv 全链路（AV1 10bit + 烧 ASS 字幕 + AAC + HLS）**
`1080p 0.80x`（修复前 0.36x）/ `720p 1.07x`

**稳定性**：编码器 800 MHz 扫频测试中出现过 1000 MHz 档掉到 0.03x 的现象，故最终保持默认 300 MHz。

## 附录 C：回滚清单

```sh
# 设备树
sudo sed -i 's#^fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40.dtb#' /boot/fnEnv.txt && sudo reboot
# 内核模块（三档备份）
sudo cp /boot/rk_vcodec.ko.rk3399-full-v2 /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
#   或飞牛原版（只剩 H.264 硬解/硬编）
sudo cp /boot/rk_vcodec.ko.orig /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
sudo depmod -a && sudo rmmod rk_vcodec && sudo insmod <路径> && sudo systemctl start mediasrv
# 用户态垫片
sudo am40-fix-mediasrv --remove
# 本次脚本的所有改动
ls /boot/am40-backup-*/        # 里面有每一步的原始文件
```

## 附录 D：组件版本对照

| 组件 | 版本 / 标识 |
|---|---|
| fnOS | `1.2.0302`（镜像）→ `1.2.0604`（更新后） |
| 内核 | `6.18.18.c951-trim`（镜像）/ `6.18.18.c1090-trim`（现役） |
| vendor MPP（内核 + 用户态同源） | `a9380ef author: nyanmisaka 2025-12-26`（`jellyfin-mpp-next`） |
| 重编模块版本串 | `custom-rk3399-full-v3` |
| ffmpeg（mediasrv 自带） | `8.1.1-mediasrv`（`--enable-rkmpp --enable-rkrga --enable-v4l2-m2m --enable-v4l2-request`） |
| librga | `librga.so.2.1.0`（用户态 API `1.10.4`） |
| mediasrv | `0.8.41` |
| 参考 BSP | `kernel-develop-6.1`（Rockchip 6.1，用于比对 DTB 绑定与 MPP 源码） |
| 上游新一代 MPP（仅作印证） | `mpp-develop` MPP `1.1.0`（`kmpp/` 新对象模型，已无 fault handler） |
