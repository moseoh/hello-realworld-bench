package org.hellorealworld.ping.aggregate;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
class AggregationController {

	private final AggregationService aggregationService;

	AggregationController(AggregationService aggregationService) {
		this.aggregationService = aggregationService;
	}

	@GetMapping("/aggregate")
	AggregateResponse aggregate(
			@RequestParam(defaultValue = "customer-001") String customerId,
			@RequestParam(defaultValue = "SKU-001") String sku
	) {
		return aggregationService.aggregate(customerId, sku);
	}
}
