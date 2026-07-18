package com.myshop.entity.vo;

import lombok.Data;

import java.io.Serializable;

@Data
public class SignRecordSyncResultVO implements Serializable {

    private int totalInDb;

    private int synced;

    private int skipped;

    private int notFound;
}
