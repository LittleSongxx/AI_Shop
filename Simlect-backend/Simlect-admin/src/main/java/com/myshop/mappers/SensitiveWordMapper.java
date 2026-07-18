package com.myshop.mappers;

import com.myshop.entity.po.SensitiveWord;
import org.apache.ibatis.annotations.Param;

public interface SensitiveWordMapper<T, P> extends BaseMapper<T, P> {

    SensitiveWord selectByWord(@Param("word") String word);

    Integer deleteById(@Param("id") Long id);
}
