package org.hellorealworld.order;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.Matchers.matchesPattern;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import io.agroal.api.AgroalDataSource;
import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.junit.QuarkusTestProfile;
import io.quarkus.test.junit.TestProfile;
import jakarta.inject.Inject;
import jakarta.ws.rs.core.MediaType;

@QuarkusTest
@TestProfile(OrderResourceTest.TransactionalProfile.class)
class OrderResourceTest {

    @Inject
    AgroalDataSource dataSource;

    @BeforeEach
    void clearTables() throws SQLException {
        try (Connection connection = dataSource.getConnection();
                Statement statement = connection.createStatement()) {
            statement.execute("truncate table order_items, outbox_events, orders");
        }
    }

    @Test
    void rejectsInvalidOrder() {
        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("""
                        {"customerId":"", "items":[]}
                        """)
                .when().post("/orders")
                .then()
                .statusCode(400);
    }

    @Test
    void createsOrderItemsAndOutboxEventInOneRequest() throws SQLException {
        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("""
                        {
                          "customerId": "customer-123",
                          "items": [
                            {"sku": "SKU-001", "quantity": 2, "unitPriceCents": 1299},
                            {"sku": "SKU-002", "quantity": 1, "unitPriceCents": 599}
                          ]
                        }
                        """)
                .when().post("/orders")
                .then()
                .statusCode(201)
                .contentType(MediaType.APPLICATION_JSON)
                .body("orderId", matchesPattern("[0-9a-f-]{36}"))
                .body("status", is("accepted"))
                .body("totalCents", is(3197));

        try (Connection connection = dataSource.getConnection()) {
            org.junit.jupiter.api.Assertions.assertAll(
                    () -> org.junit.jupiter.api.Assertions.assertEquals(1, count(connection, "orders")),
                    () -> org.junit.jupiter.api.Assertions.assertEquals(2, count(connection, "order_items")),
                    () -> org.junit.jupiter.api.Assertions.assertEquals(1, count(connection, "outbox_events"))
            );
        }
    }

    private int count(Connection connection, String table) throws SQLException {
        try (Statement statement = connection.createStatement();
                ResultSet result = statement.executeQuery("select count(*) from " + table)) {
            result.next();
            return result.getInt(1);
        }
    }

    public static final class TransactionalProfile implements QuarkusTestProfile {
        @Override
        public String getConfigProfile() {
            return "transactional";
        }
    }
}
