package org.hellorealworld.aggregate;

import java.time.Duration;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

import org.eclipse.microprofile.config.inject.ConfigProperty;

import io.smallrye.mutiny.Uni;
import jakarta.annotation.PreDestroy;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;

@ApplicationScoped
class UpstreamRequestLimiter {

    private final int maxPending;
    private final Duration acquisitionTimeout;
    private final ScheduledExecutorService scheduler;
    private final Deque<PendingRequest> pending = new ArrayDeque<>();
    private int available;

    @Inject
    UpstreamRequestLimiter(
            @ConfigProperty(name = "aggregation.max-concurrent-upstream-requests") int maxConcurrent,
            @ConfigProperty(name = "aggregation.max-pending-upstream-requests") int maxPending,
            @ConfigProperty(name = "aggregation.http.connection-request-timeout-ms") long acquisitionTimeoutMs
    ) {
        if (maxConcurrent < 1 || maxPending < 0 || acquisitionTimeoutMs < 1) {
            throw new IllegalArgumentException("aggregation request limits are invalid");
        }
        available = maxConcurrent;
        this.maxPending = maxPending;
        acquisitionTimeout = Duration.ofMillis(acquisitionTimeoutMs);
        scheduler = Executors.newSingleThreadScheduledExecutor(
                Thread.ofPlatform().daemon().name("aggregation-acquire-timeout").factory()
        );
    }

    <T> Uni<T> execute(Supplier<Uni<T>> request) {
        AtomicReference<Permit> ownedPermit = new AtomicReference<>();
        return acquire(ownedPermit).onItem().transformToUni(permit -> {
            try {
                return request.get().eventually(permit::close);
            } catch (RuntimeException exception) {
                permit.close();
                return Uni.createFrom().failure(exception);
            }
        }).onCancellation().invoke(() -> {
            Permit permit = ownedPermit.getAndSet(null);
            if (permit != null) {
                permit.close();
            }
        });
    }

    private Uni<Permit> acquire(AtomicReference<Permit> ownedPermit) {
        PendingRequest waiting;
        synchronized (this) {
            if (available > 0) {
                available--;
                Permit permit = new Permit();
                ownedPermit.set(permit);
                return Uni.createFrom().item(permit);
            }
            if (pending.size() >= maxPending) {
                return Uni.createFrom().failure(
                        new RejectedExecutionException("aggregation pending request limit reached")
                );
            }
            waiting = new PendingRequest(ownedPermit);
            pending.addLast(waiting);
        }

        waiting.timeout = scheduler.schedule(
                () -> timeout(waiting),
                acquisitionTimeout.toMillis(),
                TimeUnit.MILLISECONDS
        );
        waiting.future.whenComplete((permit, failure) -> {
            if (waiting.future.isCancelled()) {
                cancel(waiting);
            }
        });
        return Uni.createFrom().completionStage(waiting.future);
    }

    private void cancel(PendingRequest waiting) {
        boolean removed;
        synchronized (this) {
            removed = pending.remove(waiting);
        }
        if (removed) {
            waiting.timeout.cancel(false);
        }
    }

    private void timeout(PendingRequest waiting) {
        boolean removed;
        synchronized (this) {
            removed = pending.remove(waiting);
        }
        if (removed) {
            waiting.future.completeExceptionally(
                    new TimeoutException("aggregation connection acquisition timed out")
            );
        }
    }

    private void release() {
        while (true) {
            PendingRequest waiting;
            synchronized (this) {
                waiting = pending.pollFirst();
                if (waiting == null) {
                    available++;
                    return;
                }
            }
            if (waiting.timeout != null) {
                waiting.timeout.cancel(false);
            }
            Permit permit = new Permit();
            waiting.ownedPermit.set(permit);
            if (waiting.future.complete(permit)) {
                return;
            }
            if (!waiting.ownedPermit.compareAndSet(permit, null)) {
                return;
            }
        }
    }

    @PreDestroy
    void close() {
        scheduler.shutdownNow();
    }

    private final class Permit implements AutoCloseable {
        private final AtomicBoolean closed = new AtomicBoolean();

        @Override
        public void close() {
            if (closed.compareAndSet(false, true)) {
                release();
            }
        }
    }

    private static final class PendingRequest {
        private final CompletableFuture<Permit> future = new CompletableFuture<>();
        private final AtomicReference<Permit> ownedPermit;
        private ScheduledFuture<?> timeout;

        private PendingRequest(AtomicReference<Permit> ownedPermit) {
            this.ownedPermit = ownedPermit;
        }
    }
}
