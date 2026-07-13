package org.hellorealworld.ping.aggregate;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
class AggregationTaskExecutorConfig {

	@Bean(value = "aggregationTaskExecutor", destroyMethod = "close")
	BoundedAggregationOperationExecutor aggregationTaskExecutor(
			@Value("${AGGREGATION_MAX_CONCURRENT_UPSTREAM_REQUESTS:${aggregation.max-concurrent-upstream-requests:128}}")
			int maxConcurrentRequests,
			@Value("${AGGREGATION_MAX_PENDING_UPSTREAM_REQUESTS:${aggregation.max-pending-upstream-requests:128}}")
			int maxPendingRequests,
			@Value("${AGGREGATION_HTTP_CONNECTION_REQUEST_TIMEOUT_MS:${aggregation.http.connection-request-timeout-ms:500}}")
			long acquisitionTimeoutMs
	) {
		return new BoundedAggregationOperationExecutor(
				maxConcurrentRequests,
				maxPendingRequests,
				acquisitionTimeoutMs
		);
	}
}
