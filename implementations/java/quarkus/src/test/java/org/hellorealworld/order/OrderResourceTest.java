package org.hellorealworld.order;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;
import static org.hamcrest.Matchers.matchesPattern;

import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Map;

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
    void prepareTables() throws SQLException {
        try (Connection connection = dataSource.getConnection();
                Statement statement = connection.createStatement()) {
            statement.execute("drop table if exists order_items");
            statement.execute("drop table if exists outbox_events");
            statement.execute("drop table if exists orders");
            statement.execute("""
                    create table orders (
                      id varchar primary key,
                      customer_id varchar not null,
                      status varchar not null,
                      total_cents integer not null,
                      created_at timestamp with time zone not null
                    )
                    """);
            statement.execute("""
                    create table order_items (
                      id varchar primary key,
                      order_id varchar not null references orders(id),
                      sku varchar not null,
                      quantity integer not null,
                      unit_price_cents integer not null
                    )
                    """);
            statement.execute("""
                    create table outbox_events (
                      id varchar primary key,
                      aggregate_type varchar not null,
                      aggregate_id varchar not null,
                      event_type varchar not null,
                      payload_json varchar not null,
                      created_at timestamp with time zone not null,
                      published_at timestamp with time zone null
                    )
                    """);
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

        @Override
        public Map<String, String> getConfigOverrides() {
            return Map.of(
                    "quarkus.datasource.db-kind", "h2",
                    "quarkus.datasource.jdbc.url", "jdbc:h2:mem:orders;DB_CLOSE_DELAY=-1",
                    "quarkus.datasource.devservices.enabled", "false",
                    "quarkus.hibernate-orm.\"transactional\".schema-management.strategy", "none",
                    "quarkus.flyway.active", "false",
                    "quarkus.flyway.migrate-at-start", "false"
            );
        }
    }
}
