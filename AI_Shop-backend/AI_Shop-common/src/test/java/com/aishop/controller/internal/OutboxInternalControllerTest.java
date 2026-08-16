package com.aishop.controller.internal;

import com.aishop.entity.po.LocalMessageOutbox;
import com.aishop.service.OutboxMessageService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OutboxInternalControllerTest {

    private OutboxMessageService service;
    private OutboxInternalController controller;

    @BeforeEach
    void setUp() {
        service = mock(OutboxMessageService.class);
        controller = new OutboxInternalController();
        ReflectionTestUtils.setField(controller, "outboxMessageService", service);
        ReflectionTestUtils.setField(controller, "expectedOpsToken", "ops-secret");
    }

    @Test
    void rejectsMissingOpsToken() {
        ResponseStatusException error = assertThrows(
                ResponseStatusException.class,
                () -> controller.exhausted(null, 50));

        assertEquals(403, error.getStatusCode().value());
    }

    @Test
    void listsExhaustedWithoutReturningPayload() {
        LocalMessageOutbox row = new LocalMessageOutbox();
        row.setId(7L);
        row.setPayloadJson("{\"secret\":\"not-returned\"}");
        when(service.listExhausted(50)).thenReturn(List.of(row));

        var response = controller.exhausted("ops-secret", 50);

        assertEquals(7L, response.getData().get(0).id());
    }

    @Test
    void replaysWithBothInternalLayersSatisfied() {
        when(service.replayExhausted(9L)).thenReturn(true);

        var response = controller.replay(9L, "ops-secret");

        assertEquals(true, response.getData());
        verify(service).replayExhausted(9L);
    }
}
