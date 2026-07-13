package org.hellorealworld.aggregate;

import io.smallrye.mutiny.Uni;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;

@Path("/")
@Produces(MediaType.APPLICATION_JSON)
interface UpstreamApi {

    @GET
    @Path("/profile")
    Uni<CustomerProfile> profile(@QueryParam("customerId") String customerId);

    @GET
    @Path("/recommendations")
    Uni<RecommendationResponse> recommendations(@QueryParam("customerId") String customerId);

    @GET
    @Path("/inventory")
    Uni<InventoryStatus> inventory(@QueryParam("sku") String sku);
}
