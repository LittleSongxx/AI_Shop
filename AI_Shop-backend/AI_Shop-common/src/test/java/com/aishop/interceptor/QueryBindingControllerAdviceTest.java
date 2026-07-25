package com.aishop.interceptor;

import com.aishop.entity.query.BaseParam;
import org.junit.jupiter.api.Test;
import org.springframework.beans.MutablePropertyValues;
import org.springframework.web.bind.WebDataBinder;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

class QueryBindingControllerAdviceTest {

    @Test
    void externalRequestsCannotBindInternalSortOrPagingObjects() {
        BaseParam target = new BaseParam();
        WebDataBinder binder = new WebDataBinder(target);
        new QueryBindingControllerAdvice().blockInternalQueryFields(binder);

        MutablePropertyValues values = new MutablePropertyValues();
        values.add("pageNo", "3");
        values.add("orderBy", "create_time desc; drop table product_info");
        values.add("simplePage.pageNo", "99");
        binder.bind(values);

        assertEquals(3, target.getPageNo());
        assertNull(target.getOrderBy());
        assertNull(target.getSimplePage());
    }
}
