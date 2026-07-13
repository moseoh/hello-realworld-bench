package org.hellorealworld.aggregate;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import io.quarkus.test.common.QuarkusTestResourceLifecycleManager;

public final class MockUpstreamResource implements QuarkusTestResourceLifecycleManager {

    private HttpServer server;
    private ExecutorService executor;
    private final CyclicBarrier parallelRequests = new CyclicBarrier(3);

    @Override
    public Map<String, String> start() {
        try {
            server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
        } catch (IOException exception) {
            throw new IllegalStateException("mock upstream 서버를 시작할 수 없습니다", exception);
        }
        server.createContext("/profile", exchange -> respond(exchange,
                "{\"id\":\"%s\",\"tier\":\"gold\",\"region\":\"north-america\"}"
                        .formatted(queryValue(exchange, "customerId"))));
        server.createContext("/recommendations", exchange -> respond(exchange,
                "{\"items\":[{\"sku\":\"SKU-101\",\"score\":0.91},{\"sku\":\"SKU-102\",\"score\":0.84}]}"));
        server.createContext("/inventory", exchange -> {
            String sku = queryValue(exchange, "sku");
            if ("SKU-unavailable".equals(sku)) {
                respond(exchange, 503, "{\"error\":\"inventory unavailable\"}");
                return;
            }
            respond(exchange, "{\"sku\":\"%s\",\"available\":true,\"quantity\":42}".formatted(sku));
        });
        executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.start();

        String baseUrl = "http://localhost:" + server.getAddress().getPort();
        return Map.of(
                "MOCK_UPSTREAM_BASE_URL", baseUrl,
                "AGGREGATION_HTTP_CONNECT_TIMEOUT_MS", "500",
                "AGGREGATION_HTTP_RESPONSE_TIMEOUT_MS", "1000",
                "AGGREGATION_HTTP_CONNECTION_REQUEST_TIMEOUT_MS", "500",
                "AGGREGATION_HTTP_MAX_CONNECTIONS", "128",
                "AGGREGATION_HTTP_MAX_CONNECTIONS_PER_ROUTE", "128",
                "AGGREGATION_MAX_CONCURRENT_UPSTREAM_REQUESTS", "128",
                "AGGREGATION_MAX_PENDING_UPSTREAM_REQUESTS", "128"
        );
    }

    @Override
    public void stop() {
        if (server != null) {
            server.stop(0);
        }
        if (executor != null) {
            executor.shutdownNow();
        }
    }

    private void respond(HttpExchange exchange, String body) throws IOException {
        if (exchange.getRequestURI().getRawQuery().contains("parallel")) {
            try {
                parallelRequests.await(2, TimeUnit.SECONDS);
            } catch (Exception exception) {
                respond(exchange, 500, "{\"error\":\"upstream calls were not concurrent\"}");
                return;
            }
        }
        respond(exchange, 200, body);
    }

    private void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }

    private String queryValue(HttpExchange exchange, String name) {
        for (String pair : exchange.getRequestURI().getRawQuery().split("&")) {
            String[] parts = pair.split("=", 2);
            if (name.equals(parts[0])) {
                return parts[1];
            }
        }
        throw new IllegalArgumentException("필수 쿼리 파라미터가 없습니다: " + name);
    }
}
