#!/usr/bin/env python3
"""
Rockchip RKFW / RKAF 打包器（自研，格式由厂商 ROM 逆向并用往返比对校验）

RKFW 文件结构
=============
  [0x00] RKFW header (0x66 字节)
        0x00 "RKFW"
        0x04 u16  header_size = 0x66
        0x06 u16  0
        0x08 u32  version
        0x0C u32  date/time code
        0x10 5B   code (04 1b 14 27 13)
        0x15 char[4] chip = "C033" (RK3399)
        0x19 u32  loader_offset (= 0x66)
        0x1D u32  loader_length (未补齐的真实长度)
        0x21 u32  image_offset (= 0x66 + loader_length)
        0x25 u32  image_length  (= RKAF_length + 4)
        0x29 u32  0
        0x2D u32  1
  [0x66] MiniLoaderAll (loader_length 字节)
  [...]  RKAF archive
  [...]  4 字节未知字段（原样沿用厂商 ROM 的值）
  [EOF-32] 32 字节 ASCII MD5 = md5(整个文件去掉最后 32 字节)

RKAF archive 结构
=================
  [0x000] RKAF header
          0x00 "RKAF"
          0x04 u32  archive_length (= 0x800 + sum(各分区补齐后的长度))
          0x08 char[0x20] MACHINE_MODEL
          0x2A char[?]    MACHINE_ID
          0x50 char[?]    MANUFACTURER
          0x88 u16  num_parts
          0x8A u16  0x0801
  [0x08C] 分区表，每项 0x70 字节
          +0x00 char[0x20] name
          +0x20 char[0x3c] filename
          +0x5c u32 nand_size (扇区)
          +0x60 u32 pos       (相对 RKAF 起始)
          +0x64 u32 addr      (起始扇区; 非分区用 0xffffffff)
          +0x68 u32 alloc     (占用空间, 单位 0x800)
          +0x6c u32 actual    (真实文件长度)
  补齐到 0x800
  [0x800] 各分区数据，顺序排列, 每个都补齐到 0x800 的整数倍（补 0）
"""
import hashlib
import os
import struct

RKAF_HDR = 0x8C
RKAF_ENT = 0x70
RKAF_DATA = 0x800
RKFW_HDR = 0x66
MD5_LEN = 32


def parse_rkfw(path):
    """解析 RKFW -> dict(header 各字段, loader, rkaf_bytes, trailer4, md5_str)"""
    with open(path, 'rb') as f:
        blob = f.read()
    if blob[:4] != b'RKFW':
        raise ValueError('not RKFW: %r' % blob[:4])
    (hsz,) = struct.unpack_from('<H', blob, 4)
    (ver,) = struct.unpack_from('<I', blob, 8)
    (date,) = struct.unpack_from('<I', blob, 0x0C)
    chip = blob[0x15:0x19]
    loff, llen, ioff, ilen = struct.unpack_from('<IIII', blob, 0x19)
    loader = blob[loff:loff + llen]
    rkaf = blob[ioff:ioff + ilen]
    trailer4 = rkaf[-4:]
    md5_str = blob[-MD5_LEN:].decode('ascii')
    return dict(size=len(blob), hdr_size=hsz, ver=ver, date=date, chip=chip,
                loader_off=loff, loader_len=llen, image_off=ioff, image_len=ilen,
                loader=loader, rkaf=rkaf[:-4], trailer4=trailer4, md5_str=md5_str,
                md5_ok=hashlib.md5(blob[:-MD5_LEN]).hexdigest() == md5_str,
                hdr=blob[:RKFW_HDR])


def parse_rkaf(rkaf):
    """解析 RKAF archive -> (header bytes, parts list)"""
    if rkaf[:4] != b'RKAF':
        raise ValueError('not RKAF: %r' % rkaf[:4])
    (length,) = struct.unpack_from('<I', rkaf, 4)
    (num,) = struct.unpack_from('<H', rkaf, 0x88)
    parts = []
    for i in range(num):
        e = rkaf[RKAF_HDR + i * RKAF_ENT: RKAF_HDR + (i + 1) * RKAF_ENT]
        name = e[0x00:0x20].split(b'\0')[0].decode()
        fn = e[0x20:0x5C].split(b'\0')[0].decode()
        nandsz, pos, addr, alloc, actual = struct.unpack_from('<IIIII', e, 0x5C)
        data = rkaf[pos:pos + alloc * 0x800]
        parts.append(dict(name=name, file=fn, nand_size=nandsz, pos=pos, addr=addr,
                          alloc=alloc, actual=actual, data=data[:actual]))
    return rkaf[:RKAF_HDR], parts


def build_parm(text: bytes, trailer4=b'\x5c\x85\x93\x80'):
    """parameter 在 RKAF 里以 PARM 容器存放: 'PARM' + u32 len + text + 4字节"""
    return b'PARM' + struct.pack('<I', len(text)) + text + trailer4


def build_rkaf(parts, header_template):
    """
    parts: list of dict(name, file, data, addr, nand_size)
    header_template: 0x8C 字节的 RKAF 头部模板（沿用厂商 ROM，patch 长度与分区数）
    """
    hdr = bytearray(header_template)
    table = bytearray()
    data = bytearray()
    pos = RKAF_DATA
    for p in parts:
        d = p['data']
        padded = (len(d) + 0x7FF) // 0x800 * 0x800
        alloc = padded // 0x800
        name = p['name'].encode()
        fn = p.get('file', p['name']).encode()
        e = bytearray(RKAF_ENT)
        e[0x00:0x00 + len(name)] = name
        e[0x20:0x20 + len(fn)] = fn
        struct.pack_into('<IIIII', e, 0x5C, p.get('nand_size', 0), pos,
                         p.get('addr', 0xffffffff), alloc, len(d))
        table += e
        data += d + b'\0' * (padded - len(d))
        pos += padded
    struct.pack_into('<I', hdr, 4, pos)              # archive_length = 0x800 + 各分区补齐后长度之和
    struct.pack_into('<H', hdr, 0x88, len(parts))
    blob = bytes(hdr) + bytes(table)
    blob += b'\0' * (RKAF_DATA - len(blob))
    return blob + bytes(data)


def build_rkfw(loader, rkaf, header_template, trailer4=b'\xb7\x1b\x11\x76'):
    hdr = bytearray(header_template)
    loff = RKFW_HDR
    ioff = loff + len(loader)
    struct.pack_into('<IIII', hdr, 0x19, loff, len(loader), ioff, len(rkaf) + 4)
    blob = bytes(hdr) + loader + rkaf + trailer4
    return blob + hashlib.md5(blob).hexdigest().encode()


class _Md5Writer:
    """边写边算 MD5 的输出流（RKFW 末尾的 32 字节 ASCII MD5 = 前面所有内容的 MD5）"""

    def __init__(self, fh):
        self.fh = fh
        self.h = hashlib.md5()
        self.n = 0

    def write(self, data):
        self.fh.write(data)
        self.h.update(data)
        self.n += len(data)

    def write_file(self, path, chunk=1 << 22):
        with open(path, 'rb') as f:
            while True:
                b = f.read(chunk)
                if not b:
                    break
                self.write(b)

    def zeros(self, count, chunk=1 << 22):
        z = b'\0' * chunk
        while count > 0:
            n = min(count, chunk)
            self.write(z[:n])
            count -= n


def plan_parts(parts):
    """计算每个分区的 pos / alloc / padded，返回 (table_bytes, total_len)
    parts: list of dict(name,file,size,addr,nand_size)"""
    table = bytearray()
    pos = RKAF_DATA
    for p in parts:
        padded = (p['size'] + 0x7FF) // 0x800 * 0x800
        name = p['name'].encode()
        fn = p.get('file', p['name']).encode()
        e = bytearray(RKAF_ENT)
        e[0x00:0x00 + len(name)] = name
        e[0x20:0x20 + len(fn)] = fn
        struct.pack_into('<IIIII', e, 0x5C, p.get('nand_size', 0), pos,
                         p.get('addr', 0xffffffff), padded // 0x800, p['size'])
        table += e
        p['pos'] = pos
        p['padded'] = padded
        pos += padded
    return bytes(table), pos


def write_rkfw(out_path, loader_path, parts, rkfw_hdr_template, rkaf_hdr_template,
               trailer4=b'\xb7\x1b\x11\x76', parm_trailer4=b'\x5c\x85\x93\x80'):
    """
    parts: list of dict(name, file, addr=, nand_size=, path= or data=)
           name == 'parameter' 时 data 会被包成 PARM 容器
    """
    prepared = []
    for p in parts:
        if p['name'] == 'parameter':
            txt = p['data'] if 'data' in p else open(p['path'], 'rb').read()
            data = build_parm(txt, parm_trailer4)
            prepared.append(dict(name=p['name'], file=p.get('file', p['name']),
                                 size=len(data), addr=p.get('addr', 0xffffffff),
                                 nand_size=p.get('nand_size', 0), data=data))
        else:
            if 'data' in p:
                d = p['data']
                size = p.get('size') or len(d)
                prepared.append(dict(name=p['name'], file=p.get('file', p['name']),
                                     size=size, addr=p.get('addr', 0xffffffff),
                                     nand_size=p.get('nand_size', 0), data=d))
            else:
                size = p.get('size') or os.path.getsize(p['path'])
                prepared.append(dict(name=p['name'], file=p.get('file', p['name']),
                                     size=size, addr=p.get('addr', 0xffffffff),
                                     nand_size=p.get('nand_size', 0), path=p['path']))
    table, rkaf_len = plan_parts(prepared)
    hdr = bytearray(rkaf_hdr_template[:RKAF_HDR])
    struct.pack_into('<I', hdr, 4, rkaf_len)
    struct.pack_into('<H', hdr, 0x88, len(prepared))
    rkaf_head = bytes(hdr) + table
    rkaf_head += b'\0' * (RKAF_DATA - len(rkaf_head))
    loader_size = os.path.getsize(loader_path)
    wh = bytearray(rkfw_hdr_template[:RKFW_HDR])
    struct.pack_into('<IIII', wh, 0x19, RKFW_HDR, loader_size, RKFW_HDR + loader_size,
                     rkaf_len + 4)
    with open(out_path, 'wb') as fh:
        w = _Md5Writer(fh)
        w.write(bytes(wh))
        w.write_file(loader_path)
        w.write(rkaf_head)
        for p in prepared:
            if 'data' in p:
                w.write(p['data'])
            else:
                w.write_file(p['path'])
            if p['padded'] > p['size']:
                w.zeros(p['padded'] - p['size'])
        w.write(trailer4)
        fh.write(w.h.hexdigest().encode())
    return out_path


# ---------------- 自检：把厂商 ROM 拆开再按本实现重新打包，逐字节比对 ----------------
if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('用法: %s <厂商 ROM 路径>    # 自检：解析并重新打包，逐字节比对' % sys.argv[0])
        sys.exit(1)
    src = sys.argv[1]
    info = parse_rkfw(src)
    print('解析 %s' % src)
    print('  chip=%s ver=%#x date=%#x loader=%d image=%d trailer4=%s md5校验=%s'
          % (info['chip'], info['ver'], info['date'], info['loader_len'],
             info['image_len'], info['trailer4'].hex(), info['md5_ok']))
    hdr, parts = parse_rkaf(info['rkaf'])
    print('  分区 %d 个:' % len(parts))
    for p in parts:
        print('    %-14s file=%-24s addr=%#010x nand_size=%#x alloc=%#x actual=%#x'
              % (p['name'], p['file'], p['addr'], p['nand_size'], p['alloc'], p['actual']))
    # 重建
    new_parts = [dict(name=p['name'], file=p['file'], data=p['data'], addr=p['addr'],
                      nand_size=p['nand_size']) for p in parts]
    rkaf2 = build_rkaf(new_parts, hdr)
    print('  RKAF 重建一致:', rkaf2 == info['rkaf'], len(rkaf2), len(info['rkaf']))
    out = build_rkfw(info['loader'], rkaf2, info['hdr'], info['trailer4'])
    with open(src, 'rb') as f:
        orig = f.read()
    print('  RKFW 重建一致:', out == orig, len(out), len(orig))
    if out != orig:
        for i in range(min(len(out), len(orig))):
            if out[i] != orig[i]:
                print('    首个差异 @', hex(i), out[i], orig[i])
                break
