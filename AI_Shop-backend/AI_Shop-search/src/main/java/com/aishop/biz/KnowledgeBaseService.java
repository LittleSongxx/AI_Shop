package com.aishop.biz;

import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

public interface KnowledgeBaseService {

    Map<String, Object> upload(
            MultipartFile file, String title, String owner, String domain);

    Map<String, Object> publish(long documentId, String owner);

    Map<String, Object> reindexPublished(long documentId, String owner);

    Map<String, Object> archive(long documentId);

    Map<String, Object> listDocuments(int pageNo, int pageSize, String status);

    Map<String, Object> listJobs(int pageNo, int pageSize, String status);

    Map<String, Object> listFaqCandidates(int pageNo, int pageSize, String status);

    Map<String, Object> reviewFaqCandidate(
            long candidateId,
            boolean approved,
            String reviewer,
            String remark,
            String correctedAnswer,
            String category);

    Map<String, Object> exactFaq(String question, String language, String channel);

    void submitFaqCandidate(
            String question, String answer, Integer sourceMessageId, String category);

    long releaseVersion();

    Map<String, Object> releaseCatalog();

    long invalidateCaches();

    List<Map<String, Object>> topFaq(int limit);
}
