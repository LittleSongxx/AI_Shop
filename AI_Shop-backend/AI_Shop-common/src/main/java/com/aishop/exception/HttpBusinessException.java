package com.aishop.exception;

public class HttpBusinessException extends BusinessException {

    private final int httpStatus;

    public HttpBusinessException(int httpStatus, String message) {
        super(httpStatus, message);
        this.httpStatus = httpStatus;
    }

    public int getHttpStatus() {
        return httpStatus;
    }
}
