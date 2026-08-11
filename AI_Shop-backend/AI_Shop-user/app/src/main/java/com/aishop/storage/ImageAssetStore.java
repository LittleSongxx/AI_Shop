package com.aishop.storage;

import com.aishop.utils.ImageCompressUtils;

public interface ImageAssetStore {

    String save(ImageCompressUtils.PreparedImage image, boolean createThumbnail);

    String saveQuarantine(ImageCompressUtils.PreparedImage image);

    String copyQuarantineToApproved(String quarantineStorageKey, boolean createThumbnail);

    byte[] read(String storageKey);

    boolean exists(String storageKey);

    void deleteWithThumbnail(String storageKey);
}
