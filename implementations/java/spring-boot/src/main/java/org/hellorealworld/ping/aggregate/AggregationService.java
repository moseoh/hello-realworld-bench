package org.hellorealworld.ping.aggregate;

import java.util.concurrent.CompletableFuture;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;

@Service
class AggregationService {

	private final UpstreamClient upstreamClient;
	private final AggregationOperationExecutor executor;

	AggregationService(
			UpstreamClient upstreamClient,
			@Qualifier("aggregationTaskExecutor") AggregationOperationExecutor executor
	) {
		this.upstreamClient = upstreamClient;
		this.executor = executor;
	}

	AggregateResponse aggregate(String customerId, String sku) {
		CompletableFuture<CustomerProfile> profile = executor.submit(
				() -> upstreamClient.fetchProfile(customerId)
		);
		CompletableFuture<RecommendationResponse> recommendations = executor.submit(
				() -> upstreamClient.fetchRecommendations(customerId)
		);
		CompletableFuture<InventoryStatus> inventory = executor.submit(
				() -> upstreamClient.fetchInventory(sku)
		).exceptionally(ignored -> new InventoryStatus(sku, false, 0)
		);

		return new AggregateResponse(
				profile.join(),
				recommendations.join().items(),
				inventory.join()
		);
	}
}
