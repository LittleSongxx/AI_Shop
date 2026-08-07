package com.aishop.biz;

import com.aishop.entity.dto.BaiduImageCensorResultDTO;
import com.aishop.api.dto.ImageUploadResultDTO;
import com.aishop.entity.po.ImageModerationRecord;
import com.aishop.entity.query.ImageModerationRecordQuery;
import com.aishop.entity.vo.PaginationResultVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

public interface ImageModerationService {

    ImageUploadResultDTO uploadAndModerate(String userId, String userIp, MultipartFile file,
                                           Boolean createThumbnail, String scene, String orderId);

    BaiduImageCensorResultDTO censorImageBytes(byte[] imageBytes, String userId, String userIp);

    List<ImageModerationRecord> findListByParam(ImageModerationRecordQuery param);

    PaginationResultVO<ImageModerationRecord> findListByPage(ImageModerationRecordQuery param);

    ImageModerationRecord getByRecordId(Integer recordId);

    Map<String, Object> verifySupportImage(String userId, Integer moderationId, String imagePath);

    void handleReview(Integer recordId, String action, String handleRemark);

    void validateCommentQuarantinePaths(String userId, String orderId, String commentImages);

    boolean containsQuarantinePath(String commentImages);

    int cleanupOrphanedCommentUploads();
}
