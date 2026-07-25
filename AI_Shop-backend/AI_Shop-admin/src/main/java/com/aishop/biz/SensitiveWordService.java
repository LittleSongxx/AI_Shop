package com.aishop.biz;

import com.aishop.entity.po.SensitiveWord;
import com.aishop.entity.query.SensitiveWordQuery;
import com.aishop.entity.vo.PaginationResultVO;

public interface SensitiveWordService {

    PaginationResultVO<SensitiveWord> findListByPage(SensitiveWordQuery query);

    void save(Long id, String word, String replaceWord, Integer status);

    void delete(Long id);

    void refreshCache();

    int syncFromDbToRedis();
}
