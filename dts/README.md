# 设备树层：绑定修复

| 文件 | 用途 |
|---|---|
| `am40-dtb.patch` | **相对 fnOS 出厂 DTB 的完整改动**（154 行，可直接 review） |
| `rk3399-smart-am40-mpp-rga.dts` | 修正后的设备树源码（本仓库默认使用） |
| `rk3399-smart-am40-mpp-full.dts` | 满编 MPP、但没有 RGA 修正（对照 / 回滚用） |

## 三处改动

1. **`rga@ff680000` 的 compatible：`"rockchip,rk3399-rga"` → `"rockchip,rga2"`**

   飞牛只发 vendor 版 `rga3.ko`，它的 `of_match` 只认
   `rockchip,rga2` / `rockchip,rga2_core0` / `rockchip,rga3` / `rockchip,rga3_core0` / `rockchip,rga3_core1`
   （`modinfo -F alias` 为空，连 autoload 别名都没有）；而出厂 DTB 用的是上游 mainline 的名字。
   结果：模块按名字能 `insmod`，但没有任何 platform driver 认领该节点 →
   `num_of_scheduler = 0` → `rga_iommu_bind()` 三个索引全 -1 → `-EFAULT`，模块 init 整体回退：

   ```text
   rga_iommu: rga_iommu_bind, binding map scheduler failed!
   rga: rga iommu bind failed!
   modprobe: ERROR: could not insert 'rga3': Bad address
   ```

   没有 `/dev/rga` → `librga.so.2.1.0` 一调用就 SEGV → **mediasrv 一启动就崩**。

   参考实现：官方 6.1 BSP 的 `rk3399-linux.dtsi` 与 4.4 的 `rk3399-firefly-android.dts`
   都写 `compatible = "rockchip,rga2"`，而 `rockchip_linux_defconfig` 里开的正是
   `CONFIG_ROCKCHIP_MULTI_RGA=y`（就是编出这个 `rga3.ko` 的开关）。

   > 这一项**不需要整棵 DTB**，一条命令即可（`am40-apply-fixes` 的自愈路径就是它）：
   > `fdtput -t s <dtb> /rga@ff680000 compatible "rockchip,rga2"`

2. **新增 vendor MPP 绑定节点**：`mpp-srv` / `vepu@ff650000` / `vdpu@ff650400` / `rkvdec@ff660000`

   vendor MPP 的每个子驱动 probe 时要求 `compatible` 命中 **且** 具备
   `rockchip,srv` + `rockchip,taskqueue-node` + `rockchip,resetgroup-node`（外加 `iommus`/`power-domains`）。
   mainline 的 `video-codec@ff650000`（`rockchip,rk3399-vpu`）这些属性一个都没有，
   所以出厂 DTB 下 MPP 根本没有可绑定的设备 —— 这也是 H.265/VP9/4K 用不了的另一半原因。

   注意几个容易写错的细节：

   * `vepu`/`vdpu` 共用 **`iommu@ff650800`**，而 `rkvdec` 用**另一块** **`iommu@ff660480`**；
   * `rkvdec` 的队列/复位组是 **1**（`vepu`/`vdpu` 是 0），并需要 6 路 reset
     （`SRST_{H,A}_VDU{,_NOC}`、`SRST_VDU_CA`、`SRST_VDU_CORE`）；
   * 电源域：`vepu`/`vdpu` 是 `RK3399_PD_VCODEC`(31)，`rkvdec` 是 **`RK3399_PD_VDU`(32)**。

3. **把 mainline 的 `video-codec@ff660000` / `vpu@ff650000` 置 `status = "disabled"`**，避免两个驱动抢同一块地址。

## 编译与使用

```sh
dtc -I dts -O dtb -o rk3399-smart-am40-mpp-rga.dtb rk3399-smart-am40-mpp-rga.dts
sudo cp rk3399-smart-am40-mpp-rga.dtb /boot/dtb/rockchip/
sudo sed -i 's#^fdtfile=.*#fdtfile=rockchip/rk3399-smart-am40-mpp-rga.dtb#' /boot/fnEnv.txt
sudo reboot
```

## 校验

```sh
# DTB 里
fdtget -t s /boot/dtb/rockchip/rk3399-smart-am40-mpp-rga.dtb /rga@ff680000 compatible
# 运行中的设备树
tr '\0' ' ' < /proc/device-tree/rga@ff680000/compatible
# 四个驱动是否都绑上了
for d in mpp_vepu2 mpp_vdpu2 mpp_rkvdec rga2; do
  printf '%-12s ' $d; ls /sys/bus/platform/drivers/$d/ | grep -vE '^(module|bind|unbind|uevent)$'
done
# 期望：ff650000.vepu / ff650400.vdpu / ff660000.rkvdec / ff680000.rga
```

## 关于时钟

节点里的 `rockchip,normal-rates` / `advanced-rates` 保持厂商默认 **300 MHz**。
**不要为了提速去拉高它** —— 数据与原因见 `../kernel-mpp/README.md` 的「不要超频」一节。

## 移植到其它 RK3399 板子

第 1 项（RGA compatible）与第 3 项通用；第 2 项的节点内容也通用，但
`clocks`/`resets`/`power-domains` 的 phandle 与编号需要按各自 DTS 改写。
