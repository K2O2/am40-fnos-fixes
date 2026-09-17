#!/usr/bin/env python3
# ============================================================================
# am40-patch-iommu.py —— 去掉 rockchip MPP 驱动里每帧一次的废弃 API 调用
#
# 背景（2026-09-17 定位）：
#   kernel-develop-6.1 的 drivers/video/rockchip/mpp/mpp_iommu.c 里
#   mpp_iommu_dev_activate() 每次激活设备都调用一次
#       iommu_set_fault_handler(info->domain, ..., dev);
#   6.18 起这个 API 已废弃（domain->ops->set_fault_handler == NULL），
#   于是每调用一次就 WARN_ON + 打印整页调用栈（含 1.5KB 的模块清单）。
#   而"激活设备"是每帧一次 —— 实测编码/解码 300 帧各产生 287 条 WARN。
#   /boot/fnEnv.txt 是 console=both，这些 WARN 同时刷串口(1.5Mbps)+HDMI 控制台，
#   把 VPU 编解码拖慢一倍以上：
#       1080p H.264 编码 0.66x -> 1.25x
#       1080p H.264 解码 1.37x -> 5.5~9.7x
#       1080p AV1 10bit 软解+H.264 硬编 0.49x -> 1.49x
#
#   6.18 的 rockchip-iommu 自己在中断里就会报 page fault，驱动不需要再注册一遍。
#   上游新一代 MPP（mpp 1.1.0 的 kmpp/）里也已经没有这套 fault handler 了。
#
# 用法： python3 am40-patch-iommu.py [mpp_iommu.c 路径]
#        默认路径 mpp/mpp_iommu.c
# ============================================================================
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "mpp/mpp_iommu.c"
s = open(path).read()

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
		 * "每次激活设备"都调一次的，实测等于每帧一次：1080p H.264
		 * 编码 0.66x->1.17x、解码 1.37x->9.2x 全是被这个 printk 拖掉的。
		 * 6.18 的 rockchip-iommu 自己会在中断里报 page fault，这里不再注册。
		 */
#if 0
		iommu_set_fault_handler(info->domain, dev->fault_handler ?
					dev->fault_handler : mpp_iommu_handle, dev);
#endif
"""

if old not in s:
    if "#if 0\n\t\tiommu_set_fault_handler" in s:
        print("已经打过补丁了，跳过")
        sys.exit(0)
    print("ERROR: 没找到 mpp_iommu_dev_activate() 里那段调用，源码可能已变", file=sys.stderr)
    sys.exit(1)

s = s.replace(old, new)

# mpp_iommu_handle 现在没人用了，避免 -Wunused-function
old2 = "static int mpp_iommu_handle(struct iommu_domain *iommu,"
new2 = "static int __maybe_unused mpp_iommu_handle(struct iommu_domain *iommu,"
if old2 in s and new2 not in s:
    s = s.replace(old2, new2)

open(path, "w").write(s)
print("patched OK: %s" % path)
