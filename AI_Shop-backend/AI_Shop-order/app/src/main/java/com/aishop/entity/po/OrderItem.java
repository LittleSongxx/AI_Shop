package com.aishop.entity.po;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.aishop.entity.dto.RecommendationAttributionCarrier;
import java.math.BigDecimal;
import java.io.Serializable;
import java.util.Date;

public class OrderItem implements Serializable, RecommendationAttributionCarrier {

	private String orderItemId;

	private String orderId;

	private String cover;

	private String productId;

	private String productName;

	private String propertyValueIdHash;

	private String propertyInfo;

	private BigDecimal itemAmount;

	private Integer buyCount;

	private Integer orderItemStatus;

	private String remark;

	private String refundOrderId;

	private String aiRequestId;

	private Integer aiPosition;

	private String aiSource;

	private Date aiAttributedAt;

	public void setOrderItemId(String orderItemId){
		this.orderItemId = orderItemId;
	}

	public String getOrderItemId(){
		return this.orderItemId;
	}

	public void setOrderId(String orderId){
		this.orderId = orderId;
	}

	public String getOrderId(){
		return this.orderId;
	}

	public void setCover(String cover){
		this.cover = cover;
	}

	public String getCover(){
		return this.cover;
	}

	public void setProductId(String productId){
		this.productId = productId;
	}

	public String getProductId(){
		return this.productId;
	}

	public void setProductName(String productName){
		this.productName = productName;
	}

	public String getProductName(){
		return this.productName;
	}

	public void setPropertyValueIdHash(String propertyValueIdHash){
		this.propertyValueIdHash = propertyValueIdHash;
	}

	public String getPropertyValueIdHash(){
		return this.propertyValueIdHash;
	}

	public void setPropertyInfo(String propertyInfo){
		this.propertyInfo = propertyInfo;
	}

	public String getPropertyInfo(){
		return this.propertyInfo;
	}

	public void setItemAmount(BigDecimal itemAmount){
		this.itemAmount = itemAmount;
	}

	public BigDecimal getItemAmount(){
		return this.itemAmount;
	}

	public void setBuyCount(Integer buyCount){
		this.buyCount = buyCount;
	}

	public Integer getBuyCount(){
		return this.buyCount;
	}

	public void setOrderItemStatus(Integer orderItemStatus){
		this.orderItemStatus = orderItemStatus;
	}

	public Integer getOrderItemStatus(){
		return this.orderItemStatus;
	}

	public void setRemark(String remark){
		this.remark = remark;
	}

	public String getRemark(){
		return this.remark;
	}

	public void setRefundOrderId(String refundOrderId){
		this.refundOrderId = refundOrderId;
	}

	public String getRefundOrderId(){
		return this.refundOrderId;
	}

	public String getAiRequestId() {
		return aiRequestId;
	}

	public void setAiRequestId(String aiRequestId) {
		this.aiRequestId = aiRequestId;
	}

	public Integer getAiPosition() {
		return aiPosition;
	}

	public void setAiPosition(Integer aiPosition) {
		this.aiPosition = aiPosition;
	}

	public String getAiSource() {
		return aiSource;
	}

	public void setAiSource(String aiSource) {
		this.aiSource = aiSource;
	}

	public Date getAiAttributedAt() {
		return aiAttributedAt;
	}

	public void setAiAttributedAt(Date aiAttributedAt) {
		this.aiAttributedAt = aiAttributedAt;
	}

	@Override
	public String toString (){
		return "订单明细ID:"+(orderItemId == null ? "空" : orderItemId)+"，订单ID:"+(orderId == null ? "空" : orderId)+"，封面:"+(cover == null ? "空" : cover)+"，商品ID:"+(productId == null ? "空" : productId)+"，商品名称:"+(productName == null ? "空" : productName)+"，属性值id组hash:"+(propertyValueIdHash == null ? "空" : propertyValueIdHash)+"，属性信息:"+(propertyInfo == null ? "空" : propertyInfo)+"，价格:"+(itemAmount == null ? "空" : itemAmount)+"，数量:"+(buyCount == null ? "空" : buyCount)+"，状态 1:正常 0:已退款:"+(orderItemStatus == null ? "空" : orderItemStatus)+"，备注:"+(remark == null ? "空" : remark)+"，退款订单号:"+(refundOrderId == null ? "空" : refundOrderId);
	}
}
