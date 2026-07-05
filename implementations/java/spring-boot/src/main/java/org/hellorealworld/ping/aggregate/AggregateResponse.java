package org.hellorealworld.ping.aggregate;

import java.util.List;

record AggregateResponse(
		CustomerProfile customer,
		List<RecommendationItem> recommendations,
		InventoryStatus inventory
) {
}
