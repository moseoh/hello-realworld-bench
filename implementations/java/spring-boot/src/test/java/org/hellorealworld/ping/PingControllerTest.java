package org.hellorealworld.ping;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class PingControllerTest {

	@Test
	void pingReturnsPongMessage() {
		assertThat(new PingController().ping()).containsEntry("message", "pong");
	}
}
