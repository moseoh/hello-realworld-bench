package org.hellorealworld.aggregate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Field;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;

import org.junit.jupiter.api.Test;

import io.smallrye.mutiny.Uni;

class UpstreamRequestLimiterTest {

    @Test
    void boundsOperationsAt128ActiveAnd128Pending() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(128, 128, 5000);
        try {
            List<CompletableFuture<String>> upstreamCalls = new ArrayList<>();
            List<CompletableFuture<String>> active = new ArrayList<>();
            for (int index = 0; index < 128; index++) {
                CompletableFuture<String> upstream = new CompletableFuture<>();
                upstreamCalls.add(upstream);
                active.add(limiter.execute(
                        () -> Uni.createFrom().completionStage(upstream)
                ).subscribeAsCompletionStage().toCompletableFuture());
            }

            AtomicInteger pendingStarted = new AtomicInteger();
            List<CompletableFuture<String>> pending = new ArrayList<>();
            for (int index = 0; index < 128; index++) {
                pending.add(limiter.execute(() -> {
                    pendingStarted.incrementAndGet();
                    return Uni.createFrom().item("pending");
                }).subscribeAsCompletionStage().toCompletableFuture());
            }

            CompletableFuture<String> overflow = limiter.execute(
                    () -> Uni.createFrom().item("overflow")
            ).subscribeAsCompletionStage().toCompletableFuture();
            ExecutionException failure = org.junit.jupiter.api.Assertions.assertThrows(
                    ExecutionException.class,
                    () -> overflow.get(1, TimeUnit.SECONDS)
            );
            assertInstanceOf(RejectedExecutionException.class, failure.getCause());
            assertEquals(0, pendingStarted.get());

            pending.forEach(operation -> operation.cancel(false));
            upstreamCalls.forEach(call -> call.complete("active"));
            CompletableFuture.allOf(active.toArray(CompletableFuture[]::new))
                    .get(5, TimeUnit.SECONDS);
        } finally {
            limiter.close();
        }
    }

    @Test
    void expiresPendingOperationsAfter500Milliseconds() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(1, 1, 500);
        try {
            CompletableFuture<String> upstream = new CompletableFuture<>();
            CompletableFuture<String> active = limiter.execute(
                    () -> Uni.createFrom().completionStage(upstream)
            ).subscribeAsCompletionStage().toCompletableFuture();

            long startedAt = System.nanoTime();
            CompletableFuture<String> pending = limiter.execute(
                    () -> Uni.createFrom().item("pending")
            ).subscribeAsCompletionStage().toCompletableFuture();
            ExecutionException failure = org.junit.jupiter.api.Assertions.assertThrows(
                    ExecutionException.class,
                    () -> pending.get(2, TimeUnit.SECONDS)
            );
            assertInstanceOf(TimeoutException.class, failure.getCause());
            assertTrue(Duration.ofNanos(System.nanoTime() - startedAt)
                    .compareTo(Duration.ofMillis(450)) >= 0);

            upstream.complete("active");
            assertEquals("active", active.get(1, TimeUnit.SECONDS));
            assertEquals(
                    "replacement",
                    limiter.execute(() -> Uni.createFrom().item("replacement"))
                            .await().atMost(Duration.ofSeconds(1))
            );
        } finally {
            limiter.close();
        }
    }

    @Test
    void cancelledWaitersDoNotConsumeCapacity() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(1, 1, 5000);
        try {
            for (int attempt = 0; attempt < 10; attempt++) {
                CompletableFuture<String> firstCall = new CompletableFuture<>();
                CompletableFuture<String> firstResult = limiter.execute(
                        () -> Uni.createFrom().completionStage(firstCall)
                ).subscribeAsCompletionStage().toCompletableFuture();
                CompletableFuture<String> secondResult = limiter.execute(
                        () -> Uni.createFrom().item("second")
                ).subscribeAsCompletionStage().toCompletableFuture();

                assertTrue(secondResult.cancel(false));
                firstCall.complete("first");
                assertEquals("first", firstResult.get(1, TimeUnit.SECONDS));

                CompletableFuture<String> thirdResult = limiter.execute(
                        () -> Uni.createFrom().item("third")
                ).subscribeAsCompletionStage().toCompletableFuture();
                assertEquals("third", thirdResult.get(1, TimeUnit.SECONDS));
            }
        } finally {
            limiter.close();
        }
    }

    @Test
    void cancelledWaitersImmediatelyFreePendingCapacity() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(1, 1, 5000);
        try {
            CompletableFuture<String> firstCall = new CompletableFuture<>();
            CompletableFuture<String> firstResult = limiter.execute(
                    () -> Uni.createFrom().completionStage(firstCall)
            ).subscribeAsCompletionStage().toCompletableFuture();
            CompletableFuture<String> cancelled = limiter.execute(
                    () -> Uni.createFrom().item("cancelled")
            ).subscribeAsCompletionStage().toCompletableFuture();

            assertTrue(cancelled.cancel(false));
            CompletableFuture<String> replacement = limiter.execute(
                    () -> Uni.createFrom().item("replacement")
            ).subscribeAsCompletionStage().toCompletableFuture();
            firstCall.complete("first");

            assertEquals("first", firstResult.get(1, TimeUnit.SECONDS));
            assertEquals("replacement", replacement.get(1, TimeUnit.SECONDS));
        } finally {
            limiter.close();
        }
    }

    @Test
    void cancellationWhileDequeuedRequestStartsDoesNotConsumeCapacity() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(1, 1, 5000);
        try {
            CompletableFuture<String> firstCall = new CompletableFuture<>();
            CompletableFuture<String> firstResult = limiter.execute(
                    () -> Uni.createFrom().completionStage(firstCall)
            ).subscribeAsCompletionStage().toCompletableFuture();
            CountDownLatch secondStarted = new CountDownLatch(1);
            CountDownLatch allowSecondToReturn = new CountDownLatch(1);
            CompletableFuture<String> secondResult = limiter.execute(() -> {
                secondStarted.countDown();
                try {
                    assertTrue(allowSecondToReturn.await(1, TimeUnit.SECONDS));
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException(exception);
                }
                return Uni.createFrom().item("second");
            }).subscribeAsCompletionStage().toCompletableFuture();

            Thread releaseFirst = Thread.ofPlatform().start(() -> firstCall.complete("first"));
            assertTrue(secondStarted.await(1, TimeUnit.SECONDS));
            assertTrue(secondResult.cancel(false));
            allowSecondToReturn.countDown();
            releaseFirst.join(TimeUnit.SECONDS.toMillis(1));
            assertEquals("first", firstResult.get(1, TimeUnit.SECONDS));

            CompletableFuture<String> thirdResult = limiter.execute(
                    () -> Uni.createFrom().item("third")
            ).subscribeAsCompletionStage().toCompletableFuture();
            assertEquals("third", thirdResult.get(1, TimeUnit.SECONDS));
        } finally {
            limiter.close();
        }
    }

    @Test
    void cancellationAfterPermitFutureCompletesDoesNotConsumeCapacity() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(1, 1, 5000);
        try {
            CompletableFuture<String> firstCall = new CompletableFuture<>();
            CompletableFuture<String> firstResult = limiter.execute(
                    () -> Uni.createFrom().completionStage(firstCall)
            ).subscribeAsCompletionStage().toCompletableFuture();
            CompletableFuture<String> secondResult = limiter.execute(
                    () -> Uni.createFrom().item("second")
            ).subscribeAsCompletionStage().toCompletableFuture();

            CompletableFuture<?> permitFuture = pendingFuture(limiter);
            CountDownLatch permitCompleted = new CountDownLatch(1);
            CountDownLatch allowDelivery = new CountDownLatch(1);
            permitFuture.whenComplete((permit, failure) -> {
                permitCompleted.countDown();
                try {
                    assertTrue(allowDelivery.await(1, TimeUnit.SECONDS));
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException(exception);
                }
            });

            Thread releaseFirst = Thread.ofPlatform().start(() -> firstCall.complete("first"));
            assertTrue(permitCompleted.await(1, TimeUnit.SECONDS));
            assertTrue(secondResult.cancel(false));
            allowDelivery.countDown();
            releaseFirst.join(TimeUnit.SECONDS.toMillis(1));
            assertEquals("first", firstResult.get(1, TimeUnit.SECONDS));

            CompletableFuture<String> thirdResult = limiter.execute(
                    () -> Uni.createFrom().item("third")
            ).subscribeAsCompletionStage().toCompletableFuture();
            assertEquals("third", thirdResult.get(1, TimeUnit.SECONDS));
        } finally {
            limiter.close();
        }
    }

    @Test
    void releasesPermitWhenItReturnsBeforeTimeoutHandleIsVisible() throws Exception {
        UpstreamRequestLimiter limiter = new UpstreamRequestLimiter(1, 1, 5000);
        try {
            CompletableFuture<String> firstCall = new CompletableFuture<>();
            CompletableFuture<String> firstResult = limiter.execute(
                    () -> Uni.createFrom().completionStage(firstCall)
            ).subscribeAsCompletionStage().toCompletableFuture();
            CompletableFuture<String> secondResult = limiter.execute(
                    () -> Uni.createFrom().item("second")
            ).subscribeAsCompletionStage().toCompletableFuture();

            clearPendingTimeoutHandle(limiter);
            firstCall.complete("first");

            assertEquals("first", firstResult.get(1, TimeUnit.SECONDS));
            assertEquals("second", secondResult.get(1, TimeUnit.SECONDS));
        } finally {
            limiter.close();
        }
    }

    private void clearPendingTimeoutHandle(UpstreamRequestLimiter limiter) throws Exception {
        Object waiting = firstPendingRequest(limiter);

        Field timeoutField = waiting.getClass().getDeclaredField("timeout");
        timeoutField.setAccessible(true);
        timeoutField.set(waiting, null);
    }

    private CompletableFuture<?> pendingFuture(UpstreamRequestLimiter limiter) throws Exception {
        Object waiting = firstPendingRequest(limiter);
        Field futureField = waiting.getClass().getDeclaredField("future");
        futureField.setAccessible(true);
        return (CompletableFuture<?>) futureField.get(waiting);
    }

    private Object firstPendingRequest(UpstreamRequestLimiter limiter) throws Exception {
        Field pendingField = UpstreamRequestLimiter.class.getDeclaredField("pending");
        pendingField.setAccessible(true);
        Deque<?> pending = (Deque<?>) pendingField.get(limiter);
        return pending.getFirst();
    }
}
