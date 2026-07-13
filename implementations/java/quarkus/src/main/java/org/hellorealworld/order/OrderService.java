package org.hellorealworld.order;

import java.time.Instant;
import java.util.UUID;

import org.hibernate.Session;

import io.quarkus.arc.InjectableInstance;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.inject.Any;
import jakarta.inject.Inject;
import jakarta.transaction.Transactional;

@ApplicationScoped
class OrderService {

    @Inject
    @Any
    InjectableInstance<Session> sessions;

    @Transactional
    CreateOrderResponse createOrder(CreateOrderRequest request) {
        String orderId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        int totalCents = request.items().stream()
                .mapToInt(item -> item.quantity() * item.unitPriceCents())
                .sum();

        OrderEntity order = new OrderEntity(orderId, request.customerId(), "accepted", totalCents, now);
        for (CreateOrderRequest.OrderItemRequest item : request.items()) {
            order.addItem(new OrderItemEntity(
                    UUID.randomUUID().toString(),
                    order,
                    item.sku(),
                    item.quantity(),
                    item.unitPriceCents()
            ));
        }

        Session session = sessions.getActive();
        session.persist(order);
        session.persist(new OutboxEventEntity(
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
