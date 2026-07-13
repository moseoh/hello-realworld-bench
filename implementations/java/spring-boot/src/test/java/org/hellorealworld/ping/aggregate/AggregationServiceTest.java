package org.hellorealworld.ping.aggregate;

import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AggregationServiceTest {

	@Test
	void aggregatesUpstreamResponses() {
		AggregationService service = new AggregationService(
				new StubUpstreamClient(),
				directExecutor()
		);

		AggregateResponse response = service.aggregate("customer-001", "SKU-001");

		assertThat(response.customer().id()).isEqualTo("customer-001");
		assertThat(response.recommendations()).hasSize(2);
		assertThat(response.inventory().available()).isTrue();
	}

	@Test
	void usesInventoryFallbackWhenUpstreamFails() {
		AggregationService service = new AggregationService(
				new InventoryFailureUpstreamClient(),
				directExecutor()
		);

		AggregateResponse response = service.aggregate("customer-001", "SKU-001");

		assertThat(response.customer().id()).isEqualTo("customer-001");
		assertThat(response.inventory().sku()).isEqualTo("SKU-001");
		assertThat(response.inventory().available()).isFalse();
		assertThat(response.inventory().quantity()).isZero();
	}

	@Test
	void usesInventoryFallbackWhenInventorySubmissionIsRejected() {
		AggregationService service = new AggregationService(
				new StubUpstreamClient(),
				rejectSubmission(3)
		);

		AggregateResponse response = service.aggregate("customer-001", "SKU-001");

		assertThat(response.customer().id()).isEqualTo("customer-001");
		assertThat(response.inventory()).isEqualTo(new InventoryStatus("SKU-001", false, 0));
	}

	@Test
	void failsAggregationWhenRequiredSubmissionIsRejected() {
		for (int rejectedSubmission : List.of(1, 2)) {
			AggregationService service = new AggregationService(
					new StubUpstreamClient(),
					rejectSubmission(rejectedSubmission)
			);

			assertThatThrownBy(() -> service.aggregate("customer-001", "SKU-001"))
					.isInstanceOf(CompletionException.class)
					.hasCauseInstanceOf(RejectedExecutionException.class);
		}
	}

	private AggregationOperationExecutor directExecutor() {
		return new AggregationOperationExecutor() {
			@Override
			public <T> CompletableFuture<T> submit(java.util.function.Supplier<T> operation) {
				try {
					return CompletableFuture.completedFuture(operation.get());
				} catch (RuntimeException exception) {
					return CompletableFuture.failedFuture(exception);
				}
			}
		};
	}

	private AggregationOperationExecutor rejectSubmission(int rejectedSubmission) {
		AtomicInteger submissions = new AtomicInteger();
		return new AggregationOperationExecutor() {
			@Override
			public <T> CompletableFuture<T> submit(java.util.function.Supplier<T> operation) {
				if (submissions.incrementAndGet() == rejectedSubmission) {
					return CompletableFuture.failedFuture(
							new RejectedExecutionException("saturated")
					);
				}
				return CompletableFuture.completedFuture(operation.get());
			}
		};
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

	private static class InventoryFailureUpstreamClient extends StubUpstreamClient {
		@Override
		public InventoryStatus fetchInventory(String sku) {
			throw new IllegalStateException("inventory timeout");
		}
	}
}
