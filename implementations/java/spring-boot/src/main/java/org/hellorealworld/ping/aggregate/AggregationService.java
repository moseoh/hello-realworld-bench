package org.hellorealworld.ping.aggregate;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import jakarta.annotation.PreDestroy;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

@Service
class AggregationService {

	private final UpstreamClient upstreamClient;
	private final ExecutorService executor;

	@Autowired
	AggregationService(UpstreamClient upstreamClient) {
		this(
				upstreamClient,
				Executors.newThreadPerTaskExecutor(
						Thread.ofPlatform().name("aggregate-upstream-", 0).factory()
				)
		);
	}

	AggregationService(UpstreamClient upstreamClient, ExecutorService executor) {
		this.upstreamClient = upstreamClient;
		this.executor = executor;
	}

	AggregateResponse aggregate(String customerId, String sku) {
		CompletableFuture<CustomerProfile> profile = CompletableFuture.supplyAsync(
				() -> upstreamClient.fetchProfile(customerId),
				executor
		);
		CompletableFuture<RecommendationResponse> recommendations = CompletableFuture.supplyAsync(
				() -> upstreamClient.fetchRecommendations(customerId),
				executor
		);
		CompletableFuture<InventoryStatus> inventory = CompletableFuture.supplyAsync(
				() -> upstreamClient.fetchInventory(sku),
				executor
		).exceptionally(ignored -> new InventoryStatus(sku, false, 0)
		);

		return new AggregateResponse(
				profile.join(),
				recommendations.join().items(),
				inventory.join()
		);
	}

	@PreDestroy
	void shutdown() {
		executor.shutdown();
	}
}
