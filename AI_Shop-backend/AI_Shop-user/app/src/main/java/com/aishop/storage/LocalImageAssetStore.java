package com.aishop.storage;

import com.aishop.utils.FileUtils;
import com.aishop.utils.ImageCompressUtils;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Component;

@Component
public class LocalImageAssetStore implements ImageAssetStore {

    @Resource
    private FileUtils fileUtils;

    @Override
    public String save(ImageCompressUtils.PreparedImage image, boolean createThumbnail) {
        return fileUtils.savePreparedImage(image, createThumbnail);
    }

    @Override
    public String saveQuarantine(ImageCompressUtils.PreparedImage image) {
        return fileUtils.saveModerationQuarantineImage(image);
    }

    @Override
    public String copyQuarantineToApproved(String quarantineStorageKey, boolean createThumbnail) {
        String suffix = com.aishop.utils.StringTools.getFileSuffix(quarantineStorageKey);
        String approvedKey = fileUtils.allocateNormalImageRelativePath(suffix);
        fileUtils.copyQuarantineToNormal(quarantineStorageKey, approvedKey, createThumbnail);
        return approvedKey;
    }

    @Override
    public byte[] read(String storageKey) {
        return fileUtils.readStoredFile(storageKey);
    }

    @Override
    public boolean exists(String storageKey) {
        return fileUtils.storedFileExists(storageKey);
    }

    @Override
    public void deleteWithThumbnail(String storageKey) {
        fileUtils.deleteUserImageWithThumbnailQuietly(storageKey);
    }
}
