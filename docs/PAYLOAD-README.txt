AM40 (RK3399) 飞牛整机修复 payload
==================================
被板子上的 /boot/am40-apply-fixes 使用（解包到 /run/am40-fixes 后按步骤落地）。
整体说明见仓库 README.md，技术原理见 docs/TECHNICAL-REPORT.md。

内容：
  rk_vcodec-<内核版本>.ko   带「每帧 iommu WARN」修复的满编 MPP 模块（预编译，按 uname -r 选）
  mpp-src/                  同一份可用源码（没有对应预编译版时现场 make）
  am40-patch-iommu.py       生成该性能修复的补丁（已应用在 mpp-src 里，留档）
  mediasrv-shim.c/.so       mediasrv 兼容垫片（AV1 走软解 / 做不到的硬编回退 H.264）
  10-am40-shim.conf         systemd drop-in（LD_PRELOAD + -l debug）
  99-am40-mpp.rules         /dev/mpp_service 权限规则
  dtb/*.dtb                 修正过的设备树（满编 MPP + RGA 修正）
  bin/                      am40-check / am40-fix-mediasrv / am40-codec-matrix
  doc/                      文档（会同步到 /boot）

用法：
  sudo bash /boot/am40-apply-fixes            # 应用/补齐全部修复（幂等）
  sudo bash /boot/am40-apply-fixes --status    # 只看状态
  sudo am40-check                              # 分层体检

回滚：脚本每一步的原始文件都备份在 /boot/am40-backup-<时间戳>/
      内核模块也可用 /boot/rk_vcodec.ko.orig（飞牛原版）换回。
