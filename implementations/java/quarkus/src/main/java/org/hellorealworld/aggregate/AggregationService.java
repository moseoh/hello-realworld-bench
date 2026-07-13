package org.hellorealworld.aggregate;

import io.smallrye.mutiny.Uni;
import jakarta.enterprise.context.ApplicationScoped;

@ApplicationScoped
class AggregationService {

    private final UpstreamClient upstreamClient;
    private final UpstreamRequestLimiter limiter;

    AggregationService(UpstreamClient upstreamClient, UpstreamRequestLimiter limiter) {
        this.upstreamClient = upstreamClient;
        this.limiter = limiter;
    }

    Uni<AggregateResponse> aggregate(String customerId, String sku) {
        Uni<CustomerProfile> customer = limiter.execute(() -> upstreamClient.profile(customerId));
        Uni<RecommendationResponse> recommendations = limiter.execute(
                () -> upstreamClient.recommendations(customerId)
        );
        Uni<InventoryStatus> inventory = limiter.execute(() -> upstreamClient.inventory(sku))
                .onFailure().recoverWithItem(new InventoryStatus(sku, false, 0));

        return Uni.combine().all().unis(customer, recommendations, inventory).asTuple()
                .map(responses -> new AggregateResponse(
                        responses.getItem1(),
                        responses.getItem2().items(),
                        responses.getItem3()
                ));
    }
}
