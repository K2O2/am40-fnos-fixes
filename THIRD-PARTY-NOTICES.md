# 第三方组件与来源说明

本仓库以 GPL-2.0 发布。仓库内含或依赖以下第三方作品，版权归各自所有者。

## 1. Rockchip MPP 内核驱动（`kernel-mpp/mpp/`）

* 来源：Rockchip MPP 的内核部分（`drivers/video/rockchip/mpp/`），本项目使用的快照来自
  `nyanmisaka` 维护的 6.1 分支，版本串 `a9380ef author: nyanmisaka 2025-12-26`。
* 版权：Copyright (C) Rockchip Electronics Co., Ltd. 等；文件内保留了原有 SPDX 标识
  （`GPL-2.0`）。
* 本仓库对其所做的修改以 `kernel-mpp/patches/` 中的补丁形式明确记录。

## 2. Linux 内核设备树

* `dts/` 下的设备树源码是在飞牛 fnOS 出厂 DTB（其本身派生自 Linux 内核的 `rk3399.dtsi`
  与板厂改动）基础上修改而来，遵循内核的 `GPL-2.0`（设备树为 `GPL-2.0 OR BSD-3-Clause`）。
* 修改内容以 `dts/am40-dtb.patch` 明确记录。

## 3. 飞牛 fnOS

* 本项目的修复对象是飞牛 fnOS 系统（含 `mediasrv`），与本项目无隶属关系。
* 仓库**不包含** fnOS 的任何二进制或系统文件；交付镜像也不在仓库内（见 Release）。
* `mediasrv` 是闭源程序，本项目不对其做任何修改，仅通过标准 `LD_PRELOAD` 与 systemd
  drop-in 在外部调整其行为。

## 4. librga / librockchip_mpp（用户态）

* 仓库不分发这些库。垫片（`mediasrv-shim/`）是按标准 ELF 符号拦截方式在运行时介入，
  不修改、不重分发上述库。

## 5. Rockchip RKFW/RKAF 打包器（`tools/rkfw.py`）

* 本仓库自研实现：文件格式由厂商 ROM 逆向得到，并用往返比对（parse→rebuild→逐字节比较）校验。
* 仅用于生成瑞芯微刷机工具可识别的 `update.img` 容器，不包含厂商代码。

## 6. 商标

* "Rockchip"、"RK3399"、"fnOS"、"飞牛" 等名称与商标归各自所有者，本项目仅作事实性指代。
