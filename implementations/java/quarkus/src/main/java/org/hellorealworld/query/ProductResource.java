package org.hellorealworld.query;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;

@Path("/products")
@Produces(MediaType.APPLICATION_JSON)
public class ProductResource {

    private final ProductQueryService service;

    ProductResource(ProductQueryService service) {
        this.service = service;
    }

    @GET
    public ProductPageResponse products(
            @QueryParam("category") String category,
            @QueryParam("minPriceCents") int minPriceCents,
            @QueryParam("maxPriceCents") int maxPriceCents,
            @QueryParam("limit") int limit,
            @QueryParam("afterPriceCents") Integer afterPriceCents,
            @QueryParam("afterId") Long afterId) {
        return service.findProducts(category, minPriceCents, maxPriceCents, limit,
                afterPriceCents, afterId);
    }
}
