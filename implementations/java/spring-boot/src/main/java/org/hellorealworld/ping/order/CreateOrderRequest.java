package org.hellorealworld.ping.order;

import java.util.List;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;

record CreateOrderRequest(
		@NotBlank String customerId,
		@NotEmpty List<@Valid OrderItemRequest> items
) {
	record OrderItemRequest(
			@NotBlank String sku,
			@Min(1) int quantity,
			@Min(1) int unitPriceCents
	) {
	}
}
