package com.simlect.entity.query;

import lombok.Data;

@Data
public class UserSignRecordDetailQuery extends BaseParam {

    private String userId;

    private String signDate;

    private String signDateStart;

    private String signDateEnd;

    private Integer signType;

    private java.util.Date createTimeStart;
}
