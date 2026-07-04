package org.hellorealworld.ping.order;

import jakarta.validation.Valid;

import org.springframework.context.annotation.Profile;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@Profile("transactional")
@RestController
class OrderController {

	private final OrderService orderService;

	OrderController(OrderService orderService) {
		this.orderService = orderService;
	}

	@PostMapping("/orders")
	@ResponseStatus(HttpStatus.CREATED)
	CreateOrderResponse createOrder(@Valid @RequestBody CreateOrderRequest request) {
		return orderService.createOrder(request);
	}
}
