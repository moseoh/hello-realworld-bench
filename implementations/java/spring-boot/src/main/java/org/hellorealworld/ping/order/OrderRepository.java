package org.hellorealworld.ping.order;

import org.springframework.context.annotation.Profile;
import org.springframework.data.jpa.repository.JpaRepository;

@Profile("transactional")
interface OrderRepository extends JpaRepository<OrderRecord, String> {
}
