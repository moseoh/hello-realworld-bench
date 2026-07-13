package org.hellorealworld.aggregate;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.Matchers.hasSize;

import org.junit.jupiter.api.Test;

import io.quarkus.test.common.QuarkusTestResource;
import io.quarkus.test.junit.QuarkusTest;

@QuarkusTest
@QuarkusTestResource(MockUpstreamResource.class)
class AggregationResourceTest {

    @Test
    void aggregatesThreeConcurrentUpstreamResponses() {
        given()
                .queryParam("customerId", "customer-parallel")
                .queryParam("sku", "SKU-parallel")
                .when().get("/aggregate")
                .then()
                .statusCode(200)
                .body("customer.id", is("customer-parallel"))
                .body("recommendations", hasSize(2))
                .body("inventory.sku", is("SKU-parallel"))
                .body("inventory.available", is(true))
                .body("inventory.quantity", is(42));
    }

    @Test
    void usesFallbackOnlyWhenInventoryFails() {
        given()
                .queryParam("customerId", "customer-001")
                .queryParam("sku", "SKU-unavailable")
                .when().get("/aggregate")
                .then()
                .statusCode(200)
                .body("customer.id", is("customer-001"))
                .body("recommendations", hasSize(2))
                .body("inventory.sku", is("SKU-unavailable"))
                .body("inventory.available", is(false))
                .body("inventory.quantity", is(0));
    }
}
