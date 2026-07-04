package org.hellorealworld.ping.order;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import java.util.List;

import org.junit.jupiter.api.Test;

class OrderServiceTest {

	@Test
	void createsOrderAndOutboxEvent() {
		OrderRepository orderRepository = mock(OrderRepository.class);
		OutboxEventRepository outboxEventRepository = mock(OutboxEventRepository.class);
		OrderService service = new OrderService(orderRepository, outboxEventRepository);

		CreateOrderResponse response = service.createOrder(new CreateOrderRequest(
				"customer-123",
				List.of(new CreateOrderRequest.OrderItemRequest("SKU-001", 2, 1299))
		));

		assertThat(response.status()).isEqualTo("accepted");
		assertThat(response.totalCents()).isEqualTo(2598);
		assertThat(response.orderId()).isNotBlank();
		verify(orderRepository).save(any(OrderRecord.class));
		verify(outboxEventRepository).save(any(OutboxEventRecord.class));
	}
}
