package org.hellorealworld.ping.aggregate;

import java.util.concurrent.CompletableFuture;

import org.springframework.core.task.AsyncTaskExecutor;
import org.springframework.stereotype.Service;

@Service
class AggregationService {

	private final UpstreamClient upstreamClient;
	private final AsyncTaskExecutor executor;

	AggregationService(UpstreamClient upstreamClient, AsyncTaskExecutor executor) {
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
}
