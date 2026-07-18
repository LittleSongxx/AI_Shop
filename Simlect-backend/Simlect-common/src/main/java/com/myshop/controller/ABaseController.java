package com.myshop.controller;
import com.myshop.component.RedisComponent;
import com.myshop.entity.dto.TokenUserInfoDTO;
import com.myshop.entity.enums.ResponseCodeEnum;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.exception.BusinessException;
import com.myshop.utils.AuthCookieHelper;
import com.myshop.utils.StringTools;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;


public class ABaseController {

    @Resource
    protected RedisComponent redisComponent;

    @Resource
    protected AuthCookieHelper authCookieHelper;

    protected static final String STATUC_SUCCESS = "success";

    protected static final String STATUC_ERROR = "error";

    protected <T> ResponseVO getSuccessResponseVO(T t) {
        ResponseVO<T> responseVO = new ResponseVO<>();
        responseVO.setStatus(STATUC_SUCCESS);
        responseVO.setCode(ResponseCodeEnum.CODE_200.getCode());
        responseVO.setInfo(ResponseCodeEnum.CODE_200.getMsg());
        responseVO.setData(t);
        return responseVO;
    }

    // 从请求头中获取TokenUserInfoDTO
    public TokenUserInfoDTO getTokenUserInfo() {
        // 获取请求头中的token
        ServletRequestAttributes attributes = (ServletRequestAttributes) RequestContextHolder.getRequestAttributes();
        if (attributes == null) {
            return null;
        }
        HttpServletRequest request = attributes.getRequest();
        String token = authCookieHelper.resolveWebToken(request);
        if (StringTools.isEmpty(token)) {
            return null;
        }
        return redisComponent.getTokenUserInfo(token);
    }
    protected <T> ResponseVO getBusinessErrorResponseVO(BusinessException e, T t) {
        ResponseVO vo = new ResponseVO();
        vo.setStatus(STATUC_ERROR);
        if (e.getCode() == null) {
            vo.setCode(ResponseCodeEnum.CODE_600.getCode());
        } else {
            vo.setCode(e.getCode());
        }
        vo.setInfo(e.getMessage());
        vo.setData(t);
        return vo;
    }

    protected <T> ResponseVO getServerErrorResponseVO(T t) {
        ResponseVO vo = new ResponseVO();
        vo.setStatus(STATUC_ERROR);
        vo.setCode(ResponseCodeEnum.CODE_500.getCode());
        vo.setInfo(ResponseCodeEnum.CODE_500.getMsg());
        vo.setData(t);
        return vo;
    }
}
