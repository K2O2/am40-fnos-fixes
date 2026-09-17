obj-m += rk_vcodec.o
rk_vcodec-objs := mpp_service.o mpp_common.o mpp_iommu.o \
                  mpp_rkvdec.o mpp_vdpu2.o mpp_vepu2.o mpp_jpgdec.o mpp_jpgenc.o
ccflags-y += -DCONFIG_CPU_RK3399 \
             -DCONFIG_ROCKCHIP_MPP_RKVDEC -DCONFIG_ROCKCHIP_MPP_VDPU2 \
             -DCONFIG_ROCKCHIP_MPP_VEPU2 -DCONFIG_ROCKCHIP_MPP_JPGDEC \
             -DCONFIG_ROCKCHIP_MPP_JPGENC -DCONFIG_ROCKCHIP_MPP_PROC_FS \
             -I$(src) -I$(src)/hack
CFLAGS_mpp_service.o += -DMPP_VERSION="\"custom-rk3399-full-v3\""
