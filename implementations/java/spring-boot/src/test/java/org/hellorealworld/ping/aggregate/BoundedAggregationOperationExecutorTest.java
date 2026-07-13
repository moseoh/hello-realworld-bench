package org.hellorealworld.ping.aggregate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
	import java.util.concurrent.CompletableFuture;
	import java.util.concurrent.CountDownLatch;
	import java.util.concurrent.RejectedExecutionException;
	import java.util.concurrent.ThreadFactory;
	import java.util.concurrent.TimeUnit;
	import java.util.concurrent.TimeoutException;

import org.junit.jupiter.api.Test;

class BoundedAggregationOperationExecutorTest {

	@Test
	void activeOperationDoesNotUsePendingAcquisitionTimeout() throws Exception {
		CountDownLatch allowWorkerStart = new CountDownLatch(1);
		ThreadFactory delayedFactory = task -> Thread.ofPlatform().unstarted(() -> {
			try {
				allowWorkerStart.await();
			} catch (InterruptedException exception) {
				Thread.currentThread().interrupt();
				return;
			}
			task.run();
		});
		try (BoundedAggregationOperationExecutor executor =
					new BoundedAggregationOperationExecutor(1, 1, 100, delayedFactory)) {
			CompletableFuture<String> active = executor.submit(() -> "active");

			Thread.sleep(200);
			assertThat(active).isNotDone();
			allowWorkerStart.countDown();

			assertThat(active.get(1, TimeUnit.SECONDS)).isEqualTo("active");
		}
	}

	@Test
	void closeFailsActiveOperationThatHasNotStarted() {
		CountDownLatch allowWorkerStart = new CountDownLatch(1);
		ThreadFactory delayedFactory = task -> Thread.ofPlatform().unstarted(() -> {
			try {
				allowWorkerStart.await();
			} catch (InterruptedException exception) {
				Thread.currentThread().interrupt();
				return;
			}
			task.run();
		});
		BoundedAggregationOperationExecutor executor =
				new BoundedAggregationOperationExecutor(1, 1, 100, delayedFactory);
		CompletableFuture<String> active = executor.submit(() -> "active");

		executor.close();

		assertThatThrownBy(() -> active.get(1, TimeUnit.SECONDS))
				.hasCauseInstanceOf(RejectedExecutionException.class);
	}

	@Test
	void boundsOperationsAt128ActiveAnd128Pending() throws Exception {
		try (BoundedAggregationOperationExecutor executor =
					new BoundedAggregationOperationExecutor(128, 128, 5000)) {
			CountDownLatch active = new CountDownLatch(128);
			CountDownLatch release = new CountDownLatch(1);
			List<CompletableFuture<String>> activeOperations = new ArrayList<>();
			for (int index = 0; index < 128; index++) {
				activeOperations.add(executor.submit(() -> await(active, release)));
			}
			assertThat(active.await(5, TimeUnit.SECONDS)).isTrue();

			List<CompletableFuture<String>> pendingOperations = new ArrayList<>();
			for (int index = 0; index < 128; index++) {
				pendingOperations.add(executor.submit(() -> "pending"));
			}

			CompletableFuture<String> overflow = executor.submit(() -> "overflow");
			assertThatThrownBy(overflow::join)
					.hasCauseInstanceOf(RejectedExecutionException.class);

			pendingOperations.forEach(operation -> operation.cancel(true));
			release.countDown();
			CompletableFuture.allOf(activeOperations.toArray(CompletableFuture[]::new))
					.get(5, TimeUnit.SECONDS);
		}
	}

	@Test
	void expiresPendingOperationsAfter500Milliseconds() throws Exception {
		try (BoundedAggregationOperationExecutor executor =
					new BoundedAggregationOperationExecutor(1, 1, 500)) {
			CountDownLatch active = new CountDownLatch(1);
			CountDownLatch release = new CountDownLatch(1);
			CompletableFuture<String> first = executor.submit(() -> await(active, release));
			assertThat(active.await(1, TimeUnit.SECONDS)).isTrue();

			long startedAt = System.nanoTime();
			CompletableFuture<String> pending = executor.submit(() -> "pending");
			assertThatThrownBy(() -> pending.get(2, TimeUnit.SECONDS))
					.hasCauseInstanceOf(TimeoutException.class);
			assertThat(Duration.ofNanos(System.nanoTime() - startedAt))
					.isGreaterThanOrEqualTo(Duration.ofMillis(450));

			release.countDown();
			assertThat(first.get(1, TimeUnit.SECONDS)).isEqualTo("active");
			assertThat(executor.submit(() -> "replacement").get(1, TimeUnit.SECONDS))
					.isEqualTo("replacement");
		}
	}

	@Test
	void cancelledPendingOperationsImmediatelyFreeCapacity() throws Exception {
		try (BoundedAggregationOperationExecutor executor =
					new BoundedAggregationOperationExecutor(1, 1, 5000)) {
			CountDownLatch active = new CountDownLatch(1);
			CountDownLatch release = new CountDownLatch(1);
			CompletableFuture<String> first = executor.submit(() -> await(active, release));
			assertThat(active.await(1, TimeUnit.SECONDS)).isTrue();
			CompletableFuture<String> cancelled = executor.submit(() -> "cancelled");

			assertThat(cancelled.cancel(true)).isTrue();
			CompletableFuture<String> replacement = executor.submit(() -> "replacement");
			release.countDown();

			assertThat(first.get(1, TimeUnit.SECONDS)).isEqualTo("active");
			assertThat(replacement.get(1, TimeUnit.SECONDS)).isEqualTo("replacement");
		}
	}

	@Test
	void cancelledActiveOperationsReleaseWorkerCapacity() throws Exception {
		try (BoundedAggregationOperationExecutor executor =
					new BoundedAggregationOperationExecutor(1, 1, 5000)) {
			CountDownLatch active = new CountDownLatch(1);
			CompletableFuture<String> first = executor.submit(() -> {
				active.countDown();
				try {
					new CountDownLatch(1).await();
					return "unexpected";
				} catch (InterruptedException exception) {
					Thread.currentThread().interrupt();
					return "cancelled";
				}
			});
			assertThat(active.await(1, TimeUnit.SECONDS)).isTrue();

			assertThat(first.cancel(true)).isTrue();

			assertThat(executor.submit(() -> "replacement").get(1, TimeUnit.SECONDS))
					.isEqualTo("replacement");
		}
	}

	private String await(CountDownLatch active, CountDownLatch release) {
		active.countDown();
		try {
			if (!release.await(5, TimeUnit.SECONDS)) {
				throw new IllegalStateException("operation release timed out");
			}
			return "active";
		} catch (InterruptedException exception) {
			Thread.currentThread().interrupt();
			throw new IllegalStateException(exception);
		}
	}
}
