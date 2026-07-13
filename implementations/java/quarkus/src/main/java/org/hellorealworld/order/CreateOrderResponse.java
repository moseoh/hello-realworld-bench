package org.hellorealworld.order;

public record CreateOrderResponse(String orderId, String status, int totalCents) {
}
