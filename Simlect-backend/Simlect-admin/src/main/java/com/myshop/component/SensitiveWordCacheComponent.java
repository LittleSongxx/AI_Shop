package com.myshop.component;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.TypeReference;
import com.myshop.constants.Constants;
import com.myshop.entity.dto.SensitiveWordCacheItem;
import com.myshop.entity.po.SensitiveWord;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

@Component
@Slf4j
public class SensitiveWordCacheComponent {

    private static final TypeReference<List<SensitiveWordCacheItem>> CACHE_LIST_TYPE =
            new TypeReference<>() {
            };

    @Resource
    private StringRedisTemplate stringRedisTemplate;

    public List<SensitiveWordCacheItem> loadFromRedis() {
        String raw = stringRedisTemplate.opsForValue().get(Constants.REDIS_KEY_SENSITIVE_WORD_PAYLOAD);
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return JSON.parseObject(raw, CACHE_LIST_TYPE);
        } catch (Exception e) {
            log.warn("敏感词 Redis 缓存解析失败，将回源 DB", e);
            return null;
        }
    }

    public void saveToRedis(List<SensitiveWord> wordList) {
        List<SensitiveWordCacheItem> items = new ArrayList<>();
        if (wordList != null) {
            for (SensitiveWord sw : wordList) {
                items.add(new SensitiveWordCacheItem(sw.getWord(), sw.getReplaceWord()));
            }
        }
        stringRedisTemplate.opsForValue().set(
                Constants.REDIS_KEY_SENSITIVE_WORD_PAYLOAD,
                JSON.toJSONString(items));
        stringRedisTemplate.opsForValue().set(
                Constants.REDIS_KEY_SENSITIVE_WORD_VERSION,
                String.valueOf(System.currentTimeMillis()));
    }

    public long getVersion() {
        String version = stringRedisTemplate.opsForValue().get(Constants.REDIS_KEY_SENSITIVE_WORD_VERSION);
        if (version == null || version.isBlank()) {
            return 0L;
        }
        try {
            return Long.parseLong(version);
        } catch (NumberFormatException e) {
            return 0L;
        }
    }
}
