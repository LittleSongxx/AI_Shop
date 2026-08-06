package com.aishop.entity.dto;

import java.util.Date;

/**
 * Optional recommendation touchpoint carried through cart and order commands.
 * Implementations must treat source/time as server-owned canonical fields.
 */
public interface RecommendationAttributionCarrier {

    String getProductId();

    String getAiRequestId();

    void setAiRequestId(String aiRequestId);

    Integer getAiPosition();

    void setAiPosition(Integer aiPosition);

    String getAiSource();

    void setAiSource(String aiSource);

    Date getAiAttributedAt();

    void setAiAttributedAt(Date aiAttributedAt);

    default void clearRecommendationAttribution() {
        setAiRequestId(null);
        setAiPosition(null);
        setAiSource(null);
        setAiAttributedAt(null);
    }
}
