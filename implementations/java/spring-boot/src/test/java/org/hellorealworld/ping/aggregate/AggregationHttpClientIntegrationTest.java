package org.hellorealworld.ping.aggregate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

import com.sun.net.httpserver.HttpServer;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.junit.jupiter.api.Test;

class AggregationHttpClientIntegrationTest {

	private final AggregationHttpClientConfig config = new AggregationHttpClientConfig();

	@Test
	void doesNotRetryServiceUnavailableResponses() throws Exception {
		AtomicInteger requests = new AtomicInteger();
		try (TestServer server = new TestServer(exchange -> {
			requests.incrementAndGet();
			byte[] body = "{\"error\":\"unavailable\"}".getBytes();
			exchange.sendResponseHeaders(503, body.length);
			exchange.getResponseBody().write(body);
			exchange.close();
		}); CloseableHttpClient httpClient = httpClient()) {
			RestClientUpstreamClient client = client(server, httpClient);

			assertThatThrownBy(() -> client.fetchInventory("SKU-001"));

			assertThat(requests).hasValue(1);
		}
	}

	@Test
	void doesNotRetryTransientIoFailures() throws Exception {
		AtomicInteger requests = new AtomicInteger();
		try (TestServer server = new TestServer(exchange -> {
			requests.incrementAndGet();
			exchange.close();
		}); CloseableHttpClient httpClient = httpClient()) {
			RestClientUpstreamClient client = client(server, httpClient);

			assertThatThrownBy(() -> client.fetchInventory("SKU-001"));

			assertThat(requests).hasValue(1);
		}
	}

	private CloseableHttpClient httpClient() {
		return config.aggregationHttpClient(500, 1000, 500, 128, 128);
	}

	private RestClientUpstreamClient client(TestServer server, CloseableHttpClient httpClient) {
		return new RestClientUpstreamClient(
				server.baseUrl(),
				config.aggregationRequestFactory(httpClient)
		);
	}

	private static final class TestServer implements AutoCloseable {
		private final HttpServer server;

		private TestServer(com.sun.net.httpserver.HttpHandler handler) throws IOException {
			server = HttpServer.create(new InetSocketAddress("localhost", 0), 0);
			server.createContext("/inventory", handler);
			server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
			server.start();
		}

		private String baseUrl() {
			return "http://localhost:" + server.getAddress().getPort();
		}

		@Override
		public void close() {
			server.stop(0);
		}
	}
}
