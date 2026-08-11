package com.aishop.biz.impl;

import com.aishop.component.BaiduImageCensorComponent;
import com.aishop.component.ImageCensorRateLimitService;
import com.aishop.component.UserTempBanService;
import com.aishop.constants.Constants;
import com.aishop.entity.dto.BaiduImageCensorResultDTO;
import com.aishop.api.dto.ImageUploadResultDTO;
import com.aishop.api.dto.VerifiedImageAssetDTO;
import com.aishop.api.OrderFeignClient;
import com.aishop.api.dto.OrderIdDTO;
import com.aishop.api.support.FeignResponseSupport;
import com.aishop.api.vo.OrderBriefVO;
import com.aishop.api.enums.ImageModerationSceneEnum;
import com.aishop.api.enums.ImageModerationStatusEnum;
import com.aishop.entity.enums.PageSize;
import com.aishop.entity.po.ImageModerationRecord;
import com.aishop.entity.query.ImageModerationRecordQuery;
import com.aishop.entity.query.SimplePage;
import com.aishop.entity.vo.PaginationResultVO;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.ImageModerationRecordMapper;
import com.aishop.biz.ImageModerationService;
import com.aishop.storage.ImageAssetStore;
import com.aishop.utils.FileUtils;
import com.aishop.utils.ImageCompressUtils;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Arrays;
import java.util.Date;
import java.util.HexFormat;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

@Service("imageModerationService")
@Slf4j
public class ImageModerationServiceImpl implements ImageModerationService {

    private static final int TEMP_BAN_HOURS = 2;
    private static final Pattern AGENT_ASSET_ID = Pattern.compile("^img_[a-f0-9]{32}$");
    private static final String RETENTION_QUERY_30D = "QUERY_30D";
    private static final String RETENTION_SUPPORT_EVIDENCE = "SUPPORT_EVIDENCE";
    private static final int QUERY_RETENTION_DAYS = 30;
    private static final int CLEANUP_BATCH_SIZE = 200;

    @Resource
    private FileUtils fileUtils;
    @Resource
    private ImageAssetStore imageAssetStore;
    @Resource
    private BaiduImageCensorComponent baiduImageCensorComponent;
    @Resource
    private ImageCensorRateLimitService imageCensorRateLimitService;
    @Resource
    private UserTempBanService userTempBanService;
    @Resource
    private ImageModerationRecordMapper<ImageModerationRecord, ImageModerationRecordQuery> imageModerationRecordMapper;
    @Resource
    private OrderFeignClient orderFeignClient;
    @Resource
    private FeignResponseSupport feignResponseSupport;
    @Value("${image-moderation.orphan-upload-hours:24}")
    private int orphanUploadHours;

    @Override
    public ImageUploadResultDTO uploadAndModerate(String userId, String userIp, MultipartFile file,
                                                  Boolean createThumbnail, String scene, String orderId) {
        ImageModerationSceneEnum sceneEnum = ImageModerationSceneEnum.getByCode(scene);
        if (sceneEnum == null) {
            throw new BusinessException(600, "不支持的图片使用场景");
        }
        ImageCompressUtils.PreparedImage prepared = prepareForScene(file, sceneEnum);
        AssetMetadata assetMetadata = ImageModerationSceneEnum.AGENT.equals(sceneEnum)
                ? inspectAgentAsset(prepared) : null;
        String assetId = ImageModerationSceneEnum.AGENT.equals(sceneEnum)
                ? "img_" + UUID.randomUUID().toString().replace("-", "") : null;
        Date expiresAt = ImageModerationSceneEnum.AGENT.equals(sceneEnum)
                ? Date.from(Instant.now().plus(QUERY_RETENTION_DAYS, ChronoUnit.DAYS)) : null;
        byte[] censorBytes = ImageCompressUtils.prepareForBaiduCensor(prepared.getData());
        BaiduImageCensorResultDTO result = censorImageBytes(censorBytes, userId, userIp);
        if (result.isPass()) {
            if (ImageModerationSceneEnum.AGENT.equals(sceneEnum)) {
                String storageKey = imageAssetStore.save(prepared, true);
                try {
                    ImageModerationRecord record = saveRecord(
                            userId, userIp, storageKey, sceneEnum.getCode(), null, result,
                            ImageModerationStatusEnum.APPROVED.getStatus(), assetId,
                            assetMetadata, RETENTION_QUERY_30D, expiresAt);
                    return uploadResult(
                            null, false, record, ImageModerationStatusEnum.APPROVED,
                            sceneEnum.getCode());
                } catch (RuntimeException exception) {
                    imageAssetStore.deleteWithThumbnail(storageKey);
                    throw exception;
                }
            }
            String path = fileUtils.savePreparedImage(prepared, createThumbnail);
            return new ImageUploadResultDTO(path, false);
        }
        return handleCensorResult(
                userId, userIp, prepared, sceneEnum, orderId, result,
                assetId, assetMetadata, expiresAt);
    }

    private ImageCompressUtils.PreparedImage prepareForScene(
            MultipartFile file, ImageModerationSceneEnum scene) {
        if (!ImageModerationSceneEnum.AGENT.equals(scene)) {
            return fileUtils.prepareUploadImage(file);
        }
        if (file == null || file.isEmpty()) {
            throw new BusinessException(600, "请选择要上传的图片");
        }
        try {
            return ImageCompressUtils.prepareAgentImage(file.getBytes(), file.getOriginalFilename());
        } catch (IOException exception) {
            throw new BusinessException(600, "读取图片失败，请重试");
        }
    }

    @Override
    public BaiduImageCensorResultDTO censorImageBytes(byte[] imageBytes, String userId, String userIp) {
        if (baiduImageCensorComponent.isEnabled()) {
            imageCensorRateLimitService.checkUserAndIp(userId, userIp);
        }
        return baiduImageCensorComponent.censorImage(imageBytes);
    }

    private ImageUploadResultDTO handleCensorResult(
            String userId, String userIp, ImageCompressUtils.PreparedImage prepared,
            ImageModerationSceneEnum scene, String orderId, BaiduImageCensorResultDTO result,
            String assetId, AssetMetadata assetMetadata, Date expiresAt) {
        if (result.isSuspect()) {
            String quarantinePath = ImageModerationSceneEnum.AGENT.equals(scene)
                    ? imageAssetStore.saveQuarantine(prepared)
                    : fileUtils.saveModerationQuarantineImage(prepared);
            ImageModerationRecord record;
            try {
                record = saveRecord(
                        userId, userIp, quarantinePath, scene.getCode(), orderId, result,
                        ImageModerationStatusEnum.PENDING.getStatus(), assetId,
                        assetMetadata,
                        ImageModerationSceneEnum.AGENT.equals(scene) ? RETENTION_QUERY_30D : null,
                        expiresAt);
            } catch (RuntimeException exception) {
                imageAssetStore.deleteWithThumbnail(quarantinePath);
                throw exception;
            }
            if (ImageModerationSceneEnum.AGENT.equals(scene)) {
                return uploadResult(
                        null, true, record, ImageModerationStatusEnum.PENDING,
                        scene.getCode());
            }
            if (ImageModerationSceneEnum.COMMENT.equals(scene)
                    && !StringTools.isEmpty(orderId)) {
                return new ImageUploadResultDTO(quarantinePath, true);
            }
            Map<String, Object> data = new HashMap<>();
            data.put("errorType", "IMAGE_SUSPECT");
            throw new BusinessException(600, "图片存在违规风险，已提交人工审核，请更换图片后再试", data);
        }
        if (result.isReject()) {
            saveRecord(userId, userIp, null, scene.getCode(), orderId, result,
                    ImageModerationStatusEnum.VIOLATION.getStatus(), assetId,
                    assetMetadata,
                    ImageModerationSceneEnum.AGENT.equals(scene) ? RETENTION_QUERY_30D : null,
                    expiresAt);
            long unbanAt = userTempBanService.banUserHours(userId, TEMP_BAN_HOURS);
            Map<String, Object> data = new HashMap<>();
            data.put("errorType", "IMAGE_REJECT_BANNED");
            data.put("unbanAt", unbanAt);
            String msg = "图片涉嫌违规，上传已拒绝，" + userTempBanService.buildTempBanMessage(unbanAt);
            throw new BusinessException(600, msg, data);
        }
        throw new BusinessException("图片审核未通过，请更换图片后重试");
    }

    private ImageModerationRecord saveRecord(
            String userId, String userIp, String imagePath, String scene, String orderId,
            BaiduImageCensorResultDTO result, Integer status, String assetId,
            AssetMetadata assetMetadata, String retentionClass, Date expiresAt) {
        ImageModerationRecord record = new ImageModerationRecord();
        record.setUserId(userId);
        record.setUserIp(userIp);
        record.setImagePath(imagePath);
        record.setAssetId(assetId);
        if (assetMetadata != null) {
            record.setContentSha256(assetMetadata.sha256());
            record.setMimeType(assetMetadata.mimeType());
            record.setImageWidth(assetMetadata.width());
            record.setImageHeight(assetMetadata.height());
        }
        record.setRetentionClass(retentionClass);
        record.setScene(scene);
        record.setOrderId(orderId);
        record.setConclusionType(result.getConclusionType());
        record.setConclusion(result.getConclusion());
        record.setBaiduResponse(result.getRawResponse());
        record.setStatus(status);
        record.setCreateTime(new Date());
        record.setExpiresAt(expiresAt);
        imageModerationRecordMapper.insert(record);
        return record;
    }

    private ImageModerationRecord saveRecord(
            String userId, String userIp, String imagePath, String scene, String orderId,
            BaiduImageCensorResultDTO result, Integer status) {
        return saveRecord(
                userId, userIp, imagePath, scene, orderId, result, status,
                null, null, null, null);
    }

    private static ImageUploadResultDTO uploadResult(
            String path,
            boolean pendingReview,
            ImageModerationRecord record,
            ImageModerationStatusEnum status,
            String scene) {
        ImageUploadResultDTO dto = new ImageUploadResultDTO(path, pendingReview);
        dto.setModerationId(record == null ? null : record.getRecordId());
        dto.setModerationStatus(status.name());
        dto.setScene(scene);
        if (record != null && record.getAssetId() != null) {
            dto.setAssetId(record.getAssetId());
            dto.setContentSha256(record.getContentSha256());
            dto.setMimeType(record.getMimeType());
            dto.setWidth(record.getImageWidth());
            dto.setHeight(record.getImageHeight());
            dto.setExpiresAt(toIsoInstant(record.getExpiresAt()));
        }
        return dto;
    }

    @Override
    public List<ImageModerationRecord> findListByParam(ImageModerationRecordQuery param) {
        return imageModerationRecordMapper.selectList(param);
    }

    @Override
    public PaginationResultVO<ImageModerationRecord> findListByPage(ImageModerationRecordQuery param) {
        int count = imageModerationRecordMapper.selectCount(param);
        int pageSize = param.getPageSize() == null ? PageSize.SIZE15.getSize() : param.getPageSize();
        SimplePage page = new SimplePage(param.getPageNo(), count, pageSize);
        param.setSimplePage(page);
        List<ImageModerationRecord> list = findListByParam(param);
        return new PaginationResultVO<>(count, page.getPageSize(), page.getPageNo(), page.getPageTotal(), list);
    }

    @Override
    public ImageModerationRecord getByRecordId(Integer recordId) {
        return imageModerationRecordMapper.selectByRecordId(recordId);
    }

    @Override
    public VerifiedImageAssetDTO verifyAgentImage(String userId, String imageAssetId) {
        return toVerifiedAsset(requireReadableAgentAsset(userId, imageAssetId));
    }

    @Override
    public byte[] readAgentImage(String userId, String imageAssetId) {
        ImageModerationRecord record = requireReadableAgentAsset(userId, imageAssetId);
        return imageAssetStore.read(record.getImagePath());
    }

    @Override
    public void retainAgentImageAsSupportEvidence(String userId, String imageAssetId) {
        requireReadableAgentAsset(userId, imageAssetId);
        int updated = imageModerationRecordMapper.retainAsset(
                imageAssetId, RETENTION_SUPPORT_EVIDENCE);
        if (updated != 1) {
            throw new BusinessException("图片资产已过期或已被清理");
        }
    }

    private ImageModerationRecord requireReadableAgentAsset(String userId, String imageAssetId) {
        if (StringTools.isEmpty(userId)
                || StringTools.isEmpty(imageAssetId)
                || !AGENT_ASSET_ID.matcher(imageAssetId).matches()) {
            throw new BusinessException("图片资产校验参数无效");
        }
        ImageModerationRecord record = imageModerationRecordMapper.selectByAssetId(imageAssetId);
        boolean expired = record != null
                && RETENTION_QUERY_30D.equals(record.getRetentionClass())
                && record.getExpiresAt() != null
                && !record.getExpiresAt().after(new Date());
        boolean readable = record != null
                && userId.equals(record.getUserId())
                && ImageModerationSceneEnum.AGENT.getCode().equals(record.getScene())
                && ImageModerationStatusEnum.APPROVED.getStatus().equals(record.getStatus())
                && record.getPurgedAt() == null
                && !expired
                && !StringTools.isEmpty(record.getImagePath())
                && !FileUtils.isModerationQuarantinePath(record.getImagePath())
                && imageAssetStore.exists(record.getImagePath());
        if (!readable) {
            throw new BusinessException("图片资产不可用、尚未通过审核或不属于当前用户");
        }
        return record;
    }

    private static VerifiedImageAssetDTO toVerifiedAsset(ImageModerationRecord record) {
        VerifiedImageAssetDTO dto = new VerifiedImageAssetDTO();
        dto.setApproved(true);
        dto.setAssetId(record.getAssetId());
        dto.setContentSha256(record.getContentSha256());
        dto.setMimeType(record.getMimeType());
        dto.setWidth(record.getImageWidth());
        dto.setHeight(record.getImageHeight());
        dto.setScene(record.getScene());
        dto.setModerationStatus(ImageModerationStatusEnum.APPROVED.name());
        dto.setRetentionClass(record.getRetentionClass());
        dto.setExpiresAt(toIsoInstant(record.getExpiresAt()));
        return dto;
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void handleReview(Integer recordId, String action, String handleRemark) {
        ImageModerationRecord record = getByRecordId(recordId);
        if (record == null) {
            throw new BusinessException("审核记录不存在");
        }
        if (!ImageModerationStatusEnum.PENDING.getStatus().equals(record.getStatus())) {
            throw new BusinessException("该记录已处理");
        }
        if (ImageModerationSceneEnum.AGENT.getCode().equals(record.getScene())
                && (record.getPurgedAt() != null
                || (record.getExpiresAt() != null && !record.getExpiresAt().after(new Date())))) {
            throw new BusinessException("该图片资产已过期，无法继续审核");
        }
        ImageModerationRecord patch = new ImageModerationRecord();
        patch.setHandleTime(new Date());
        patch.setHandleRemark(handleRemark);
        String quarantinePathToDelete = null;
        String copiedNormalPath = null;
        switch (action == null ? "" : action) {
            case "approve" -> {
                patch.setStatus(ImageModerationStatusEnum.APPROVED.getStatus());
                if (ImageModerationSceneEnum.AGENT.getCode().equals(record.getScene())
                        && FileUtils.isModerationQuarantinePath(record.getImagePath())) {
                    copiedNormalPath = imageAssetStore.copyQuarantineToApproved(
                            record.getImagePath(), true);
                    patch.setImagePath(copiedNormalPath);
                    quarantinePathToDelete = record.getImagePath();
                }
            }
            case "dismiss" -> patch.setStatus(ImageModerationStatusEnum.DISMISSED.getStatus());
            case "ban_temp" -> patch.setStatus(ImageModerationStatusEnum.VIOLATION.getStatus());
            case "ban_perm" -> patch.setStatus(ImageModerationStatusEnum.VIOLATION.getStatus());
            default -> throw new BusinessException("无效的处理动作");
        }
        try {
            int updated = imageModerationRecordMapper.updateByRecordIdIfPending(patch, recordId);
            if (updated != 1) {
                throw new BusinessException("该记录已处理");
            }
        } catch (RuntimeException ex) {
            if (copiedNormalPath != null) {
                imageAssetStore.deleteWithThumbnail(copiedNormalPath);
            }
            throw ex;
        }
        switch (action == null ? "" : action) {
            case "approve" -> onCommentRecordApproved(record);
            case "dismiss" -> onCommentRecordRejected(record, action);
            case "ban_temp" -> {
                userTempBanService.banUserHours(record.getUserId(), TEMP_BAN_HOURS);
                onCommentRecordRejected(record, action);
            }
            case "ban_perm" -> {
                userTempBanService.banUserPermanent(record.getUserId());
                onCommentRecordRejected(record, action);
            }
            default -> { }
        }
        if (!"approve".equals(action)
                && ImageModerationSceneEnum.AGENT.getCode().equals(record.getScene())
                && FileUtils.isModerationQuarantinePath(record.getImagePath())) {
            imageAssetStore.deleteWithThumbnail(record.getImagePath());
        }
        if (quarantinePathToDelete != null) {
            imageAssetStore.deleteWithThumbnail(quarantinePathToDelete);
        }
    }

    private void onCommentRecordApproved(ImageModerationRecord record) {
        if (!isCommentPendingReview(record)) {
            return;
        }
        String orderId = record.getOrderId();
        if (countPendingCommentModerationRecords(orderId) > 0) {
            return;
        }
        publishPendingOrderComment(orderId);
    }

    private void onCommentRecordRejected(ImageModerationRecord record, String action) {
        if (!isCommentPendingReview(record)) {
            return;
        }
        rejectPendingOrderComment(record.getOrderId(), action);
    }

    private boolean isCommentPendingReview(ImageModerationRecord record) {
        return ImageModerationSceneEnum.COMMENT.getCode().equals(record.getScene())
                && !StringTools.isEmpty(record.getOrderId());
    }

    private int countPendingCommentModerationRecords(String orderId) {
        ImageModerationRecordQuery query = new ImageModerationRecordQuery();
        query.setOrderId(orderId);
        query.setScene(ImageModerationSceneEnum.COMMENT.getCode());
        query.setStatus(ImageModerationStatusEnum.PENDING.getStatus());
        return imageModerationRecordMapper.selectCount(query);
    }

    private void publishPendingOrderComment(String orderId) {
        // 评论发布属 order 域；审核通过后由运营在订单侧处理或后续 Feign 补齐
        log.warn("审核通过后发布待审评论需订单服务配合，已跳过自动发布 orderId={}", orderId);
    }

    private void rejectPendingOrderComment(String orderId, String action) {
        log.warn("驳回待审评论需订单服务配合，仅清理本域审核记录 orderId={} action={}", orderId, action);
        dismissSiblingPendingRecords(orderId, action);
    }

    private void dismissSiblingPendingRecords(String orderId, String action) {
        ImageModerationRecordQuery query = new ImageModerationRecordQuery();
        query.setOrderId(orderId);
        query.setScene(ImageModerationSceneEnum.COMMENT.getCode());
        query.setStatus(ImageModerationStatusEnum.PENDING.getStatus());
        List<ImageModerationRecord> pendingList = imageModerationRecordMapper.selectList(query);
        Integer targetStatus = "dismiss".equals(action)
                ? ImageModerationStatusEnum.DISMISSED.getStatus()
                : ImageModerationStatusEnum.VIOLATION.getStatus();
        for (ImageModerationRecord item : pendingList) {
            ImageModerationRecord patch = new ImageModerationRecord();
            patch.setStatus(targetStatus);
            patch.setHandleTime(new Date());
            patch.setHandleRemark("关联订单评论复核一并处理");
            imageModerationRecordMapper.updateByRecordId(patch, item.getRecordId());
            if (FileUtils.isModerationQuarantinePath(item.getImagePath())) {
                fileUtils.deleteStoredFileQuietly(item.getImagePath());
            }
        }
    }

    private void deleteAllCommentImagesQuietly(String commentImages) {
        for (String path : splitImagePaths(commentImages)) {
            fileUtils.deleteUserImageWithThumbnailQuietly(path);
        }
    }

    @Override
    public int cleanupOrphanedCommentUploads() {
        ImageModerationRecordQuery query = new ImageModerationRecordQuery();
        query.setScene(ImageModerationSceneEnum.COMMENT.getCode());
        query.setStatus(ImageModerationStatusEnum.PENDING.getStatus());
        List<ImageModerationRecord> pendingList = imageModerationRecordMapper.selectList(query);
        long cutoffMs = System.currentTimeMillis() - (long) orphanUploadHours * 3600_000L;
        int cleaned = 0;
        for (ImageModerationRecord record : pendingList) {
            if (record.getCreateTime() != null && record.getCreateTime().getTime() > cutoffMs) {
                continue;
            }
            if (!isOrphanCommentUpload(record)) {
                continue;
            }
            ImageModerationRecord patch = new ImageModerationRecord();
            patch.setStatus(ImageModerationStatusEnum.DISMISSED.getStatus());
            patch.setHandleTime(new Date());
            patch.setHandleRemark("超时未提交评价，自动清理");
            int updated = imageModerationRecordMapper.updateByRecordIdIfPending(patch, record.getRecordId());
            if (updated != 1) {
                continue;
            }
            if (FileUtils.isModerationQuarantinePath(record.getImagePath())) {
                fileUtils.deleteStoredFileQuietly(record.getImagePath());
            }
            cleaned++;
        }
        if (cleaned > 0) {
            log.info("清理孤立评论疑似图片 {} 条", cleaned);
        }
        return cleaned;
    }

    @Override
    public int cleanupExpiredAgentAssets() {
        List<ImageModerationRecord> expired = imageModerationRecordMapper
                .selectExpiredAgentAssets(CLEANUP_BATCH_SIZE);
        int cleaned = 0;
        Date purgedAt = new Date();
        for (ImageModerationRecord record : expired) {
            int claimed = imageModerationRecordMapper.markAssetPurged(
                    record.getRecordId(), purgedAt);
            if (claimed != 1) {
                continue;
            }
            imageAssetStore.deleteWithThumbnail(record.getImagePath());
            cleaned++;
        }
        if (cleaned > 0) {
            log.info("清理到期 Agent 查询图片 {} 条", cleaned);
        }
        return cleaned;
    }

    private boolean isOrphanCommentUpload(ImageModerationRecord record) {
        String orderId = record.getOrderId();
        if (StringTools.isEmpty(orderId)) {
            return true;
        }
        try {
            OrderBriefVO order = feignResponseSupport.call(
                    () -> orderFeignClient.getOrder(new OrderIdDTO(orderId)),
                    "查询订单失败");
            // 订单不存在视为孤立；存在则保守不删（评论状态跨服务暂不可见）
            return order == null;
        } catch (Exception e) {
            log.warn("判断孤立评论上传失败 orderId={}", orderId, e);
            return false;
        }
    }

    @Override
    public void validateCommentQuarantinePaths(String userId, String orderId, String commentImages) {
        List<String> quarantinePaths = splitImagePaths(commentImages).stream()
                .filter(FileUtils::isModerationQuarantinePath)
                .collect(Collectors.toList());
        if (quarantinePaths.isEmpty()) {
            return;
        }
        for (String path : quarantinePaths) {
            ImageModerationRecordQuery query = new ImageModerationRecordQuery();
            query.setUserId(userId);
            query.setOrderId(orderId);
            query.setScene(ImageModerationSceneEnum.COMMENT.getCode());
            query.setStatus(ImageModerationStatusEnum.PENDING.getStatus());
            List<ImageModerationRecord> records = imageModerationRecordMapper.selectList(query);
            boolean matched = records.stream().anyMatch(r -> path.equals(r.getImagePath()));
            if (!matched) {
                throw new BusinessException("评论图片审核状态异常，请重新上传后再试");
            }
        }
    }

    public static List<String> splitImagePaths(String commentImages) {
        if (StringTools.isEmpty(commentImages)) {
            return List.of();
        }
        return Arrays.stream(commentImages.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .collect(Collectors.toList());
    }

    @Override
    public boolean containsQuarantinePath(String commentImages) {
        return splitImagePaths(commentImages).stream().anyMatch(FileUtils::isModerationQuarantinePath);
    }

    private static AssetMetadata inspectAgentAsset(ImageCompressUtils.PreparedImage prepared) {
        try {
            BufferedImage image = ImageIO.read(new ByteArrayInputStream(prepared.getData()));
            if (image == null) {
                throw new BusinessException(600, "规范化图片无法解析");
            }
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            String sha256 = HexFormat.of().formatHex(digest.digest(prepared.getData()));
            String suffix = prepared.getSuffix() == null
                    ? "" : prepared.getSuffix().toLowerCase();
            String mimeType = switch (suffix) {
                case ".png" -> "image/png";
                case ".gif" -> "image/gif";
                case ".webp" -> "image/webp";
                case ".bmp" -> "image/bmp";
                default -> "image/jpeg";
            };
            return new AssetMetadata(sha256, mimeType, image.getWidth(), image.getHeight());
        } catch (IOException exception) {
            throw new BusinessException(600, "规范化图片无法解析");
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 is unavailable", exception);
        }
    }

    private static String toIsoInstant(Date date) {
        return date == null ? null : date.toInstant().toString();
    }

    private record AssetMetadata(String sha256, String mimeType, int width, int height) {
    }
}
