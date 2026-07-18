package com.myshop.controller.admin;

import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.SearchHotKeywordService;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/admin/searchHotKeyword")
public class SearchHotKeywordController extends com.myshop.controller.admin.ABaseController {

    @Resource
    private SearchHotKeywordService searchHotKeywordService;

    @PostMapping("/loadList")
    public ResponseVO loadList() {
        return getSuccessResponseVO(searchHotKeywordService.loadList());
    }

    @PostMapping("/save")
    public ResponseVO save(String keyword, Integer sort, Integer status) {
        searchHotKeywordService.save(keyword, sort, status);
        return getSuccessResponseVO(null);
    }

    @PostMapping("/del")
    public ResponseVO del(String keyword) {
        searchHotKeywordService.deleteByKeyword(keyword);
        return getSuccessResponseVO(null);
    }
}
