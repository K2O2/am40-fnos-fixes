# 工具

| 文件 | 用途 | 需要什么 |
|---|---|---|
| `build-fixes-payload.sh` | 把仓库内容打成交付 payload `am40-fixes.tar.gz`（板子上的 `am40-apply-fixes` 就吃这个） | 目标内核编译出的 `rk_vcodec.ko`；`dtc`（编 DTB）；`gcc`（编垫片，可选） |
| `build-am40-images.py` | 从飞牛官方基础镜像生成交付镜像三件套（**只改 BOOT 分区**，rootfs 逐字节复用） | 飞牛基础镜像、打好补丁的 p1、AM40 eMMC 0-16MB 引导备份、官方 `MiniLoaderAll.bin`、一份厂商 ROM（取 RKFW 头模板） |
| `rkfw.py` | Rockchip RKFW/RKAF 打包器（自研，格式由厂商 ROM 逆向并用往返比对校验） | — |

## payload 怎么打

```sh
# 1) 在目标内核上编模块（见 kernel-mpp/build.sh），两个内核版本各编一份
bash kernel-mpp/build.sh                       # 得到 build/rk_vcodec.ko

# 2) 打包（文件名必须是 rk_vcodec-<uname -r>.ko，脚本按 uname -r 取用）
tools/build-fixes-payload.sh \
    --ko 6.18.18.c951-trim=build/rk_vcodec.ko \
    --ko 6.18.18.c1090-trim=build-c1090/rk_vcodec.ko \
    --out am40-fixes.tar.gz
```

payload 结构（`am40-apply-fixes` 依赖这个布局）：

```
am40-fixes/
  VERSION  README.txt
  rk_vcodec-<内核版本>.ko        ← 预编译模块（可多份）
  mpp-src/                       ← 源码，现场重编用（含 6.18 适配与 WARN 补丁）
  am40-patch-iommu.py
  mediasrv-shim.c / .so
  10-am40-shim.conf  99-am40-mpp.rules
  dtb/*.dtb
  bin/am40-check.sh  am40-fix-mediasrv.sh  am40-codec-matrix.sh
  doc/*.md
```

## 镜像怎么打（维护者）

镜像**不是**从原始镜像重做的，而是在现成的 AM40 化镜像上「原地补 BOOT 分区」：

```
1. 取出 p1（360MB ext2）        : dd if=<base img> of=p1.img bs=512 skip=65536 count=737280
2. 用 debugfs 写入新文件（新 DTB / fnEnv.txt / am40-apply-fixes / am40-fixes.tar.gz / 文档）
3. 写回镜像 p1 区域             : dd if=p1.img of=<out img> bs=512 seek=65536 conv=notrunc
4. 跑本目录的 build-am40-images.py 生成三件套
```

第 2 步的 debugfs 命令序列（`-w` 是写模式；替换已有文件要先 `rm`）：

```sh
debugfs -w -f cmds.txt p1.img
# cmds.txt 形如：
#   rm /fnEnv.txt
#   write ./fnEnv.txt /fnEnv.txt
#   write ./rk3399-smart-am40-mpp-rga.dtb /dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb
#   write ./am40-apply-fixes /am40-apply-fixes
#   sif /am40-apply-fixes mode 0100755
```

打完务必校验（`docs/USAGE.md` 有完整清单）：
p1 的 `e2fsck -fn` 干净、回读文件 md5 与源一致、
启动链 `0x8000~0x1000000` 的 md5 与基础镜像一致、p2 与基础镜像逐字节一致。
