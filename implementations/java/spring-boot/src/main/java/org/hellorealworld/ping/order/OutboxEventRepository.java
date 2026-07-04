package org.hellorealworld.ping.order;

import org.springframework.context.annotation.Profile;
import org.springframework.data.jpa.repository.JpaRepository;

@Profile("transactional")
interface OutboxEventRepository extends JpaRepository<OutboxEventRecord, String> {
}
