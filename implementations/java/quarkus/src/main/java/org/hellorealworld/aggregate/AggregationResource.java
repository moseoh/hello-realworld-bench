package org.hellorealworld.aggregate;

import io.smallrye.mutiny.Uni;
import jakarta.ws.rs.DefaultValue;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;

@Path("/aggregate")
@Produces(MediaType.APPLICATION_JSON)
public class AggregationResource {

    private final AggregationService aggregationService;

    AggregationResource(AggregationService aggregationService) {
        this.aggregationService = aggregationService;
    }

    @GET
    public Uni<AggregateResponse> aggregate(
            @DefaultValue("customer-001") @QueryParam("customerId") String customerId,
            @DefaultValue("SKU-001") @QueryParam("sku") String sku
    ) {
        return aggregationService.aggregate(customerId, sku);
    }
}
