package org.hellorealworld.ping.aggregate;

import java.util.concurrent.CompletableFuture;

import org.springframework.stereotype.Service;

@Service
class AggregationService {

	private final UpstreamClient upstreamClient;

	AggregationService(UpstreamClient upstreamClient) {
		this.upstreamClient = upstreamClient;
	}

	AggregateResponse aggregate(String customerId, String sku) {
		CompletableFuture<CustomerProfile> profile = CompletableFuture.supplyAsync(
				() -> upstreamClient.fetchProfile(customerId)
		);
		CompletableFuture<RecommendationResponse> recommendations = CompletableFuture.supplyAsync(
				() -> upstreamClient.fetchRecommendations(customerId)
		);
		CompletableFuture<InventoryStatus> inventory = CompletableFuture.supplyAsync(
				() -> upstreamClient.fetchInventory(sku)
		);

		return new AggregateResponse(
				profile.join(),
				recommendations.join().items(),
				inventory.join()
		);
	}
}
