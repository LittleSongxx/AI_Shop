package com.aishop.component;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.Executor;
import java.util.concurrent.ThreadPoolExecutor;

/**
 * Async executor configuration for background RAG chunk enrichment (P1-1 Contextual Retrieval).
 *
 * <p>Kept intentionally small so enrichment never competes with user-facing traffic.
 * A 200-item queue absorbs full document publishes; CallerRunsPolicy means overflow
 * falls back to the publish thread rather than dropping tasks.
 */
@Configuration
@EnableAsync
public class RagEnricherConfig {

    @Bean(name = "ragEnrichExecutor")
    public Executor ragEnrichExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(2);
        executor.setMaxPoolSize(4);
        executor.setQueueCapacity(200);
        executor.setThreadNamePrefix("rag-enrich-");
        // CallerRunsPolicy: publish thread handles overflow instead of dropping tasks —
        // acceptable because publish is an infrequent admin-triggered operation.
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
        executor.initialize();
        return executor;
    }
}
