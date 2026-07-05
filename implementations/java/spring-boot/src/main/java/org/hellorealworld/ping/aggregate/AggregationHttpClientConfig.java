package org.hellorealworld.ping.aggregate;

import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.core5.util.TimeValue;
import org.apache.hc.core5.util.Timeout;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.HttpComponentsClientHttpRequestFactory;

@Configuration
class AggregationHttpClientConfig {

	@Bean(destroyMethod = "close")
	CloseableHttpClient aggregationHttpClient(
			@Value("${AGGREGATION_HTTP_CONNECT_TIMEOUT_MS:${aggregation.http.connect-timeout-ms:500}}")
			long connectTimeoutMs,
			@Value("${AGGREGATION_HTTP_RESPONSE_TIMEOUT_MS:${aggregation.http.response-timeout-ms:1000}}")
			long responseTimeoutMs,
			@Value("${AGGREGATION_HTTP_CONNECTION_REQUEST_TIMEOUT_MS:${aggregation.http.connection-request-timeout-ms:500}}")
			long connectionRequestTimeoutMs,
			@Value("${AGGREGATION_HTTP_MAX_CONNECTIONS:${aggregation.http.max-connections:128}}")
			int maxConnections,
			@Value("${AGGREGATION_HTTP_MAX_CONNECTIONS_PER_ROUTE:${aggregation.http.max-connections-per-route:128}}")
			int maxConnectionsPerRoute
	) {
		PoolingHttpClientConnectionManager connectionManager = PoolingHttpClientConnectionManagerBuilder.create()
				.setMaxConnTotal(maxConnections)
				.setMaxConnPerRoute(maxConnectionsPerRoute)
				.setDefaultConnectionConfig(ConnectionConfig.custom()
						.setConnectTimeout(Timeout.ofMilliseconds(connectTimeoutMs))
						.build())
				.build();

		RequestConfig requestConfig = RequestConfig.custom()
				.setResponseTimeout(timeout(responseTimeoutMs))
				.setConnectionRequestTimeout(Timeout.ofMilliseconds(connectionRequestTimeoutMs))
				.build();

		return HttpClients.custom()
				.setConnectionManager(connectionManager)
				.setDefaultRequestConfig(requestConfig)
				.evictExpiredConnections()
				.evictIdleConnections(TimeValue.ofSeconds(30))
				.build();
	}

	@Bean
	HttpComponentsClientHttpRequestFactory aggregationRequestFactory(CloseableHttpClient aggregationHttpClient) {
		return new HttpComponentsClientHttpRequestFactory(aggregationHttpClient);
	}

	private Timeout timeout(long timeoutMs) {
		if (timeoutMs <= 0) {
			return Timeout.DISABLED;
		}
		return Timeout.ofMilliseconds(timeoutMs);
	}
}
