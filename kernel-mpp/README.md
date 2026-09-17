# 内核模块层：vendor MPP 的 6.18 适配与性能修复

## 目录内容

| 路径 | 说明 |
|---|---|
| `mpp/` | **可直接 out-of-tree 编译**的 vendor MPP 内核驱动源码（已含本项目的全部改动） |
| `patches/0001-mpp-6.18-api-drift.patch` | 把 vendor 6.1 源码迁到 6.18 的 4 处改动 |
| `patches/0002-remove-per-frame-iommu-warn.patch` | 去掉每帧一次的内核 WARN（吞吐减半的真凶） |
| `build.sh` | 一条命令编出 `rk_vcodec.ko` |

## 来源与版本

* 上游：Rockchip MPP 内核驱动（`drivers/video/rockchip/mpp/`），本文档使用的快照来自
  `nyanmisaka` 维护的 6.1 分支，版本串 `a9380ef author: nyanmisaka 2025-12-26`——
  **与飞牛 `1.2.0304/1.2.0604` 镜像里自带用户态 `librockchip_mpp.so.1` 同源同版本**，
  这一点很重要：内核模块与用户态库的接口必须配套。
* 许可证：`GPL-2.0`（源码内保留了原有 SPDX 头）。见仓库根目录 `THIRD-PARTY-NOTICES.md`。

## 为什么需要重编

飞牛自带的 `rk_vcodec.ko` 编译时**没有开 `CONFIG_CPU_RK3399`**：

```console
$ strings /boot/rk_vcodec.ko.orig | grep -oE 'rockchip,rkv-decoder-[a-z0-9-]+' | sort -u
rockchip,rkv-decoder-rk3528
rockchip,rkv-decoder-rk3562
rockchip,rkv-decoder-rk3568
rockchip,rkv-decoder-rk3576
rockchip,rkv-decoder-v1
rockchip,rkv-decoder-v2
rockchip,rkv-decoder-v2-ccu          ← 没有 rk3399
```

源码依据：`mpp_rkvdec.c` 里 rk3399 那条匹配被 `#ifdef CONFIG_CPU_RK3399` 包着。
后果是 RK3399 的 RKV 解码器（`ff660000`）根本没被绑定 → HEVC / VP9 / 4K 只能软解。
（`mpp_vdpu2` / `mpp_vepu2` 不受影响，所以 H.264 一直是能用的。）

## 编译

```sh
bash kernel-mpp/build.sh                      # 默认用当前内核 headers
KDIR=/usr/src/linux-headers-6.18.18.c951-trim bash kernel-mpp/build.sh   # 指定内核
```

编译开关（见 `mpp/Makefile`）：

```
-DCONFIG_CPU_RK3399                       ← 关键：让 RKV 的 rk3399 匹配生效
-DCONFIG_ROCKCHIP_MPP_{RKVDEC,VDPU2,VEPU2,JPGDEC,JPGENC,PROC_FS}
-DMPP_VERSION="\"custom-rk3399-full-v3\"" ← 便于用 /proc/mpp_service/version 确认装对了
KCFLAGS="-Wno-error=incompatible-pointer-types"   ← .remove 签名 int→void（见 0001）
```

**vermagic 必须与运行内核一致**（`6.18.18.c951-trim` 与 `6.18.18.c1090-trim` 互不通用）。
好消息是这两个内核都**没有开 `CONFIG_MODVERSIONS`**，所以不需要符号 CRC，只要版本对上就能 `insmod`。

## 改动清单（相对 vendor 原版）

### 0001：6.18 API 漂移（4 处）

| 文件 | 原写法 | 6.18 写法 |
|---|---|---|
| `mpp_service.c` | `class_create(THIS_MODULE, MPP_CLASS_NAME)` | `class_create(MPP_CLASS_NAME)` |
| `mpp_service.c` | `MODULE_IMPORT_NS(DMA_BUF)` | `MODULE_IMPORT_NS("DMA_BUF")` |
| `mpp_common.c` | `f.file` / `f.file->private_data` | `fd_file(f)` / `fd_file(f)->private_data` |
| `mpp_rkvdec.c` | `iommu_map(..., IOMMU_READ\|IOMMU_WRITE)` | 末尾补 `, GFP_KERNEL` |
| `mpp_iommu.c` | `#include <asm/dma-iommu.h>` | 删除（arm64 6.18 已无此头；相关代码本就被 `#ifdef CONFIG_ARM_DMA_USE_IOMMU` 排除） |

另外还需要 5 个 vendor 头文件（飞牛的 headers 包里没有，仓库的 `am40-apply-fixes` 会在需要时补进
`/usr/src/linux-headers-<ver>/`）：

```
include/uapi/linux/rk-mpp.h
include/linux/dma-buf-cache.h
include/linux/rockchip/rockchip_sip.h        ← 需要加 include guard
include/soc/rockchip/rockchip_sip.h          ← 改成一个 wrapper，否则 enum 重复定义编译失败
include/soc/rockchip/rockchip_opp_select.h
```

### 0002：每帧一次的内核 WARN（**性能修复**）

`mpp_iommu_dev_activate()` **每次激活设备都调用一次** `iommu_set_fault_handler()`，
而 6.18 起这个 API 已废弃（`domain->ops->set_fault_handler == NULL`），函数入口就是 `WARN_ON`：

```text
WARNING: CPU: 5 PID: 7972 at drivers/iommu/iommu.c:2015 iommu_set_fault_handler+0x14/0x38
Modules linked in: rk_vcodec(O) xt_conntrack … （一行约 1.5 KB 的模块清单）
```

实测 300 帧编解码各产生 **287 条** WARN；因为 `fnEnv.txt` 里是 `console=both`，
这些 printk 同时刷串口(1.5 Mbps)与 HDMI 控制台，**单帧打印开销比编码本身还大**：

| | 修复前 | 修复后 |
|---|---|---|
| 1080p H.264 编码 | 0.66x | **1.22x** |
| 1080p H.264 解码 | 1.37x | **10.8x** |
| dmesg（300 帧编 + 解一遍） | 2588 行 | **0 行** |

补丁内容就是把这个调用 `#if 0` 掉，并给 `mpp_iommu_handle` 加 `__maybe_unused`。
6.18 的 `rockchip-iommu` 自己在中断里就会报 page fault，驱动无需再注册。

> 上游印证：Rockchip 新一代 MPP（`mpp` 1.1.0 的 `kmpp/`）里 `fault_handler` /
> `iommu_set_fault_handler` 一个引用都没有 —— 官方同样是把它删掉了。

**不需要重编也能自证**：`sudo dmesg -n 1`（只让 emergency 上控制台）后重跑基准，
速度立刻回到 1.17x / 9.2x。

## 不要超频

`rockchip,normal-rates` / `advanced-rates` 是 MPP 驱动用来设 VPU 时钟的（按 `clock-names` 下标读）。
厂商默认档是 **300 MHz**（实测 297 MHz），这就是正确值：

| `aclk_vcodec` | 1080p H.264 编码 |
|---|---|
| 297 MHz | 0.66x（打上 0002 后 1.22x） |
| 594 MHz | 0.89x |
| 800 MHz | 1.23x（用 2.7 倍功耗换回本来就该有的速度） |
| 1000 MHz+ | **0.03x，禁止** |

运行期可用 `/proc/mpp_service/vepu/aclk`（= `debug_rate_hz`）自己验证这一点。

## 适配更新后的内核

1. 取新内核的 headers（`/usr/src/linux-headers-<new ver>`，系统更新一般会带上）；
2. `bash kernel-mpp/build.sh`；若编译报错，多半是新的 API 漂移 —— 照 `patches/0001` 的思路补；
3. 编不动时最稳的参照是 `kernel-develop-*` 系列里对应内核版本的 vendor 源码。
