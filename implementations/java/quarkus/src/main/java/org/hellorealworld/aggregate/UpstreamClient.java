package org.hellorealworld.aggregate;

import java.net.URI;
import java.util.Objects;
import java.util.concurrent.TimeUnit;

import org.eclipse.microprofile.config.inject.ConfigProperty;

import io.quarkus.rest.client.reactive.QuarkusRestClientBuilder;
import io.smallrye.mutiny.Uni;
import io.vertx.core.http.HttpClientOptions;
import jakarta.annotation.PreDestroy;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

@ApplicationScoped
class UpstreamClient {

    private final UpstreamApi api;

    UpstreamClient(UpstreamApi api) {
        this.api = Objects.requireNonNull(api);
    }

    @Inject
    UpstreamClient(
            @ConfigProperty(name = "mock.upstream.base-url") String baseUrl,
            @ConfigProperty(name = "aggregation.http.connect-timeout-ms") int connectTimeoutMs,
            @ConfigProperty(name = "aggregation.http.response-timeout-ms") int responseTimeoutMs,
            @ConfigProperty(name = "aggregation.http.max-connections") int maxConnections,
            @ConfigProperty(name = "aggregation.http.max-connections-per-route") int maxConnectionsPerRoute,
            @ConfigProperty(name = "aggregation.max-pending-upstream-requests") int maxPendingRequests
    ) {
        if (connectTimeoutMs < 1 || responseTimeoutMs < 1 || maxConnections < 1
                || maxConnectionsPerRoute < 1 || maxPendingRequests < 0) {
            throw new IllegalArgumentException("aggregation HTTP client limits are invalid");
        }

        HttpClientOptions options = new HttpClientOptions()
                .setConnectTimeout(connectTimeoutMs)
                .setMaxPoolSize(Math.min(maxConnections, maxConnectionsPerRoute))
                .setMaxWaitQueueSize(maxPendingRequests)
                .setKeepAlive(true)
                .setPipelining(false);

        api = QuarkusRestClientBuilder.newBuilder()
                .baseUri(URI.create(baseUrl))
                .httpClientOptions(options)
                .connectTimeout(connectTimeoutMs, TimeUnit.MILLISECONDS)
                .readTimeout(responseTimeoutMs, TimeUnit.MILLISECONDS)
                .build(UpstreamApi.class);
    }

    Uni<CustomerProfile> profile(String customerId) {
        return api.profile(customerId);
    }

    Uni<RecommendationResponse> recommendations(String customerId) {
        return api.recommendations(customerId);
    }

    Uni<InventoryStatus> inventory(String sku) {
        return api.inventory(sku);
    }

    @PreDestroy
    void close() throws Exception {
        if (api instanceof AutoCloseable closeable) {
            closeable.close();
        }
    }
}
