package com.aishop.interceptor;

import org.springframework.web.bind.WebDataBinder;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.InitBinder;

@ControllerAdvice
public class QueryBindingControllerAdvice {

    @InitBinder
    public void blockInternalQueryFields(WebDataBinder binder) {
        binder.setDisallowedFields("orderBy", "orderBy.*", "simplePage", "simplePage.*");
    }
}
