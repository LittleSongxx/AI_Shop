package com.myshop.biz;

import com.myshop.entity.po.SensitiveWord;
import com.myshop.entity.query.SensitiveWordQuery;
import com.myshop.entity.vo.PaginationResultVO;

public interface SensitiveWordService {

    PaginationResultVO<SensitiveWord> findListByPage(SensitiveWordQuery query);

    void save(Long id, String word, String replaceWord, Integer status);

    void delete(Long id);

    void refreshCache();

    int syncFromDbToRedis();
}
