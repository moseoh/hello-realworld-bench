package org.hellorealworld.ping.product;

import org.springframework.boot.persistence.autoconfigure.EntityScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

@Profile("read-heavy")
@Configuration
@EntityScan(basePackageClasses = ProductRecord.class)
class ProductPersistenceConfig {
}
