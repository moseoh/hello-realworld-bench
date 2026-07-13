package org.hellorealworld.query;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.contains;
import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.hasSize;
import static org.hamcrest.Matchers.nullValue;

import java.sql.Connection;
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

@QuarkusTest
@TestProfile(ProductResourceTest.ReadHeavyProfile.class)
class ProductResourceTest {

    @Inject
    AgroalDataSource dataSource;

    @BeforeEach
    void prepareProducts() throws SQLException {
        try (Connection connection = dataSource.getConnection();
                Statement statement = connection.createStatement()) {
            statement.execute("drop table if exists catalog_products");
            statement.execute("""
                    create table catalog_products (
                      id bigint primary key,
                      sku text not null,
                      name text not null,
                      category text not null,
                      price_cents integer not null,
                      rating_basis_points integer not null,
                      active boolean not null
                    )
                    """);

            for (long id = 1; id <= 21; id++) {
                int priceCents = id <= 2 ? 1000 : 1000 + (int) id - 2;
                statement.executeUpdate("""
                        insert into catalog_products
                            (id, sku, name, category, price_cents, rating_basis_points, active)
                        values (%d, 'SKU-%03d', 'Product %03d', 'electronics', %d, 4500, true)
                        """.formatted(id, id, id, priceCents));
            }

            statement.execute("""
                    insert into catalog_products
                        (id, sku, name, category, price_cents, rating_basis_points, active)
                    values
                        (22, 'SKU-022', 'Inactive product', 'electronics', 500, 4500, false),
                        (23, 'SKU-023', 'Book product', 'books', 500, 4500, true)
                    """);
        }
    }

    @Test
    void rejectsInvalidQueryParameters() {
        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", 1000)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 20)
                .queryParam("afterPriceCents", 1000)
                .when().get("/products")
                .then().statusCode(400);

        given()
                .queryParam("category", "unknown")
                .queryParam("minPriceCents", 1000)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 20)
                .when().get("/products")
                .then().statusCode(400);

        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", -1)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 20)
                .when().get("/products")
                .then().statusCode(400);

        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", 2000)
                .queryParam("maxPriceCents", 1000)
                .queryParam("limit", 20)
                .when().get("/products")
                .then().statusCode(400);

        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", 1000)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 10)
                .when().get("/products")
                .then().statusCode(400);

        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", 1000)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 20)
                .queryParam("afterPriceCents", 1000)
                .queryParam("afterId", 0)
                .when().get("/products")
                .then().statusCode(400);
    }

    @Test
    void returnsFilteredOrderedPageAndCursorOnlyWhenAnotherRowExists() {
        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", 1000)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 20)
                .when().get("/products")
                .then()
                .statusCode(200)
                .body("items", hasSize(20))
                .body("items.id", contains(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20))
                .body("items[0].sku", equalTo("SKU-001"))
                .body("items[0].name", equalTo("Product 001"))
                .body("items[0].category", equalTo("electronics"))
                .body("items[0].priceCents", equalTo(1000))
                .body("items[0].ratingBasisPoints", equalTo(4500))
                .body("nextCursor.priceCents", equalTo(1018))
                .body("nextCursor.id", equalTo(20));

        given()
                .queryParam("category", "electronics")
                .queryParam("minPriceCents", 1000)
                .queryParam("maxPriceCents", 2000)
                .queryParam("limit", 20)
                .queryParam("afterPriceCents", 1018)
                .queryParam("afterId", 20)
                .when().get("/products")
                .then()
                .statusCode(200)
                .body("items", hasSize(1))
                .body("items[0].id", equalTo(21))
                .body("items[0].priceCents", equalTo(1019))
                .body("nextCursor", nullValue());
    }

    public static final class ReadHeavyProfile implements QuarkusTestProfile {
        @Override
        public String getConfigProfile() {
            return "read-heavy";
        }

        @Override
        public Map<String, String> getConfigOverrides() {
            return Map.of(
                    "quarkus.datasource.db-kind", "h2",
                    "quarkus.datasource.jdbc.url", "jdbc:h2:mem:products;DB_CLOSE_DELAY=-1",
                    "quarkus.datasource.devservices.enabled", "false",
                    "quarkus.hibernate-orm.\"read-heavy\".schema-management.strategy", "none"
            );
        }
    }
}
