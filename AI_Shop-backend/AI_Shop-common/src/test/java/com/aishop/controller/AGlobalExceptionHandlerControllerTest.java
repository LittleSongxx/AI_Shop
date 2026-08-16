package com.aishop.controller;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.core.read.ListAppender;
import com.aishop.entity.vo.ResponseVO;
import com.aishop.exception.BusinessException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class AGlobalExceptionHandlerControllerTest {

    @Test
    void expectedBusinessFailureIsWarnedWithoutAnErrorEvent() {
        AGlobalExceptionHandlerController handler = new AGlobalExceptionHandlerController();
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mock(HttpServletResponse.class);
        when(request.getRequestURL()).thenReturn(new StringBuffer("http://localhost/test"));

        Logger logger = (Logger) LoggerFactory.getLogger(AGlobalExceptionHandlerController.class);
        ListAppender<ch.qos.logback.classic.spi.ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            ResponseVO result = (ResponseVO) handler.handleException(
                    new BusinessException("支付宝支付未配置"),
                    request,
                    response
            );

            assertEquals("支付宝支付未配置", result.getInfo());
            assertTrue(appender.list.stream().anyMatch(event -> event.getLevel() == Level.WARN));
            assertFalse(appender.list.stream().anyMatch(event -> event.getLevel() == Level.ERROR));
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }
    }

    @Test
    void unexpectedFailureStillEmitsAnErrorEvent() {
        AGlobalExceptionHandlerController handler = new AGlobalExceptionHandlerController();
        HttpServletRequest request = mock(HttpServletRequest.class);
        HttpServletResponse response = mock(HttpServletResponse.class);
        when(request.getRequestURL()).thenReturn(new StringBuffer("http://localhost/test"));

        Logger logger = (Logger) LoggerFactory.getLogger(AGlobalExceptionHandlerController.class);
        ListAppender<ch.qos.logback.classic.spi.ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try {
            handler.handleException(new IllegalStateException("boom"), request, response);

            assertTrue(appender.list.stream().anyMatch(event -> event.getLevel() == Level.ERROR));
        } finally {
            logger.detachAppender(appender);
            appender.stop();
        }
    }
}
