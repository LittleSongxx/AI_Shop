package com.aishop.biz.impl;

import com.aishop.entity.po.SearchHotKeyword;
import com.aishop.entity.query.SearchHotKeywordQuery;
import com.aishop.exception.BusinessException;
import com.aishop.mappers.SearchHotKeywordMapper;
import com.aishop.biz.SearchHotKeywordService;
import com.aishop.utils.StringTools;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.util.Date;
import java.util.List;

@Service("searchHotKeywordService")
public class SearchHotKeywordServiceImpl implements SearchHotKeywordService {

    @Resource
    private SearchHotKeywordMapper<SearchHotKeyword, SearchHotKeywordQuery> searchHotKeywordMapper;

    @Override
    public List<SearchHotKeyword> loadList() {
        SearchHotKeywordQuery query = new SearchHotKeywordQuery();
        query.setOrderBy(com.aishop.entity.query.SafeSort.of("sort asc, update_time desc"));
        return searchHotKeywordMapper.selectList(query);
    }

    @Override
    public void save(String keyword, Integer sort, Integer status) {
        if (StringTools.isEmpty(keyword)) {
            throw new BusinessException("热搜词不能为空");
        }
        SearchHotKeyword bean = new SearchHotKeyword();
        bean.setKeyword(keyword.trim());
        bean.setSort(sort == null ? 0 : sort);
        bean.setStatus(status == null ? 1 : status);
        bean.setUpdateTime(new Date());
        searchHotKeywordMapper.insertOrUpdate(bean);
    }

    @Override
    public void deleteByKeyword(String keyword) {
        if (StringTools.isEmpty(keyword)) {
            throw new BusinessException("热搜词不能为空");
        }
        searchHotKeywordMapper.deleteByKeyword(keyword.trim());
    }
}
