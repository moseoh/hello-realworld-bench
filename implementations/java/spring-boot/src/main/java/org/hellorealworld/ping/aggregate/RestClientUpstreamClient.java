package org.hellorealworld.ping.aggregate;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
class RestClientUpstreamClient implements UpstreamClient {

	private final RestClient restClient;

	RestClientUpstreamClient(
			@Value("${mock.upstream.base-url:http://localhost:8081}") String baseUrl
	) {
		this.restClient = RestClient.builder().baseUrl(baseUrl).build();
	}

	@Override
	public CustomerProfile fetchProfile(String customerId) {
		return restClient.get()
				.uri("/profile?customerId={customerId}", customerId)
				.retrieve()
				.body(CustomerProfile.class);
	}

	@Override
	public RecommendationResponse fetchRecommendations(String customerId) {
		return restClient.get()
				.uri("/recommendations?customerId={customerId}", customerId)
				.retrieve()
				.body(RecommendationResponse.class);
	}

	@Override
	public InventoryStatus fetchInventory(String sku) {
		return restClient.get()
				.uri("/inventory?sku={sku}", sku)
				.retrieve()
				.body(InventoryStatus.class);
	}
}
