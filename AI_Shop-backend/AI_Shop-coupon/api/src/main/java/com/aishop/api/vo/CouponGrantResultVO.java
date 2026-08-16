package com.aishop.api.vo;

import java.io.Serializable;

public class CouponGrantResultVO implements Serializable {

    private static final long serialVersionUID = 1L;

    private Boolean granted;
    private Boolean newlyGranted;
    private String userCouponId;
    private String couponName;

    public Boolean getGranted() {
        return granted;
    }

    public void setGranted(Boolean granted) {
        this.granted = granted;
    }

    public Boolean getNewlyGranted() {
        return newlyGranted;
    }

    public void setNewlyGranted(Boolean newlyGranted) {
        this.newlyGranted = newlyGranted;
    }

    public String getUserCouponId() {
        return userCouponId;
    }

    public void setUserCouponId(String userCouponId) {
        this.userCouponId = userCouponId;
    }

    public String getCouponName() {
        return couponName;
    }

    public void setCouponName(String couponName) {
        this.couponName = couponName;
    }
}
