package org.hellorealworld.ping.order;

import org.springframework.boot.persistence.autoconfigure.EntityScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

@Profile("transactional")
@Configuration
@EntityScan(basePackageClasses = OrderRecord.class)
class OrderPersistenceConfig {
}
