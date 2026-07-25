package com.aishop.controller.admin;

import com.aishop.entity.query.CommentReportQuery;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.biz.CommentReportService;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController("adminCommentReportController")
@RequestMapping("/admin/commentReport")
public class CommentReportController extends com.aishop.controller.admin.ABaseController {

    @Resource
    private CommentReportService commentReportService;

    @PostMapping("/loadDataList")
    public ResponseVO loadDataList(CommentReportQuery query) {
        if (query.getOrderBy() == null) {
            query.setOrderBy(com.aishop.entity.query.SafeSort.of("report_time desc"));
        }
        return getSuccessResponseVO(commentReportService.findListByPage(query));
    }

    @PostMapping("/getCommentReportByReportId")
    public ResponseVO getCommentReportByReportId(Integer reportId) {
        return getSuccessResponseVO(commentReportService.getByReportId(reportId));
    }

    @PostMapping("/handleReport")
    public ResponseVO handleReport(Integer reportId, Integer status, String handleRemark) {
        commentReportService.handleReport(reportId, status, handleRemark);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/deleteCommentReportByReportId")
    public ResponseVO deleteCommentReportByReportId(Integer reportId) {
        commentReportService.deleteByReportId(reportId);
        return getSuccessResponseVO(null);
    }
}
