package org.hellorealworld.ping;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;

import org.junit.jupiter.api.Test;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.ws.rs.core.MediaType;

@QuarkusTest
class PingResourceTest {

    @Test
    void returnsPongMessage() {
        given()
                .accept(MediaType.APPLICATION_JSON)
                .when().get("/ping")
                .then()
                .statusCode(200)
                .contentType(MediaType.APPLICATION_JSON)
                .body("message", is("pong"));
    }
}
