# 变更记录

## v6 — 2026-09-17

**交付形态改变**：镜像只烘焙设备树，其余修复交给一个统一脚本（rootfs 逐字节未改）。

* 新增 `am40-apply-fixes`：五步幂等修复入口（设备树 / 内核模块 / 垫片 / 服务权限 / 自检），
  每步失败自动回滚，原始文件备份到 `/boot/am40-backup-<时间戳>/`。
* 新增 `am40-fixes.tar.gz` payload：两份预编译模块（c951 / c1090）+ MPP 源码 + 垫片 + 脚本 + 文档。
* 新增分层体检 `am40-check`：设备树 / 模块版本 / 每帧 WARN / RGA / 垫片 / 服务权限。
* 新增 `am40-codec-matrix` 能力矩阵脚本。
* 镜像：`fnos_1.2.0302_am40_SD-boot_v6.img`、`fnos_1.2.0302_am40_eMMC_v6.img`、
  `am40-eMMC-flash_v6/`。

### 仓库版脚本相对 v6 镜像内那一份的差异（仅外观）

* `am40-apply-fixes`：自编 DTB 的 md5 与发布版不同时改为打印 `[note]` 而不是 `[WARN]`
  （用 `dtc` 重新编译的 DTB 布局本就与发布版不同，功能性判断由 RGA 节点检查负责）。
* payload 构建器把补丁脚本命名为 `am40-patch-iommu.py`，与 payload 内 `README.txt` 一致。

## v5 — 2026-09-16

* 镜像自带 AM40 可用的启动链（`0x8000~0x1000000`），可独立从 SD/eMMC 启动。
* 内置诊断 initramfs（`am40-diag.cpio.gz`）与 `AM40-NOTES.txt`。

## 修复项（按层）

### 设备树

* **RGA 节点 compatible**：`rockchip,rk3399-rga` → `rockchip,rga2`。
  修复 mediasrv 一启动就 SEGV（缺 `/dev/rga` → `librga` 崩）。
* **新增 vendor MPP 绑定节点**（`mpp-srv` / `vepu` / `vdpu` / `rkvdec`），
  修复 RK3399 的 HEVC / VP9 / 4K 解码器未被 MPP 绑定。
* mainline 的 `video-codec@ff660000` / `vpu@ff650000` 置 `disabled`，避免抢地址。

### 内核模块

* 重编 `rk_vcodec.ko`：补上 `CONFIG_CPU_RK3399` 等开关，让 RKV 的 rk3399 匹配生效。
* 6.18 API 迁移 4 处：`class_create`、`MODULE_IMPORT_NS("DMA_BUF")`、`fd_file()`、
  `iommu_map(..., GFP_KERNEL)`。
* **性能修复**：去掉每帧一次的内核 WARN（`iommu_set_fault_handler` 调用），
  1080p H.264 编码 0.66x → 1.22x，解码 1.37x → 10.8x。

### 用户态

* 新增 `mediasrv-shim`（LD_PRELOAD）：隐藏 `av1_rkmpp` / `vp8_rkmpp`（走软件解码），
  把做不到的 `*_rkmpp` 硬编改写成 `h264_rkmpp`。
  修复 AV1 播放报 `errno 4096`、以及浏览器请求 HEVC 时的编码失败。
