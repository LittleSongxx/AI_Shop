package com.myshop.config;

import org.springframework.cloud.loadbalancer.annotation.LoadBalancerClients;
import org.springframework.context.annotation.Configuration;

@Configuration
@LoadBalancerClients(defaultConfiguration = SimlectLoadBalancerClientConfiguration.class)
public class SimlectLoadBalancerAutoConfiguration {
}
