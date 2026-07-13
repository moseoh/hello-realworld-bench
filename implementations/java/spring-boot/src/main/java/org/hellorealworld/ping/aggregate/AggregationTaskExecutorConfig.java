package org.hellorealworld.ping.aggregate;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.task.AsyncTaskExecutor;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

@Configuration
class AggregationTaskExecutorConfig {

	@Bean("aggregationTaskExecutor")
	AsyncTaskExecutor aggregationTaskExecutor(
			@Value("${AGGREGATION_MAX_CONCURRENT_UPSTREAM_REQUESTS:${aggregation.max-concurrent-upstream-requests:128}}")
			int maxConcurrentRequests,
			@Value("${AGGREGATION_MAX_PENDING_UPSTREAM_REQUESTS:${aggregation.max-pending-upstream-requests:128}}")
			int maxPendingRequests
	) {
		if (maxConcurrentRequests < 1 || maxPendingRequests < 0) {
			throw new IllegalArgumentException("aggregation executor limits are invalid");
		}
		ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
		executor.setCorePoolSize(maxConcurrentRequests);
		executor.setMaxPoolSize(maxConcurrentRequests);
		executor.setQueueCapacity(maxPendingRequests);
		executor.setThreadNamePrefix("aggregation-");
		executor.initialize();
		return executor;
	}
}
