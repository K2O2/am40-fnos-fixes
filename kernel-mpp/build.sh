#!/bin/bash
# ============================================================================
#  build.sh —— 编译 AM40 版 rk_vcodec.ko（vendor MPP + 本项目补丁）
#
#  用法：
#     [KDIR=/usr/src/linux-headers-$(uname -r)] bash kernel-mpp/build.sh
#
#  说明：
#   * 默认用当前运行内核的 headers 目录；产物在 kernel-mpp/build/rk_vcodec.ko。
#   * 验证：modinfo 里的 vermagic 必须 == uname -r，否则 insmod 会被拒。
#   * 编译成功后可直接安装：
#       sudo systemctl stop mediasrv
#       sudo cp build/rk_vcodec.ko /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
#       sudo depmod -a && sudo rmmod rk_vcodec && sudo insmod /lib/modules/$(uname -r)/updates/trim/rk_vcodec/rk_vcodec.ko
#       sudo systemctl start mediasrv
#     或直接跑仓库根目录的 scripts/am40-apply-fixes（它会按 uname -r 选预编译模块，
#     没有就用自带源码现场编译）。
# ============================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
KDIR="${KDIR:-/usr/src/linux-headers-$(uname -r)}"
SRC="$HERE/mpp"
BUILD="$HERE/build"

[ -d "$KDIR" ] || { echo "找不到内核头文件目录 $KDIR（apt install linux-headers-$(uname -r)？）" >&2; exit 1; }
command -v make >/dev/null || { echo "缺少 make" >&2; exit 1; }

echo ">> 编译：$SRC"
echo ">> 内核头文件：$KDIR"
rm -rf "$BUILD" && mkdir -p "$BUILD"
# out-of-tree 构建：把源码树拷到 build/ 里编，保持源码目录干净
cp -r "$SRC"/. "$BUILD"/

make -C "$KDIR" M="$BUILD" modules KCFLAGS="-Wno-error=incompatible-pointer-types"

KO="$BUILD/rk_vcodec.ko"
[ -f "$KO" ] || { echo "编译失败：没有产出 $KO" >&2; exit 1; }
strip --strip-debug "$KO" 2>/dev/null || true
echo
echo ">> 产物：$KO  ($(stat -c%s "$KO") B)"
modinfo "$KO" | grep -E "^(name|vermagic|license)" || true
echo ">> 期望 vermagic = $(uname -r)"
