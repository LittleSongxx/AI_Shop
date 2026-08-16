package com.aishop.utils;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

class StringToolsTest {

    @Test
    void stableUserCouponIdIsDeterministicAndBusinessScoped() {
        String first = StringTools.createStableUserCouponId(
                "sign_reward", "u1", "7:c1");
        String retry = StringTools.createStableUserCouponId(
                "sign_reward", "u1", "7:c1");
        String otherReward = StringTools.createStableUserCouponId(
                "sign_reward", "u1", "14:c1");

        assertEquals(first, retry);
        assertEquals(32, first.length());
        assertNotEquals(first, otherReward);
    }
}
