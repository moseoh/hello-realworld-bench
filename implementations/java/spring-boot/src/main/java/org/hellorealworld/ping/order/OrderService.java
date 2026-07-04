package org.hellorealworld.ping.order;

import java.time.Instant;
import java.util.UUID;

import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Profile("transactional")
@Service
class OrderService {

	private final OrderRepository orderRepository;
	private final OutboxEventRepository outboxEventRepository;

	OrderService(OrderRepository orderRepository, OutboxEventRepository outboxEventRepository) {
		this.orderRepository = orderRepository;
		this.outboxEventRepository = outboxEventRepository;
	}

	@Transactional
	CreateOrderResponse createOrder(CreateOrderRequest request) {
		String orderId = UUID.randomUUID().toString();
		Instant now = Instant.now();
		int totalCents = request.items().stream()
				.mapToInt(item -> item.quantity() * item.unitPriceCents())
				.sum();

		OrderRecord order = new OrderRecord(orderId, request.customerId(), "accepted", totalCents, now);
		for (CreateOrderRequest.OrderItemRequest item : request.items()) {
			order.addItem(new OrderItemRecord(
					UUID.randomUUID().toString(),
					order,
					item.sku(),
					item.quantity(),
					item.unitPriceCents()
			));
		}

		orderRepository.save(order);
		outboxEventRepository.save(new OutboxEventRecord(
				UUID.randomUUID().toString(),
				"order",
				orderId,
				"order.accepted",
				"""
				{"orderId":"%s","status":"accepted","totalCents":%d}
				""".formatted(orderId, totalCents).trim(),
				now
		));

		return new CreateOrderResponse(orderId, "accepted", totalCents);
	}
}
