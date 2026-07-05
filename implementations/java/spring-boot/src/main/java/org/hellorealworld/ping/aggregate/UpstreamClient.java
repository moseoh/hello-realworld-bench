package org.hellorealworld.ping.aggregate;

interface UpstreamClient {
	CustomerProfile fetchProfile(String customerId);

	RecommendationResponse fetchRecommendations(String customerId);

	InventoryStatus fetchInventory(String sku);
}
