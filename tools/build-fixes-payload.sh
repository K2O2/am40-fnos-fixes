#!/bin/bash
# ============================================================================
#  build-fixes-payload.sh —— 把仓库里的东西打成交付 payload（am40-fixes.tar.gz）
#
#  payload 是给板子上的 am40-apply-fixes 用的：它按 uname -r 取预编译模块，
#  没有对应版本就用 mpp-src/ 现场编译；垫片同样“源码优先、.so 兜底”。
#
#  用法：
#     tools/build-fixes-payload.sh --ko 6.18.18.c951-trim=<path>/rk_vcodec.ko \
#                                  --ko 6.18.18.c1090-trim=<path>/rk_vcodec.ko \
#                                 [--dtb-dir <含已编译 dtb 的目录>] [--out am40-fixes.tar.gz]
#
#  说明：
#   * 内核模块必须在**目标内核**的 headers 下编译（vermagic 必须匹配），
#     见 kernel-mpp/build.sh。
#   * .dtb 若没给 --dtb-dir，会用 dtc 从 dts/ 现场编译。
#   * .so 若本机有 gcc 就现场编，否则用 --shim-so 指定。
# ============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/am40-fixes.tar.gz"
DTBDIR=""; SHIMSO=""
declare -a KOS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --ko)     KOS+=("$2"); shift 2;;
    --dtb-dir) DTBDIR="$2"; shift 2;;
    --shim-so) SHIMSO="$2"; shift 2;;
    --out)    OUT="$2"; shift 2;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "未知参数: $1" >&2; exit 1;;
  esac
done
[ ${#KOS[@]} -gt 0 ] || { echo "至少要一个 --ko <kver>=<path>（没有预编译模块就只能现场编译，那也可以只发源码）" >&2; }

STAGE="$(mktemp -d)/am40-fixes"
mkdir -p "$STAGE"/{bin,doc,dtb,mpp-src}
VERSION="$(date +%F)"

# --- 预编译内核模块（文件名必须是 rk_vcodec-<uname -r>.ko）---
for kv in "${KOS[@]}"; do
  k="${kv%%=*}"; p="${kv#*=}"
  [ -f "$p" ] || { echo "找不到 $p" >&2; exit 1; }
  cp -f "$p" "$STAGE/rk_vcodec-${k}.ko"
  echo "  + rk_vcodec-${k}.ko  ($(stat -c%s "$p") B)"
done

# --- MPP 源码（现场重编用；已含 6.18 适配 + WARN 修复）---
cp -f "$ROOT"/kernel-mpp/mpp/*.c "$ROOT"/kernel-mpp/mpp/*.h "$ROOT"/kernel-mpp/mpp/Makefile "$STAGE/mpp-src/"
cp -r "$ROOT"/kernel-mpp/mpp/hack "$STAGE/mpp-src/"
cp -f "$ROOT"/scripts/patch-mpp-iommu.py "$STAGE/am40-patch-iommu.py"

# --- 垫片 ---
cp -f "$ROOT"/mediasrv-shim/mediasrv-shim.c "$STAGE/"
if [ -n "$SHIMSO" ]; then cp -f "$SHIMSO" "$STAGE/mediasrv-shim.so"
elif command -v gcc >/dev/null 2>&1; then
  gcc -shared -fPIC -O2 -o "$STAGE/mediasrv-shim.so" "$ROOT/mediasrv-shim/mediasrv-shim.c" -ldl -lpthread
else echo "没有 gcc 也没有 --shim-so，垫片 .so 将缺失（板子上会现场编译，通常没关系）" >&2; fi
cp -f "$ROOT"/mediasrv-shim/10-am40-shim.conf "$STAGE/"
cp -f "$ROOT"/udev/99-am40-mpp.rules "$STAGE/"

# --- 设备树（编译成 .dtb）---
if [ -n "$DTBDIR" ]; then
  cp -f "$DTBDIR"/rk3399-smart-am40-mpp-*.dtb "$STAGE/dtb/"
else
  for d in "$ROOT"/dts/rk3399-smart-am40-mpp-*.dts; do
    dtc -I dts -O dtb -o "$STAGE/dtb/$(basename "${d%.dts}").dtb" "$d" 2>/dev/null
  done
fi
# 注意：现编出来的 dtb 与发布版 dtb 的 md5 可能不同（dtc 版本/排布差异），
#       am40-apply-fixes 只在 md5 不符时告警，不会拒绝使用。

# --- 脚本与文档 ---
for s in am40-check am40-fix-mediasrv am40-codec-matrix; do cp -f "$ROOT/scripts/$s" "$STAGE/bin/$s.sh"; done
cp -f "$ROOT"/docs/*.md "$STAGE/doc/" 2>/dev/null || true
cp -f "$ROOT"/docs/BOOT-NOTES.txt "$STAGE/doc/AM40-NOTES.txt" 2>/dev/null || true
printf 'AM40-fixes %s  payload for: RK3399 / fnOS\n' "$VERSION" > "$STAGE/VERSION"
cp -f "$ROOT"/docs/PAYLOAD-README.txt "$STAGE/README.txt" 2>/dev/null || true

tar czf "$OUT" -C "$(dirname "$STAGE")" am40-fixes
echo "==> $OUT  ($(stat -c%s "$OUT") B)"
tar tzf "$OUT" | sed 's/^/    /'
