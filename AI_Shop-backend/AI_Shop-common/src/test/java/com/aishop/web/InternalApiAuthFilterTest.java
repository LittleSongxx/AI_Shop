package com.aishop.web;

import com.aishop.constants.InternalApiHeaders;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.PrintWriter;
import java.io.StringWriter;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class InternalApiAuthFilterTest {

    private InternalApiAuthFilter filter;
    private HttpServletRequest request;
    private HttpServletResponse response;
    private FilterChain chain;

    @BeforeEach
    void setUp() throws Exception {
        filter = new InternalApiAuthFilter();
        request = mock(HttpServletRequest.class);
        response = mock(HttpServletResponse.class);
        chain = mock(FilterChain.class);
        when(response.getWriter()).thenReturn(new PrintWriter(new StringWriter()));
    }

    private void configure(String expectedToken, boolean authEnabled) {
        ReflectionTestUtils.setField(filter, "expectedToken", expectedToken);
        ReflectionTestUtils.setField(filter, "authEnabled", authEnabled);
    }

    @Test
    void unconfiguredTokenRejectsInternalCalls() throws Exception {
        // An unset token must not turn the filter into a pass-through: the only way
        // to switch the check off is the explicit auth-enabled flag.
        configure("", true);
        when(request.getHeader(InternalApiHeaders.INTERNAL_TOKEN)).thenReturn("anything");

        filter.doFilterInternal(request, response, chain);

        verify(chain, never()).doFilter(any(), any());
        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    @Test
    void matchingTokenPasses() throws Exception {
        configure("secret", true);
        when(request.getHeader(InternalApiHeaders.INTERNAL_TOKEN)).thenReturn("secret");

        filter.doFilterInternal(request, response, chain);

        verify(chain).doFilter(request, response);
        verify(response, never()).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    @Test
    void wrongOrMissingTokenIsRejected() throws Exception {
        configure("secret", true);
        when(request.getHeader(InternalApiHeaders.INTERNAL_TOKEN)).thenReturn(null);

        filter.doFilterInternal(request, response, chain);

        verify(chain, never()).doFilter(any(), any());
        verify(response).setStatus(HttpServletResponse.SC_UNAUTHORIZED);
    }

    @Test
    void onlyInternalPathsAreFiltered() {
        configure("secret", true);
        when(request.getRequestURI()).thenReturn("/api/product/list");
        assertTrue(filter.shouldNotFilter(request));

        when(request.getRequestURI()).thenReturn("/api/internal/order/query");
        assertFalse(filter.shouldNotFilter(request));
    }

    @Test
    void disablingAuthSkipsTheFilterEntirely() {
        configure("", false);
        when(request.getRequestURI()).thenReturn("/api/internal/order/query");
        assertTrue(filter.shouldNotFilter(request));
    }
}
