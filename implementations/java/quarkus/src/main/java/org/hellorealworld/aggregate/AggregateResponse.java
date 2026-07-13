package org.hellorealworld.aggregate;

import java.util.List;

public record AggregateResponse(
        CustomerProfile customer,
        List<RecommendationItem> recommendations,
        InventoryStatus inventory
) {
}
