package com.myshop.utils;

import com.myshop.component.RedisComponent;
import com.myshop.entity.vo.CheckCodeVO;
import com.wf.captcha.SpecCaptcha;
import com.wf.captcha.base.Captcha;

public final class CheckCodeGenerator {

    private CheckCodeGenerator() {
    }

    public static CheckCodeVO generate(RedisComponent redisComponent) {
        SpecCaptcha captcha = new SpecCaptcha(148, 52, 4);
        captcha.setCharType(Captcha.TYPE_ONLY_NUMBER);
        String code = captcha.text();
        String codeKey = redisComponent.saveCheckCode(code);
        return new CheckCodeVO(captcha.toBase64(), codeKey);
    }
}
