package com.aishop.component;

import org.junit.jupiter.api.Test;
import org.springframework.http.client.SimpleClientHttpRequestFactory;

import java.lang.reflect.Field;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ImageVlmDescriberTest {

    @Test
    void defaultReadTimeoutHonoursTheFifteenSecondBudget() throws Exception {
        SimpleClientHttpRequestFactory factory = ImageVlmDescriber.createRequestFactory(5, 15);

        assertEquals(5_000, timeout(factory, "connectTimeout"));
        assertEquals(15_000, timeout(factory, "readTimeout"));
    }

    @Test
    void readTimeoutIsBoundedIndependentlyFromConnectTimeout() throws Exception {
        SimpleClientHttpRequestFactory factory = ImageVlmDescriber.createRequestFactory(30, 90);

        assertEquals(5_000, timeout(factory, "connectTimeout"));
        assertEquals(60_000, timeout(factory, "readTimeout"));
    }

    @Test
    void detectsSupportedImageMagicBytes() {
        byte[] png = {
                (byte) 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A
        };
        byte[] jpeg = {(byte) 0xFF, (byte) 0xD8, (byte) 0xFF, (byte) 0xE0};
        byte[] gif87a = {'G', 'I', 'F', '8', '7', 'a'};
        byte[] gif89a = {'G', 'I', 'F', '8', '9', 'a'};
        byte[] webp = {
                'R', 'I', 'F', 'F', 0, 0, 0, 0,
                'W', 'E', 'B', 'P'
        };

        assertEquals("image/png", ImageVlmDescriber.detectImageMime(png));
        assertEquals("image/jpeg", ImageVlmDescriber.detectImageMime(jpeg));
        assertEquals("image/gif", ImageVlmDescriber.detectImageMime(gif87a));
        assertEquals("image/gif", ImageVlmDescriber.detectImageMime(gif89a));
        assertEquals("image/webp", ImageVlmDescriber.detectImageMime(webp));
    }

    @Test
    void nearMissMagicBytesAreNotMisclassifiedAsImages() {
        assertEquals(
                "application/octet-stream",
                ImageVlmDescriber.detectImageMime(new byte[] {
                        (byte) 0x89, 0x50, 0, 0, 0, 0, 0, 0
                }));
        assertEquals(
                "application/octet-stream",
                ImageVlmDescriber.detectImageMime(new byte[] {
                        (byte) 0xFF, (byte) 0xD8, 0x00
                }));
        assertEquals(
                "application/octet-stream",
                ImageVlmDescriber.detectImageMime(new byte[] {'G', 'I', 'F', 'x', 'x', 'x'}));
    }

    private int timeout(SimpleClientHttpRequestFactory factory, String fieldName)
            throws ReflectiveOperationException {
        Field field = SimpleClientHttpRequestFactory.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        return field.getInt(factory);
    }
}
