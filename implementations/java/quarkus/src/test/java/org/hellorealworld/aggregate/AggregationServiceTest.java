package org.hellorealworld.aggregate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;

import org.junit.jupiter.api.Test;

import io.smallrye.mutiny.Uni;

class AggregationServiceTest {

    @Test
    void usesInventoryFallbackWhenInventorySubmissionIsRejected() throws Exception {
        StubUpstreamClient client = new StubUpstreamClient();
        RejectingLimiter limiter = new RejectingLimiter(3);
        try {
            AggregationService service = new AggregationService(client, limiter);

            AggregateResponse response = service.aggregate("customer-001", "SKU-001")
                    .await().indefinitely();

            assertEquals("customer-001", response.customer().id());
            assertEquals("SKU-001", response.inventory().sku());
            assertFalse(response.inventory().available());
            assertEquals(0, response.inventory().quantity());
        } finally {
            client.close();
            limiter.close();
        }
    }

    @Test
    void failsAggregationWhenRequiredSubmissionIsRejected() throws Exception {
        for (int rejectedSubmission : List.of(1, 2)) {
            StubUpstreamClient client = new StubUpstreamClient();
            RejectingLimiter limiter = new RejectingLimiter(rejectedSubmission);
            try {
                AggregationService service = new AggregationService(client, limiter);

                assertThrows(
                        RejectedExecutionException.class,
                        () -> service.aggregate("customer-001", "SKU-001").await().indefinitely()
                );
            } finally {
                client.close();
                limiter.close();
            }
        }
    }

    private static final class RejectingLimiter extends UpstreamRequestLimiter {
        private final int rejectedSubmission;
        private final AtomicInteger submissions = new AtomicInteger();

        private RejectingLimiter(int rejectedSubmission) {
            super(1, 1, 5000);
            this.rejectedSubmission = rejectedSubmission;
        }

        @Override
        <T> Uni<T> execute(Supplier<Uni<T>> request) {
            if (submissions.incrementAndGet() == rejectedSubmission) {
                return Uni.createFrom().failure(
                        new RejectedExecutionException("saturated")
                );
            }
            return request.get();
        }
    }

    private static final class StubUpstreamClient extends UpstreamClient {
        private StubUpstreamClient() {
            super(stubApi());
        }

        private static UpstreamApi stubApi() {
            return new UpstreamApi() {
                @Override
                public Uni<CustomerProfile> profile(String customerId) {
                    return Uni.createFrom().item(
                            new CustomerProfile(customerId, "gold", "north-america")
                    );
                }

                @Override
                public Uni<RecommendationResponse> recommendations(String customerId) {
                    return Uni.createFrom().item(new RecommendationResponse(List.of(
                            new RecommendationItem("SKU-101", 0.91),
                            new RecommendationItem("SKU-102", 0.84)
                    )));
                }

                @Override
                public Uni<InventoryStatus> inventory(String sku) {
                    return Uni.createFrom().item(new InventoryStatus(sku, true, 42));
                }
            };
        }
    }
}
