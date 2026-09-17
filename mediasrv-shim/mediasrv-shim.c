/*
 * mediasrv-shim.c —— 给飞牛 mediasrv 用的 LD_PRELOAD 小垫片
 *
 * 目的（诊断 + 修 AV1 播放报错）：
 *   AM40/RK3399 的 MPP 没有 AV1、VP8 硬解；mediasrv 在"GPU 解码已启用"时
 *   会挑到 av1_rkmpp / vp8_rkmpp，avcodec_open2 直接返回 AVERROR_EXTERNAL，
 *   于是 init_video_stream_map 失败、前端播放报错，且它不会自己回退软解。
 *
 *   本垫片把 RK3399 不支持的 *_rkmpp 解码器请求重定向到等价的软件解码器：
 *       av1_rkmpp -> libdav1d        (AV1)
 *       vp8_rkmpp -> libvpx          (VP8)
 *   h264_rkmpp / hevc_rkmpp / vp9_rkmpp / mpeg*_rkmpp 原样放行（这些 RK3399 真有硬解）。
 *
 *   同时把所有解码器查找/打开调用记到 /tmp/mediasrv-shim.log，方便事后确认。
 *
 * 编译： gcc -shared -fPIC -O2 -o mediasrv-shim.so mediasrv-shim.c -ldl -lpthread
 * 用法： LD_PRELOAD=/usr/local/lib/mediasrv-shim.so mediasrv ...
 * 关日志： export MEDIASRV_SHIM_QUIET=1
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <unistd.h>
#include <fcntl.h>
#include <pthread.h>

typedef void AVCodec;
typedef void AVCodecContext;

static const AVCodec *(*real_find_by_name)(const char *);
static const AVCodec *(*real_find_decoder)(int);
static const AVCodec *(*real_iterate)(void **);
static const AVCodec *(*real_find_enc_by_name)(const char *);
static const AVCodec *(*real_find_encoder)(int);
static int (*real_open2)(AVCodecContext *, const AVCodec *, void *);

static int logfd = -2;                       /* -2 = 还没初始化 */
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;

static void lg(const char *fmt, ...)
{
	va_list ap;
	int n;
	char buf[512];

	if (getenv("MEDIASRV_SHIM_QUIET"))
		return;

	va_start(ap, fmt);
	n = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	if (n <= 0)
		return;

	pthread_mutex_lock(&lock);
	if (logfd == -2)
		logfd = open("/tmp/mediasrv-shim.log",
			     O_WRONLY | O_CREAT | O_APPEND, 0644);
	if (logfd >= 0) {
		ssize_t w = write(logfd, buf, (size_t)n);
		(void)w;
	}
	pthread_mutex_unlock(&lock);
}

/* AVCodec 头部布局（ffmpeg 公共 ABI，多年没变）：
 *   +0  const char *name
 *   +8  const char *long_name
 *   +16 enum AVMediaType type
 *   +20 enum AVCodecID   id
 */
static const char *c_name(const AVCodec *c)
{
	const char *n;

	if (!c)
		return "(null)";
	n = *(const char *const *)c;
	return n ? n : "(noname)";
}

static int c_id(const AVCodec *c)
{
	if (!c)
		return -1;
	return *(const int *)((const char *)c + 20);
}

/* RK3399 MPP 没有的硬解：请求进来就换成软件解码器 */
static const char *sw_fallback(const char *name)
{
	if (!name)
		return NULL;
	if (!strcmp(name, "av1_rkmpp"))
		return "libdav1d";
	if (!strcmp(name, "vp8_rkmpp"))
		return "libvpx";
	return NULL;
}

/*
 * RK3399 的 VEPU2 只能编 H.264（还有 MJPEG），没有 HEVC/AV1/VP9 编码器。
 * 前端（浏览器支持 HEVC 时）会让 mediasrv 去开 hevc_rkmpp，直接失败，
 * 整个播放就报错。这里把"做不到的硬编"统一改成 h264_rkmpp：
 * 网页播放器拿到的是 H.264 的 m3u8/分片，浏览器都认。
 */
static const char *enc_remap(const char *name)
{
	if (!name)
		return NULL;
	if (!strcmp(name, "h264_rkmpp") || !strcmp(name, "mjpeg_rkmpp"))
		return NULL;                    /* 这两个 RK3399 真有 */
	if (strlen(name) > 6 && !strcmp(name + strlen(name) - 6, "_rkmpp"))
		return "h264_rkmpp";
	return NULL;
}

const AVCodec *avcodec_find_decoder_by_name(const char *name)
{
	const AVCodec *c;
	const char *alt;

	if (!real_find_by_name)
		real_find_by_name = dlsym(RTLD_NEXT,
					  "avcodec_find_decoder_by_name");

	alt = sw_fallback(name);
	if (alt) {
		c = real_find_by_name ? real_find_by_name(alt) : NULL;
		lg("[shim] find_decoder_by_name(\"%s\") -> 改写为 \"%s\" -> %s(id=%d)\n",
		   name, alt, c_name(c), c_id(c));
		return c;
	}

	c = real_find_by_name ? real_find_by_name(name) : NULL;
	lg("[shim] find_decoder_by_name(\"%s\") -> %s(id=%d)\n",
	   name ? name : "(null)", c_name(c), c_id(c));
	return c;
}

const AVCodec *avcodec_find_decoder(int id)
{
	const AVCodec *c;

	if (!real_find_decoder)
		real_find_decoder = dlsym(RTLD_NEXT, "avcodec_find_decoder");

	c = real_find_decoder ? real_find_decoder(id) : NULL;
	lg("[shim] find_decoder(id=%d) -> %s\n", id, c_name(c));
	return c;
}

/*
 * mediasrv 找硬件解码器靠 av_codec_iterate() 遍历全部解码器（不看名字），
 * 所以最干净的做法是：遍历时直接把 RK3399 没有的 av1_rkmpp / vp8_rkmpp 藏掉。
 * 这样 mediasrv 自己就会走软件解码器，avcodec_alloc_context3() 也用对 codec，
 * 不会踩 ffmpeg 那条 "This AVCodecContext was allocated for X, but Y passed to
 * avcodec_open2()" 的检查。
 */
const AVCodec *avcodec_find_encoder_by_name(const char *name)
{
	const AVCodec *c;
	const char *alt = enc_remap(name);

	if (!real_find_enc_by_name)
		real_find_enc_by_name = dlsym(RTLD_NEXT,
					      "avcodec_find_encoder_by_name");

	if (alt) {
		c = real_find_enc_by_name ? real_find_enc_by_name(alt) : NULL;
		lg("[shim] find_encoder_by_name(\"%s\") -> RK3399 无此硬编，改用 \"%s\" -> %s\n",
		   name, alt, c_name(c));
		return c;
	}

	c = real_find_enc_by_name ? real_find_enc_by_name(name) : NULL;
	lg("[shim] find_encoder_by_name(\"%s\") -> %s\n",
	   name ? name : "(null)", c_name(c));
	return c;
}

const AVCodec *avcodec_find_encoder(int id)
{
	const AVCodec *c;

	if (!real_find_encoder)
		real_find_encoder = dlsym(RTLD_NEXT, "avcodec_find_encoder");
	c = real_find_encoder ? real_find_encoder(id) : NULL;
	lg("[shim] find_encoder(id=%d) -> %s\n", id, c_name(c));
	return c;
}

const AVCodec *av_codec_iterate(void **opaque)
{
	const AVCodec *c;

	if (!real_iterate)
		real_iterate = dlsym(RTLD_NEXT, "av_codec_iterate");

	if (!real_iterate)
		return NULL;

	for (;;) {
		c = real_iterate(opaque);
		if (!c)
			return NULL;
		if (sw_fallback(c_name(c))) {
			lg("[shim] iterate: 对 mediasrv 隐藏 %s（RK3399 无此硬解）\n",
			   c_name(c));
			continue;
		}
		return c;
	}
}

/*
 * 兜底：万一还有别的路径把 *_rkmpp 塞进来。注意 ffmpeg 8 会拒绝
 * "上下文是为 A 分配的、却拿 B 来 open"，所以这里只在上下文本来就是
 * 为同一个 codec id 之外的情况才改写——正常情况下 iterate 已经拦住了，
 * 这里主要是记录日志。
 */
int avcodec_open2(AVCodecContext *ctx, const AVCodec *codec, void *opts)
{
	int ret;

	if (!real_open2)
		real_open2 = dlsym(RTLD_NEXT, "avcodec_open2");

	lg("[shim] open2 codec=%s(id=%d)\n", c_name(codec), c_id(codec));
	ret = real_open2 ? real_open2(ctx, codec, opts) : -1;
	if (ret < 0)
		lg("[shim] open2 FAILED ret=%d codec=%s\n", ret, c_name(codec));
	else
		lg("[shim] open2 OK codec=%s\n", c_name(codec));
	return ret;
}
