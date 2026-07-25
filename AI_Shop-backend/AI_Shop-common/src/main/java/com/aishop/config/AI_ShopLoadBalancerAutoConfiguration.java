package com.aishop.config;

import org.springframework.cloud.loadbalancer.annotation.LoadBalancerClients;
import org.springframework.context.annotation.Configuration;

@Configuration
@LoadBalancerClients(defaultConfiguration = AI_ShopLoadBalancerClientConfiguration.class)
public class AI_ShopLoadBalancerAutoConfiguration {
}
