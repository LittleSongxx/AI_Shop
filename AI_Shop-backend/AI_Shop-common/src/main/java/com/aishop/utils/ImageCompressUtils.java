package com.aishop.utils;

import com.aishop.constants.Constants;
import com.aishop.entity.enums.ResponseCodeEnum;
import com.aishop.exception.BusinessException;
import lombok.Getter;
import lombok.extern.slf4j.Slf4j;
import net.coobird.thumbnailator.Thumbnails;

import javax.imageio.ImageIO;
import javax.imageio.ImageReader;
import javax.imageio.stream.ImageInputStream;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.util.Iterator;
import java.util.Set;

@Slf4j
public final class ImageCompressUtils {

    private static final int MAX_EDGE = 4096;

    private static final int BAIDU_MIN_EDGE = 128;
    private static final int BAIDU_MIN_BYTES = 3840;
    private static final float MIN_QUALITY = 0.35f;
    private static final float QUALITY_STEP = 0.07f;
    private static final Set<String> KEEP_ORIGINAL_SUFFIX = Set.of(".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp");

    private static final Set<String> FORCE_TRANSCODE_SUFFIX = Set.of(".heic", ".heif", ".heics", ".heifs");

    private ImageCompressUtils() {
    }

    @Getter
    public static class PreparedImage {
        private final byte[] data;
        private final String suffix;

        public PreparedImage(byte[] data, String suffix) {
            this.data = data;
            this.suffix = suffix;
        }
    }

    public static PreparedImage prepare(byte[] source, String originalFilename) {
        if (source == null || source.length == 0) {
            throw new BusinessException(ResponseCodeEnum.CODE_600.getCode(), "请选择要上传的图片");
        }

        String suffix = normalizeSuffix(StringTools.getFileSuffix(originalFilename));
        if (needsTranscodeToJpeg(suffix, source)) {
            log.info("图片格式 {} 需转码为 JPEG: {}", suffix, originalFilename);
            return transcodeToJpeg(source, suffix);
        }

        if (source.length <= Constants.MAX_IMAGE_UPLOAD_BYTES) {
            return new PreparedImage(source, suffix);
        }

        log.info("图片超过 5MB（{} bytes），开始压缩: {}", source.length, originalFilename);

        try {
            PreparedImage compressed = compressWithThumbnailator(source);
            log.info("Java 压缩完成: {} -> {} bytes", source.length, compressed.getData().length);
            return compressed;
        } catch (BusinessException biz) {
            throw biz;
        } catch (Exception javaEx) {
            log.warn("Java 图片压缩失败，尝试 ffmpeg: {}", javaEx.getMessage());
        }

        try {
            PreparedImage compressed = compressWithFfmpeg(source, suffix);
            log.info("ffmpeg 压缩完成: {} -> {} bytes", source.length, compressed.getData().length);
            return compressed;
        } catch (BusinessException biz) {
            throw biz;
        } catch (Exception ffmpegEx) {
            log.error("ffmpeg 图片压缩失败: {}", ffmpegEx.getMessage());
            throw compressFailed();
        }
    }

    /** Normalize Agent images before moderation/storage and strip embedded metadata. */
    public static PreparedImage prepareAgentImage(byte[] source, String originalFilename) {
        if (source == null || source.length == 0) {
            throw new BusinessException(ResponseCodeEnum.CODE_600.getCode(), "请选择要上传的图片");
        }
        if (source.length > 10L * 1024 * 1024) {
            throw new BusinessException(ResponseCodeEnum.CODE_600.getCode(), "图片不能超过10MB");
        }
        if (detectImageMime(source) == null) {
            throw new BusinessException(ResponseCodeEnum.CODE_600.getCode(), "仅支持 JPEG、PNG、GIF、WebP 或 BMP 图片");
        }
        try {
            int[] dimensions = readImageDimensions(source);
            long pixels = (long) dimensions[0] * dimensions[1];
            if (dimensions[0] < 16 || dimensions[1] < 16 || pixels > 25_000_000L) {
                throw new BusinessException(ResponseCodeEnum.CODE_600.getCode(), "图片尺寸不符合要求");
            }
            return compressWithThumbnailator(source);
        } catch (BusinessException e) {
            throw e;
        } catch (IOException e) {
            throw new BusinessException(ResponseCodeEnum.CODE_600.getCode(), "图像无法解析，请更换图片");
        }
    }

    private static int[] readImageDimensions(byte[] source) throws IOException {
        try (ImageInputStream input = ImageIO.createImageInputStream(new ByteArrayInputStream(source))) {
            if (input == null) {
                throw new IOException("无法创建图像输入流");
            }
            Iterator<ImageReader> readers = ImageIO.getImageReaders(input);
            if (!readers.hasNext()) {
                throw new IOException("没有可用的图像解码器");
            }
            ImageReader reader = readers.next();
            try {
                reader.setInput(input, true, true);
                return new int[]{reader.getWidth(0), reader.getHeight(0)};
            } finally {
                reader.dispose();
            }
        }
    }

    private static String detectImageMime(byte[] source) {
        if (source.length >= 3
                && (source[0] & 0xff) == 0xff
                && (source[1] & 0xff) == 0xd8
                && (source[2] & 0xff) == 0xff) {
            return "image/jpeg";
        }
        if (source.length >= 8
                && (source[0] & 0xff) == 0x89
                && source[1] == 0x50 && source[2] == 0x4e && source[3] == 0x47
                && source[4] == 0x0d && source[5] == 0x0a
                && source[6] == 0x1a && source[7] == 0x0a) {
            return "image/png";
        }
        if (source.length >= 6) {
            String signature = new String(source, 0, 6, java.nio.charset.StandardCharsets.US_ASCII);
            if ("GIF87a".equals(signature) || "GIF89a".equals(signature)) {
                return "image/gif";
            }
        }
        if (source.length >= 12
                && source[0] == 'R' && source[1] == 'I' && source[2] == 'F' && source[3] == 'F'
                && source[8] == 'W' && source[9] == 'E' && source[10] == 'B' && source[11] == 'P') {
            return "image/webp";
        }
        if (source.length >= 2 && source[0] == 'B' && source[1] == 'M') {
            return "image/bmp";
        }
        return null;
    }

    private static boolean needsTranscodeToJpeg(String suffix, byte[] source) {
        if (FORCE_TRANSCODE_SUFFIX.contains(suffix)) {
            return true;
        }
        try {
            return ImageIO.read(new ByteArrayInputStream(source)) == null;
        } catch (IOException e) {
            return true;
        }
    }

    private static PreparedImage transcodeToJpeg(byte[] source, String suffix) {
        try {
            return compressWithThumbnailator(source);
        } catch (Exception javaEx) {
            log.warn("Java 转码失败，尝试 ffmpeg: {}", javaEx.getMessage());
        }
        try {
            return compressWithFfmpeg(source, suffix);
        } catch (Exception ffmpegEx) {
            log.error("ffmpeg 转码失败: {}", ffmpegEx.getMessage());
            throw compressFailed();
        }
    }

    private static BusinessException compressFailed() {
        return new BusinessException(ResponseCodeEnum.CODE_600.getCode(),
                "图片过大或格式不支持，无法压缩到5MB以内");
    }

    public static byte[] prepareForBaiduCensor(byte[] source) {
        if (source == null || source.length == 0) {
            throw new BusinessException(ResponseCodeEnum.CODE_605.getCode(), "图片内容为空");
        }
        try {
            BufferedImage decoded = ImageIO.read(new ByteArrayInputStream(source));
            if (decoded == null) {
                log.warn("送审图 Java 解码失败，尝试 ffmpeg 转 JPEG（{} bytes）", source.length);
                source = transcodeRawToJpeg(source);
                decoded = ImageIO.read(new ByteArrayInputStream(source));
            }
            if (decoded == null) {
                throw new BusinessException(ResponseCodeEnum.CODE_605.getCode(),
                        "图像无法解析，请重新截图或换一张图片");
            }

            int w = decoded.getWidth();
            int h = decoded.getHeight();
            BufferedImage rgb = flattenToRgb(decoded);
            if (Math.min(w, h) < BAIDU_MIN_EDGE) {
                rgb = resizeImage(rgb, BAIDU_MIN_EDGE);
            }

            byte[] candidate = encodeJpegFromImage(rgb, 1.0, 0.92f);
            int upscaleMin = Math.max(BAIDU_MIN_EDGE, Math.min(w, h));
            for (int i = 0; i < 6
                    && candidate.length < BAIDU_MIN_BYTES
                    && upscaleMin < MAX_EDGE; i++) {
                upscaleMin = Math.min((int) Math.round(upscaleMin * 1.5), MAX_EDGE);
                rgb = resizeImage(flattenToRgb(decoded), upscaleMin);
                candidate = encodeJpegFromImage(rgb, 1.0, 0.92f);
            }
            if (candidate.length < BAIDU_MIN_BYTES) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                if (!ImageIO.write(rgb, "png", out)) {
                    throw new IOException("PNG 编码失败");
                }
                candidate = out.toByteArray();
            }
            log.debug("百度送审图规范化: {}x{} -> {} bytes", w, h, candidate.length);
            return candidate;
        } catch (BusinessException biz) {
            throw biz;
        } catch (IOException e) {
            log.warn("百度送审图规范化失败: {}", e.getMessage());
            throw new BusinessException(ResponseCodeEnum.CODE_605.getCode(),
                    "图像无法解析，请重新截图或换一张图片");
        }
    }

    private static byte[] transcodeRawToJpeg(byte[] source) throws IOException {
        File tempIn = Files.createTempFile("eshop_censor_in_", ".img").toFile();
        File tempOut = Files.createTempFile("eshop_censor_out_", ".jpg").toFile();
        try {
            Files.write(tempIn.toPath(), source);
            String cmd = String.format(
                    "ffmpeg -y -i \"%s\" -vf \"scale='if(lt(iw,ih),128,-2)':'if(lt(iw,ih),-2,128)'\" -map_metadata -1 -q:v 4 \"%s\"",
                    tempIn.getAbsolutePath(), tempOut.getAbsolutePath());
            ProcessUtils.executeCommand(cmd, false);
            if (!tempOut.exists() || tempOut.length() == 0) {
                throw new IOException("ffmpeg 输出为空");
            }
            return Files.readAllBytes(tempOut.toPath());
        } catch (BusinessException e) {
            throw new IOException(e.getMessage(), e);
        } finally {
            if (tempIn.exists() && !tempIn.delete()) {
                log.debug("临时文件删除失败: {}", tempIn.getAbsolutePath());
            }
            if (tempOut.exists() && !tempOut.delete()) {
                log.debug("临时文件删除失败: {}", tempOut.getAbsolutePath());
            }
        }
    }

    private static byte[] encodeJpegFromImage(BufferedImage image, double scale, float quality) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        Thumbnails.of(image)
                .scale(scale)
                .outputFormat("jpg")
                .outputQuality(quality)
                .toOutputStream(out);
        return out.toByteArray();
    }

    private static BufferedImage resizeImage(BufferedImage image, int minEdge) {
        int w = image.getWidth();
        int h = image.getHeight();
        int currentMin = Math.min(w, h);
        if (currentMin >= minEdge) {
            return image;
        }
        double scale = (double) minEdge / currentMin;
        int nw = (int) Math.round(w * scale);
        int nh = (int) Math.round(h * scale);
        BufferedImage out = new BufferedImage(nw, nh, BufferedImage.TYPE_INT_RGB);
        Graphics2D g = out.createGraphics();
        g.drawImage(image, 0, 0, nw, nh, null);
        g.dispose();
        return out;
    }

    private static String normalizeSuffix(String suffix) {
        if (StringTools.isEmpty(suffix)) {
            return ".jpg";
        }
        String lower = suffix.toLowerCase();
        return lower.startsWith(".") ? lower : "." + lower;
    }

    private static PreparedImage compressWithThumbnailator(byte[] source) throws IOException {
        BufferedImage decoded = ImageIO.read(new ByteArrayInputStream(source));
        if (decoded == null) {
            throw new IOException("无法解码图片");
        }

        double scale = Math.min(1.0, (double) MAX_EDGE / Math.max(decoded.getWidth(), decoded.getHeight()));
        byte[] best = null;
        double currentScale = scale;

        while (currentScale >= 0.25) {
            float quality = 0.92f;
            while (quality >= MIN_QUALITY) {
                byte[] candidate = encodeJpegFromStream(source, currentScale, quality);
                if (candidate.length <= Constants.MAX_IMAGE_UPLOAD_BYTES) {
                    return new PreparedImage(candidate, ".jpg");
                }
                if (best == null || candidate.length < best.length) {
                    best = candidate;
                }
                quality -= QUALITY_STEP;
            }
            currentScale *= 0.85;
        }

        if (best != null && best.length <= Constants.MAX_IMAGE_UPLOAD_BYTES) {
            return new PreparedImage(best, ".jpg");
        }
        throw new IOException("Java 压缩后仍超过 5MB");
    }

    private static byte[] encodeJpegFromStream(byte[] source, double scale, float quality) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        try {
            Thumbnails.of(new ByteArrayInputStream(source))
                    .scale(scale)
                    .useExifOrientation(true)
                    .outputFormat("jpg")
                    .outputQuality(quality)
                    .toOutputStream(out);
            return out.toByteArray();
        } catch (IOException ex) {
            BufferedImage image = ImageIO.read(new ByteArrayInputStream(source));
            if (image == null) {
                throw ex;
            }
            BufferedImage rgb = flattenToRgb(image);
            out.reset();
            Thumbnails.of(rgb)
                    .scale(scale)
                    .outputFormat("jpg")
                    .outputQuality(quality)
                    .toOutputStream(out);
            return out.toByteArray();
        }
    }

    private static BufferedImage flattenToRgb(BufferedImage image) {
        BufferedImage rgb = new BufferedImage(image.getWidth(), image.getHeight(), BufferedImage.TYPE_INT_RGB);
        Graphics2D g = rgb.createGraphics();
        g.setColor(Color.WHITE);
        g.fillRect(0, 0, rgb.getWidth(), rgb.getHeight());
        g.drawImage(image, 0, 0, null);
        g.dispose();
        return rgb;
    }

    private static PreparedImage compressWithFfmpeg(byte[] source, String suffix) throws IOException {
        String ext = KEEP_ORIGINAL_SUFFIX.contains(suffix) ? suffix : ".img";
        File tempIn = Files.createTempFile("eshop_upload_in_", ext).toFile();
        File tempOut = Files.createTempFile("eshop_upload_out_", ".jpg").toFile();
        try {
            Files.write(tempIn.toPath(), source);
            int maxEdge = MAX_EDGE;
            int qv = 4;
            byte[] best = null;

            for (int round = 0; round < 10; round++) {
                Files.deleteIfExists(tempOut.toPath());
                String cmd = String.format(
                        "ffmpeg -y -i \"%s\" -vf \"scale='min(%d,iw)':-2\" -map_metadata -1 -q:v %d \"%s\"",
                        tempIn.getAbsolutePath(), maxEdge, qv, tempOut.getAbsolutePath());
                try {
                    ProcessUtils.executeCommand(cmd, false);
                } catch (BusinessException e) {
                    log.warn("ffmpeg 执行失败: {}", e.getMessage());
                    break;
                }

                if (!tempOut.exists() || tempOut.length() == 0) {
                    break;
                }
                byte[] candidate = Files.readAllBytes(tempOut.toPath());
                if (candidate.length <= Constants.MAX_IMAGE_UPLOAD_BYTES) {
                    return new PreparedImage(candidate, ".jpg");
                }
                if (best == null || candidate.length < best.length) {
                    best = candidate;
                }
                qv += 3;
                if (qv > 28) {
                    qv = 4;
                    maxEdge = (int) Math.max(640, maxEdge * 0.85);
                }
            }

            if (best != null && best.length <= Constants.MAX_IMAGE_UPLOAD_BYTES) {
                return new PreparedImage(best, ".jpg");
            }
            throw new IOException("ffmpeg 压缩后仍超过 5MB");
        } finally {
            if (tempIn.exists() && !tempIn.delete()) {
                log.debug("临时文件删除失败: {}", tempIn.getAbsolutePath());
            }
            if (tempOut.exists() && !tempOut.delete()) {
                log.debug("临时文件删除失败: {}", tempOut.getAbsolutePath());
            }
        }
    }
}
