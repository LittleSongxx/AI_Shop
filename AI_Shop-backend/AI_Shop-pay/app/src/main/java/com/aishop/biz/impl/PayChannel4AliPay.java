package com.aishop.biz.impl;

import com.alipay.api.AlipayApiException;
import com.alipay.api.AlipayClient;
import com.alipay.api.AlipayConfig;
import com.alipay.api.DefaultAlipayClient;
import com.alipay.api.domain.AlipayTradeCloseModel;
import com.alipay.api.domain.AlipayTradePagePayModel;
import com.alipay.api.domain.AlipayTradeWapPayModel;
import com.alipay.api.domain.AlipayTradeQueryModel;
import com.alipay.api.domain.AlipayTradeRefundModel;
import com.alipay.api.internal.util.AlipaySignature;
import com.alipay.api.request.AlipayTradeCloseRequest;
import com.alipay.api.request.AlipayTradePagePayRequest;
import com.alipay.api.request.AlipayTradeWapPayRequest;
import com.alipay.api.request.AlipayTradeQueryRequest;
import com.alipay.api.request.AlipayTradeRefundRequest;
import com.alipay.api.response.AlipayTradeCloseResponse;
import com.alipay.api.response.AlipayTradePagePayResponse;
import com.alipay.api.response.AlipayTradeWapPayResponse;
import com.alipay.api.response.AlipayTradeQueryResponse;
import com.alipay.api.response.AlipayTradeRefundResponse;
import com.aishop.component.PayOrderRedisComponent;
import com.aishop.entity.config.AppConfig;
import com.aishop.api.dto.PayInfoDTO;
import com.aishop.api.dto.PayOrderNotifyDTO;
import com.aishop.entity.enums.DateTimePatternEnum;
import com.aishop.api.enums.PayChannelEnum;
import com.aishop.exception.BusinessException;
import com.aishop.biz.PayChannel;
import com.aishop.utils.DateUtil;
import com.aishop.utils.OrderPayAmountUtil;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.Map;

@Service("payChannel4Alipay")
@Slf4j
public class PayChannel4AliPay implements PayChannel {

    static {
        // 设置SSL协议版本
        System.setProperty("https.protocols", "TLSv1.2");
        System.setProperty("jdk.tls.client.protocols", "TLSv1.2");
    }

    private static final String TRADE_STATE_SUCCESS = "TRADE_SUCCESS";
    private static final String TRADE_STATE_FINISHED = "TRADE_FINISHED";

    private static final String TRADE_NOT_EXIST = "ACQ.TRADE_NOT_EXIST";

    private static final String NOTIFY_URL = "/api/notify/alipayNotify";
    private static final String RETURN_URL_PATH = "/payment/";

    @Resource
    private AppConfig appConfig;
    @Resource
    private PayOrderRedisComponent payOrderRedisComponent;
    @Override
    public PayInfoDTO getPayUrl(PayChannelEnum payChannelEnum, String payOrderId, String subject, BigDecimal amount) {
        try {
            BigDecimal payAmount = OrderPayAmountUtil.normalizeChannelPayAmount(amount);
            String payAmountText = OrderPayAmountUtil.formatChannelPayAmount(amount);
            // 初始化SDK
            AlipayClient alipayClient = new DefaultAlipayClient(getAlipayConfig());
            // 构造请求参数以调用接口
            AlipayTradePagePayRequest request = new AlipayTradePagePayRequest();
            AlipayTradePagePayModel model = new AlipayTradePagePayModel();
            // 设置商户订单号
            model.setOutTradeNo(payOrderId);
            // 设置订单总金额
            model.setTotalAmount(payAmountText);
            // 设置订单标题
            model.setSubject(subject);
            //订单绝对超时时间
            model.setTimeExpire(DateUtil.getMinAfter(appConfig.getOrderExpireMinute(), DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));

            request.setBizModel(model);

            //实际开发会设置异步通知回调信息，以便实时获取订单支付结果
            request.setNotifyUrl(appConfig.getProjectDomain() + NOTIFY_URL);
            request.setReturnUrl(appConfig.getProjectDomain() + RETURN_URL_PATH + payOrderId);
            String payInfo = null;
            switch (payChannelEnum) {
                case ALIPAY_PC -> {
                    model.setProductCode("FAST_INSTANT_TRADE_PAY");
                    AlipayTradePagePayResponse response = alipayClient.pageExecute(request);
                    if (!response.isSuccess()) {
                        throw new BusinessException("获取支付信息失败");
                    }
                    payInfo = response.getBody();
                    payOrderRedisComponent.markPayTradeInitiated(payOrderId);
                }
                case ALIPAY_WAP -> {
                    AlipayTradeWapPayRequest wapRequest = new AlipayTradeWapPayRequest();
                    AlipayTradeWapPayModel wapModel = new AlipayTradeWapPayModel();
                    wapModel.setOutTradeNo(payOrderId);
                    wapModel.setTotalAmount(payAmountText);
                    wapModel.setSubject(subject);
                    wapModel.setProductCode("QUICK_WAP_WAY");
                    wapModel.setTimeExpire(DateUtil.getMinAfter(appConfig.getOrderExpireMinute(),
                            DateTimePatternEnum.YYYY_MM_DD_HH_MM_SS.getPattern()));
                    wapRequest.setBizModel(wapModel);
                    wapRequest.setNotifyUrl(appConfig.getProjectDomain() + NOTIFY_URL);
                    wapRequest.setReturnUrl(appConfig.getProjectDomain() + RETURN_URL_PATH + payOrderId);
                    AlipayTradeWapPayResponse wapResponse = alipayClient.pageExecute(wapRequest);
                    if (!wapResponse.isSuccess()) {
                        throw new BusinessException("获取支付信息失败");
                    }
                    payInfo = wapResponse.getBody();
                    payOrderRedisComponent.markPayTradeInitiated(payOrderId);
                }
                default -> throw new BusinessException("不支持的支付方式");
            }
            return new PayInfoDTO(payInfo, payOrderId, payAmount);
        } catch (BusinessException e) {
            throw e;
        }catch (Exception e){
            log.error("支付宝获取支付信息失败");
            throw new BusinessException("获取支付信息失败");
        }
    }

    private AlipayConfig getAlipayConfig() {
        AlipayConfig config = new AlipayConfig();
        //支付宝网关
        config.setServerUrl(appConfig.getAlipayServerUrl());
        //应用ID
        config.setAppId(appConfig.getAlipayAppid());
        //应用私钥信息
        config.setPrivateKey(appConfig.getAlipayAppPrivateKey());
        //应用公钥证书本地地址
        config.setAppCertPath(appConfig.getProjectFolder() + appConfig.getAlipayAppCertPath());
        //支付宝公钥证书 alipayPublicCert.crt
        config.setAlipayPublicCertPath(appConfig.getProjectFolder() + appConfig.getAlipayPublicCertPath());
        //支付宝根证书本地地址
        config.setRootCertPath(appConfig.getProjectFolder() + appConfig.getAlipayRootCertPath());
        config.setCharset("UTF8");
        config.setSignType("RSA2");
        config.setFormat("json");
        // 设置超时时间（毫秒）
        config.setConnectTimeout(30000);  // 连接超时30秒
        config.setReadTimeout(30000);     // 读取超时30秒
        return config;
    }

    @Override
    public PayOrderNotifyDTO payNotify(Map<String, String> requestParams, String jsonBody) {
        try {
            requestParams.remove("sign_type");
            Boolean signCheckResult = AlipaySignature.rsaCertCheckV2(requestParams, appConfig.getProjectFolder() + appConfig.getAlipayPublicCertPath(), "UTF-8", "RSA2");
            if (!signCheckResult) {
                throw new BusinessException("支付宝回调校验失败");
            }
        } catch (AlipayApiException e) {
            log.error("支付宝回调检验失败", e);
            throw new BusinessException("支付宝回调校验失败");
        }
        String payOrderId = requestParams.get("out_trade_no");
        String channelOrderId = requestParams.get("trade_no");
        String status = String.valueOf(requestParams.get("trade_status"));
        if (!TRADE_STATE_SUCCESS.equalsIgnoreCase(status)
                && !TRADE_STATE_FINISHED.equalsIgnoreCase(status)) {
            log.info("支付宝回调地址状态不为success，不做处理，订单号：{}", payOrderId);
            return null;
        }
        return new PayOrderNotifyDTO(payOrderId, channelOrderId);
    }

    @Override
    public PayOrderNotifyDTO queryOrder(String payOrderId) {
        try {
            // 初始化SDK
            AlipayClient alipayClient = new DefaultAlipayClient(getAlipayConfig());
            AlipayTradeQueryRequest request = new AlipayTradeQueryRequest();
            AlipayTradeQueryModel model = new AlipayTradeQueryModel();
            // 设置商户订单号
            model.setOutTradeNo(payOrderId);
            request.setBizModel(model);
            AlipayTradeQueryResponse response = alipayClient.certificateExecute(request);

            if (!response.isSuccess()) {
                String subCode = response.getSubCode();
                if (TRADE_NOT_EXIST.equals(subCode)) {
                    return null;
                }
                log.debug("支付宝查单未成功 payOrderId={}, subCode={}, subMsg={}",
                        payOrderId, subCode, response.getSubMsg());
                return null;
            }
            if (!TRADE_STATE_SUCCESS.equals(response.getTradeStatus())) {
                return null;
            }
            log.info("查询支付宝订单已支付 payOrderId={}, tradeNo={}", payOrderId, response.getTradeNo());
            return new PayOrderNotifyDTO(payOrderId, response.getTradeNo());
        } catch (BusinessException e) {
            throw e;
        } catch (Exception e) {
            log.error("支付宝获取支付信息失败", e);
            throw new BusinessException("获取支付信息失败");
        }
    }

    @Override
    public void refund(String sourcePayOrderId, String payOrderId, BigDecimal refundAmount) {
        try {
            // 初始化SDK
            AlipayClient alipayClient = new DefaultAlipayClient(getAlipayConfig());
            AlipayTradeRefundRequest request = new AlipayTradeRefundRequest();
            AlipayTradeRefundModel model = new AlipayTradeRefundModel();
            // 设置商户订单号
            model.setOutTradeNo(sourcePayOrderId);
            model.setOutRequestNo(payOrderId);
            model.setRefundAmount(refundAmount.toString());
            request.setBizModel(model);
            AlipayTradeRefundResponse response = alipayClient.certificateExecute(request);
            log.info("支付宝退款:{},返回结果:{}", payOrderId, response.getBody());
            if (!response.isSuccess()) {
                throw new BusinessException("退款失败");
            }
        } catch (BusinessException e) {
            throw e;
        } catch (Exception e) {
            log.error("支付宝退款失败", e);
            throw new BusinessException("支付宝退款失败");
        }
    }

    @Override
    public void closeOrder(String payOrderId) {
        try {
            AlipayClient alipayClient = new DefaultAlipayClient(getAlipayConfig());
            AlipayTradeCloseRequest request = new AlipayTradeCloseRequest();
            AlipayTradeCloseModel model = new AlipayTradeCloseModel();
            // 设置商户订单号
            model.setOutTradeNo(payOrderId);
            request.setBizModel(model);
            AlipayTradeCloseResponse response = alipayClient.certificateExecute(request);
            if (!response.isSuccess()) {
                String subCode = response.getSubCode();
                // 忽略交易不存在和系统繁忙的错误
                if (!TRADE_NOT_EXIST.equals(subCode) && !"aop.unknow-error".equals(subCode)) {
                    throw new BusinessException("订单关闭失败");
                }
                log.warn("支付宝订单关闭返回非成功状态: subCode={}, payOrderId={}", subCode, payOrderId);
            }
        } catch (BusinessException e) {
            throw e;
        } catch (Exception e) {
            log.error("支付宝订单关闭失败, payOrderId={}", payOrderId, e);
        }
    }
}
