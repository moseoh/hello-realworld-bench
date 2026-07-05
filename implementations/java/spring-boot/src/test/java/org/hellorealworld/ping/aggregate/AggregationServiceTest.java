package org.hellorealworld.ping.aggregate;

import java.util.List;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class AggregationServiceTest {

	@Test
	void aggregatesUpstreamResponses() {
		AggregationService service = new AggregationService(new StubUpstreamClient());

		AggregateResponse response = service.aggregate("customer-001", "SKU-001");

		assertThat(response.customer().id()).isEqualTo("customer-001");
		assertThat(response.recommendations()).hasSize(2);
		assertThat(response.inventory().available()).isTrue();
	}

	private static class StubUpstreamClient implements UpstreamClient {
		@Override
		public CustomerProfile fetchProfile(String customerId) {
			return new CustomerProfile(customerId, "gold", "north-america");
		}

		@Override
		public RecommendationResponse fetchRecommendations(String customerId) {
			return new RecommendationResponse(List.of(
					new RecommendationItem("SKU-101", 0.91),
					new RecommendationItem("SKU-102", 0.84)
			));
		}

		@Override
		public InventoryStatus fetchInventory(String sku) {
			return new InventoryStatus(sku, true, 42);
		}
	}
}
