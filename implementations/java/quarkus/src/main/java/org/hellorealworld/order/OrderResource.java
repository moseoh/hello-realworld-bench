package org.hellorealworld.order;

import jakarta.validation.Valid;
import jakarta.ws.rs.Consumes;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

@Path("/orders")
@Consumes(MediaType.APPLICATION_JSON)
@Produces(MediaType.APPLICATION_JSON)
public class OrderResource {

    private final OrderService orderService;

    OrderResource(OrderService orderService) {
        this.orderService = orderService;
    }

    @POST
    public Response createOrder(@Valid CreateOrderRequest request) {
        return Response.status(Response.Status.CREATED)
                .entity(orderService.createOrder(request))
                .build();
    }
}
