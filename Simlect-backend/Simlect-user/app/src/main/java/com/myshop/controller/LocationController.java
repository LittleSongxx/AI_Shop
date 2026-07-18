package com.myshop.controller;

import com.myshop.annotation.GlobalInterceptor;
import com.myshop.annotation.RateLimit;
import com.myshop.entity.vo.LocationWeatherVO;
import com.myshop.entity.vo.ResponseVO;
import com.myshop.biz.LocationWeatherService;
import jakarta.annotation.Resource;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RequestMapping("/location")
@RestController
public class LocationController extends ABaseController {

    @Resource
    private LocationWeatherService locationWeatherService;

    @GetMapping("/resolve")
    @GlobalInterceptor(checkLogin = true)
    public ResponseVO resolve(Double latitude, Double longitude) {
        return getSuccessResponseVO(locationWeatherService.resolve(latitude, longitude));
    }

    @GetMapping("/sync")
    @GlobalInterceptor(checkLogin = true)
    @RateLimit(limitType = RateLimit.LimitType.USER, windowSeconds = 10, maxCount = 1, message = "获取位置过于频繁，请稍后再试")
    public ResponseVO sync(Double latitude, Double longitude) {
        String userId = getTokenUserInfo().getUserId();
        LocationWeatherVO vo = locationWeatherService.syncUserLocation(userId, latitude, longitude);
        return getSuccessResponseVO(vo);
    }
}
