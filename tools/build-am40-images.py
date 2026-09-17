#!/usr/bin/env python3
# ============================================================================
#  build-am40-images.py —— 维护者工具：生成 AM40 交付镜像（三件套）
#
#  设计要点：**只改 BOOT 分区，rootfs 逐字节复用基础镜像**。
#  所以这个脚本只是「把打过补丁的 p1 写回镜像 + 重排 eMMC 的 GPT + 打瑞芯微直刷包」。
#
#  它需要（仓库不提供，需自行准备）：
#    * 飞牛官方基础镜像（已 AM40 化过的 v5 镜像，即 rom/fnos_1.2.0302_am40_SD-boot.img）
#    * 打好的 BOOT 分区镜像 p1（含修正 DTB + am40-apply-fixes + payload）
#    * AM40 的 eMMC 0-16MB 引导区备份、官方 MiniLoaderAll.bin、
#      以及一份厂商 ROM（用于取 RKFW/RKAF 头部模板）
#
#  用法（全部可用环境变量覆盖）：
#     AM40_BASE_IMAGE=<v5 镜像> AM40_P1_IMAGE=<p1.img> \
#     AM40_EMMC_BOOT_BACKUP=<0-16M 备份> AM40_MINILOADER=<MiniLoaderAll.bin> \
#     AM40_RKFW_TEMPLATE=<厂商 ROM> AM40_OUTDIR=rom \
#     python3 tools/build-am40-images.py
#
#  产出：
#     rom/fnos_1.2.0302_am40_SD-boot_v6.img    卡启（dd 到 SD 卡）
#     rom/fnos_1.2.0302_am40_eMMC_v6.img       eMMC 直刷（dd 到 /dev/mmcblk0）
#     rom/am40-eMMC-flash_v6/                  瑞芯微工具包（含 update.img）
# ============================================================================
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rkfw  # noqa: E402


def env(name, default):
    return os.environ.get(name, default)


# 基础镜像：已经是「AM40 化的 v5」（自带可启动的 U-Boot 链 + p1/p2 布局）
BASE = env('AM40_BASE_IMAGE', 'rom/fnos_1.2.0302_am40_SD-boot.img')
# 打过补丁的 BOOT 分区（用 debugfs 往里写文件得到，见 docs/USAGE.md「维护者」一节）
P1 = env('AM40_P1_IMAGE', 'build/p1-v6.img')
EMMC_BOOT_BACKUP = env('AM40_EMMC_BOOT_BACKUP', 'build/emmc-boot-0-16M.img')
MINILOADER = env('AM40_MINILOADER', 'build/RK3399_MiniLoaderAll.bin')
RKFW_TEMPLATE = env('AM40_RKFW_TEMPLATE', 'build/vendor-rom.img')
OUTDIR = env('AM40_OUTDIR', 'rom')

SD_IMG = os.path.join(OUTDIR, 'fnos_1.2.0302_am40_SD-boot_v6.img')
EMMC_IMG = os.path.join(OUTDIR, 'fnos_1.2.0302_am40_eMMC_v6.img')
FLASHDIR = os.path.join(OUTDIR, 'am40-eMMC-flash_v6')

BOOTLOADER_END = 0x8000            # 0-16MB 引导区
EMMC_BOOT_START = 0x8000           # 16MB
EMMC_BOOT_SECTORS = 0x100000       # 512MB
EMMC_ROOT_START = 0x108000         # 528MB
SD_BOOT_START = 0x10000            # 32MB（v5 原布局）
SD_ROOT_START = 0xCC000            # 408MB

# 0fc63daf-8483-4772-8e79-3d69d8477de4 (Linux filesystem)，按 GPT 的字节序存放
TYPE_LINUX = bytes.fromhex('af3dc60f838472478e793d69d8477de4')


def run(*cmd):
    print('  +', ' '.join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def dd(src, dst, skip_sectors, length_bytes, seek_sectors=0):
    run('dd', 'if=' + src, 'of=' + dst, 'bs=512', 'skip=%d' % skip_sectors,
        'seek=%d' % seek_sectors, 'count=%d' % (length_bytes // 512),
        'conv=notrunc', 'status=none')


def gpt_chunks(total_sectors, parts, disk_guid):
    """构造保护 MBR + 主 GPT + 备份 GPT（纯 Python，不需要 root/loop）"""
    ent_size, ent_count, ent_lba = 128, 128, 2
    ent_sectors = ent_count * ent_size // 512
    first_usable = ent_lba + ent_sectors
    last_usable = total_sectors - 1 - ent_sectors - 1
    entries = bytearray(ent_count * ent_size)
    for i, (name, tguid, pguid, start, end) in enumerate(parts):
        e = bytearray(ent_size)
        struct.pack_into('<16s16sQQQ', e, 0, tguid, pguid, start, end, 0)
        nm = name.encode('utf-16-le')
        e[56:56 + len(nm)] = nm
        entries[i * ent_size:(i + 1) * ent_size] = e
    ent_crc = zlib.crc32(bytes(entries)) & 0xffffffff

    def hdr(my_lba, alt_lba, elba, first, last):
        h = bytearray(92)
        struct.pack_into('<8sIIIIQQQQ16sQIII', h, 0, b'EFI PART', 0x00010000, 92, 0, 0,
                         my_lba, alt_lba, first, last, disk_guid, elba, ent_count,
                         ent_size, ent_crc)
        struct.pack_into('<I', h, 16, zlib.crc32(bytes(h)) & 0xffffffff)
        return bytes(h)

    mbr = bytearray(512)
    mbr[0x1BE:0x1BE + 16] = struct.pack('<B3sB3sII', 0, b'\x00\x02\x00', 0xEE,
                                        b'\xff\xff\xff', 1,
                                        min(total_sectors - 1, 0xffffffff))
    mbr[0x1FE:0x200] = b'\x55\xaa'
    return [(0, bytes(mbr)),
            (512, hdr(1, total_sectors - 1, ent_lba, first_usable, last_usable)),
            (2 * 512, bytes(entries)),
            ((total_sectors - 1 - ent_sectors) * 512, bytes(entries)),
            ((total_sectors - 1) * 512,
             hdr(total_sectors - 1, 1, total_sectors - 1 - ent_sectors, first_usable, last_usable))]


def base_part_sectors():
    with open(BASE, 'rb') as f:
        f.seek(0x400)
        ent = f.read(256)
    out = []
    for i in range(2):
        _t, _u, s, e, _a = struct.unpack_from('<16s16sQQQ', ent, i * 128)
        out.append(e - s + 1)
    return out


def build_sd_image():
    print('\n=== [1] 卡启镜像 ===')
    os.makedirs(OUTDIR, exist_ok=True)
    shutil.copy2(BASE, SD_IMG)
    dd(P1, SD_IMG, 0, os.path.getsize(P1), SD_BOOT_START)
    print('  -> %s  %d 字节' % (SD_IMG, os.path.getsize(SD_IMG)))


def build_emmc_image():
    print('\n=== [2] eMMC 直刷 raw 镜像 ===')
    boot_sectors, root_sectors = base_part_sectors()
    total_sectors = EMMC_ROOT_START + root_sectors + 0x800
    total_sectors = (total_sectors + 0x7FF) // 0x800 * 0x800
    with open(EMMC_BOOT_BACKUP, 'rb') as f:
        f.seek(0x400)
        ent = f.read(128 * 2)
    p1_guid, p2_guid = ent[16:32], ent[128 + 16:128 + 32]
    parts = [('BOOT', TYPE_LINUX, p1_guid, EMMC_BOOT_START,
              EMMC_BOOT_START + EMMC_BOOT_SECTORS - 1),
             ('rootfs', TYPE_LINUX, p2_guid, EMMC_ROOT_START,
              EMMC_ROOT_START + root_sectors - 1)]
    chunks = gpt_chunks(total_sectors, parts, os.urandom(16))
    with open(EMMC_IMG, 'wb') as f:
        f.truncate(total_sectors * 512)
    with open(EMMC_IMG, 'r+b') as f:
        with open(EMMC_BOOT_BACKUP, 'rb') as b:
            f.seek(0)
            f.write(b.read(BOOTLOADER_END * 512))
        for off, data in chunks:
            f.seek(off)
            f.write(data)
    dd(P1, EMMC_IMG, 0, boot_sectors * 512, EMMC_BOOT_START)
    dd(SD_IMG, EMMC_IMG, SD_ROOT_START, root_sectors * 512, EMMC_ROOT_START)
    print('  -> %s  %d 字节' % (EMMC_IMG, os.path.getsize(EMMC_IMG)))


PARAMETER = """FIRMWARE_VER: 1.2.0302-am40
MACHINE_MODEL: RK3399
MACHINE_ID: 007
MANUFACTURER: RK3399
MAGIC: 0x5041524B
ATAG: 0x00200800
MACHINE: 3399
CHECK_MASK: 0x80
PWR_HLD: 0,0,A,0,1
TYPE: GPT
CMDLINE: mtdparts=rk29xxnand:0x00002000@0x00004000(uboot),0x00002000@0x00006000(trust),0x00100000@0x00008000(boot:bootable),-@0x00108000(rootfs:grow)
uuid:rootfs=614e0000-0000-4b53-8000-1d28000054a9
"""

PACKAGE_FILE = """# NAME\t\tRelative path
#
#HWDEF\t\tHWDEF
package-file\tpackage-file
bootloader\tImage/MiniLoaderAll.bin
parameter\tImage/parameter.txt
uboot\t\tImage/uboot.img
trust\t\tImage/trust.img
boot\t\tImage/boot.img
rootfs\t\tImage/rootfs.img
"""


def build_flash_package():
    print('\n=== [3] 瑞芯微直刷包 ===')
    os.makedirs(FLASHDIR, exist_ok=True)
    boot_img = os.path.join(FLASHDIR, 'boot.img')
    rootfs_img = os.path.join(FLASHDIR, 'rootfs.img')
    uboot_img = os.path.join(FLASHDIR, 'uboot.img')
    trust_img = os.path.join(FLASHDIR, 'trust.img')
    boot_sectors, root_sectors = base_part_sectors()
    for path, (src, skip, ln) in {
        boot_img: (P1, 0, boot_sectors * 512),
        rootfs_img: (SD_IMG, SD_ROOT_START, root_sectors * 512),
        uboot_img: (EMMC_BOOT_BACKUP, 0x4000, 0x400000),
        trust_img: (EMMC_BOOT_BACKUP, 0x6000, 0x400000),
    }.items():
        dd(src, path, skip, ln)
    shutil.copy2(MINILOADER, os.path.join(FLASHDIR, 'MiniLoaderAll.bin'))
    with open(os.path.join(FLASHDIR, 'parameter.txt'), 'w') as f:
        f.write(PARAMETER)
    with open(os.path.join(FLASHDIR, 'package-file'), 'w') as f:
        f.write(PACKAGE_FILE)
    # update.img：从厂商 ROM 里取 RKFW/RKAF 头部模板，再打包
    with open(RKFW_TEMPLATE, 'rb') as f:
        rkfw_hdr = f.read(0x66)
        f.seek(0x19)
        _loff, _llen, ioff, _ilen = struct.unpack('<IIII', f.read(16))
        f.seek(ioff)
        rkaf_hdr = f.read(0x800)
        f.seek(ioff + 0x61000 + 0x1F5 - 4)
        parm_trailer4 = f.read(4)
    num = struct.unpack_from('<H', rkaf_hdr, 0x88)[0]
    bk = {}
    for i in range(num):
        e = rkaf_hdr[0x8C + i * 0x70:0x8C + (i + 1) * 0x70]
        nm = e[0:0x20].split(b'\0')[0].decode()
        _ns, _pos, addr, _al, _ac = struct.unpack_from('<IIIII', e, 0x5C)
        bk[nm] = dict(addr=addr, nand_size=_ns)
    parts = [
        dict(name='package-file', file='package-file', data=PACKAGE_FILE.encode(),
             addr=bk['package-file']['addr'], nand_size=bk['package-file']['nand_size']),
        dict(name='bootloader', file='Image/MiniLoaderAll.bin', path=MINILOADER,
             addr=bk['bootloader']['addr'], nand_size=bk['bootloader']['nand_size']),
        dict(name='parameter', file='Image/parameter.txt', data=PARAMETER.encode(),
             addr=bk['parameter']['addr'], nand_size=bk['parameter']['nand_size']),
        dict(name='uboot', file='Image/uboot.img', path=uboot_img, addr=0x4000, nand_size=0x2000),
        dict(name='trust', file='Image/trust.img', path=trust_img, addr=0x6000, nand_size=0x2000),
        dict(name='boot', file='Image/boot.img', path=boot_img,
             addr=EMMC_BOOT_START, nand_size=EMMC_BOOT_SECTORS),
        dict(name='rootfs', file='Image/rootfs.img', path=rootfs_img,
             addr=EMMC_ROOT_START, nand_size=0),
    ]
    rkfw.write_rkfw(os.path.join(FLASHDIR, 'update.img'), MINILOADER, parts,
                    rkfw_hdr, rkaf_hdr, bytes.fromhex('b71b1176'), parm_trailer4)
    print('  -> %s' % FLASHDIR)


if __name__ == '__main__':
    for p in (BASE, P1, EMMC_BOOT_BACKUP, MINILOADER, RKFW_TEMPLATE):
        if not os.path.exists(p):
            print('缺文件:', p)
            print('（这是一个维护者工具，需要自备飞牛基础镜像与 AM40 引导区备份；'
                  '可用环境变量覆盖路径，见文件头注释）')
            sys.exit(1)
    build_sd_image()
    build_emmc_image()
    build_flash_package()
    print('\n=== 校验值 ===')
    for p in (SD_IMG, EMMC_IMG, os.path.join(FLASHDIR, 'update.img')):
        print('  %-58s %s  %d B' % (os.path.basename(p), sha256(p)[:16], os.path.getsize(p)))
