package com.aishop.config;

import java.time.Duration;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

@Configuration
public class AdminRestClientConfig {

    @Bean
    public RestClient.Builder restClientBuilder(
            @Value("${aishop.agent.connect-timeout-ms:3000}") int connectTimeoutMs,
            @Value("${aishop.agent.read-timeout-ms:50000}") int readTimeoutMs) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofMillis(Math.max(connectTimeoutMs, 100)));
        requestFactory.setReadTimeout(Duration.ofMillis(Math.max(readTimeoutMs, 1000)));
        return RestClient.builder().requestFactory(requestFactory);
    }
}
