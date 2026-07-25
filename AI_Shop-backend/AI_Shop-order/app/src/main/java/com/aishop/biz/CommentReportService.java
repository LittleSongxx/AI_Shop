package com.aishop.biz;

import com.aishop.entity.po.CommentReport;
import com.aishop.entity.query.CommentReportQuery;
import com.aishop.entity.vo.PaginationResultVO;

import java.util.List;

public interface CommentReportService {

    List<CommentReport> findListByParam(CommentReportQuery param);

    Integer findCountByParam(CommentReportQuery param);

    PaginationResultVO<CommentReport> findListByPage(CommentReportQuery param);

    CommentReport submitReport(String reporterUserId, String orderId, String productId,
                               String reason, String detail, String commentSnapshot);

    CommentReport getByReportId(Integer reportId);

    Integer handleReport(Integer reportId, Integer status, String handleRemark);

    Integer deleteByReportId(Integer reportId);
}
