package com.aishop.biz.impl;

import com.aishop.component.RedisComponent;
import com.aishop.entity.po.SysCategory;
import com.aishop.entity.query.SysCategoryQuery;
import com.aishop.mappers.SysCategoryMapper;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class SysCategoryServiceImplTest {

    @Test
    void categoryTreeUsesDatabaseAndRefreshesStaleRedisSnapshot() {
        SysCategoryMapper<SysCategory, SysCategoryQuery> mapper = mock(SysCategoryMapper.class);
        RedisComponent redis = mock(RedisComponent.class);
        SysCategoryServiceImpl service = new SysCategoryServiceImpl();
        ReflectionTestUtils.setField(service, "sysCategoryMapper", mapper);
        ReflectionTestUtils.setField(service, "redisComponent", redis);

        SysCategory parent = category("10001", "0", "数码家电");
        SysCategory child = category("20003", "10001", "数码影音");
        List<SysCategory> databaseRows = List.of(parent, child);
        when(mapper.selectList(any(SysCategoryQuery.class))).thenReturn(databaseRows);
        SysCategoryQuery query = new SysCategoryQuery();
        query.setParent(true);
        List<SysCategory> result = service.findListByParam(query);

        assertEquals(List.of("10001"),
                result.stream().map(SysCategory::getCategoryId).toList());
        assertEquals(List.of("20003"),
                result.get(0).getChildren().stream()
                        .map(SysCategory::getCategoryId).toList());
        verify(redis, never()).getCategoryList();
        verify(redis).saveCategory2Redis(databaseRows);
    }

    private static SysCategory category(String id, String parentId, String name) {
        SysCategory category = new SysCategory();
        category.setCategoryId(id);
        category.setpCategoryId(parentId);
        category.setCategoryName(name);
        return category;
    }
}
