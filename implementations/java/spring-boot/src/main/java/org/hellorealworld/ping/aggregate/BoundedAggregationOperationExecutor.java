package org.hellorealworld.ping.aggregate;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Supplier;

final class BoundedAggregationOperationExecutor implements AggregationOperationExecutor, AutoCloseable {

	private final int maxPending;
	private final long acquisitionTimeoutMs;
	private final ExecutorService workers;
	private final ScheduledExecutorService scheduler;
	private final Deque<Operation<?>> pending = new ArrayDeque<>();
	private final Set<Operation<?>> active = new HashSet<>();
	private int available;
	private boolean closed;

	BoundedAggregationOperationExecutor(int maxConcurrent, int maxPending, long acquisitionTimeoutMs) {
		this(
				maxConcurrent,
				maxPending,
				acquisitionTimeoutMs,
				Thread.ofPlatform().name("aggregation-", 0).factory()
		);
	}

	BoundedAggregationOperationExecutor(
			int maxConcurrent,
			int maxPending,
			long acquisitionTimeoutMs,
			ThreadFactory threadFactory
	) {
		if (maxConcurrent < 1 || maxPending < 0 || acquisitionTimeoutMs < 1) {
			throw new IllegalArgumentException("aggregation executor limits are invalid");
		}
		available = maxConcurrent;
		this.maxPending = maxPending;
		this.acquisitionTimeoutMs = acquisitionTimeoutMs;
		workers = Executors.newFixedThreadPool(maxConcurrent, threadFactory);
		scheduler = Executors.newSingleThreadScheduledExecutor(
				Thread.ofPlatform().daemon().name("aggregation-acquire-timeout").factory()
		);
	}

	@Override
	public <T> CompletableFuture<T> submit(Supplier<T> supplier) {
		Operation<T> operation = new Operation<>(supplier);
		boolean start;
		synchronized (this) {
			if (closed) {
				operation.reject(new RejectedExecutionException("aggregation executor closed"));
				return operation.result;
			}
			if (available > 0) {
				available--;
				operation.state = State.ACTIVE;
				active.add(operation);
				start = true;
			} else if (pending.size() < maxPending) {
				pending.addLast(operation);
				operation.timeout = scheduler.schedule(
						operation::timeout,
						acquisitionTimeoutMs,
						TimeUnit.MILLISECONDS
				);
				start = false;
			} else {
				operation.reject(new RejectedExecutionException(
						"aggregation pending request limit reached"
				));
				return operation.result;
			}
		}
		if (start) {
			start(operation);
		}
		return operation.result;
	}

	private void start(Operation<?> operation) {
		try {
			workers.execute(operation);
		} catch (RejectedExecutionException failure) {
			operation.failBeforeStart(failure);
		}
	}

	private void release() {
		Operation<?> next;
		synchronized (this) {
			if (closed || pending.isEmpty()) {
				available++;
				return;
			}
			next = pending.removeFirst();
			next.cancelTimeout();
			next.state = State.ACTIVE;
			active.add(next);
		}
		start(next);
	}

	@Override
	public void close() {
		List<Operation<?>> waiting;
		synchronized (this) {
			if (closed) {
				return;
			}
			closed = true;
			waiting = new ArrayList<>(pending.size() + active.size());
			waiting.addAll(pending);
			waiting.addAll(active);
			pending.clear();
			active.clear();
			for (Operation<?> operation : waiting) {
				operation.cancelTimeout();
				operation.state = State.COMPLETED;
			}
		}
		RejectedExecutionException failure =
				new RejectedExecutionException("aggregation executor closed");
		waiting.forEach(operation -> operation.result.completeExceptionally(failure));
		workers.shutdownNow();
		scheduler.shutdownNow();
	}

	private enum State {
		PENDING,
		ACTIVE,
		CANCELLED_ACTIVE,
		COMPLETED
	}

	private final class Operation<T> implements Runnable {
		private final Supplier<T> supplier;
		private final OperationFuture result = new OperationFuture();
		private State state = State.PENDING;
		private ScheduledFuture<?> timeout;
		private Thread worker;

		private Operation(Supplier<T> supplier) {
			this.supplier = supplier;
		}

		@Override
		public void run() {
			boolean cancelledBeforeStart;
			synchronized (BoundedAggregationOperationExecutor.this) {
				cancelledBeforeStart = state == State.CANCELLED_ACTIVE;
				if (cancelledBeforeStart) {
					state = State.COMPLETED;
					active.remove(this);
				} else if (state != State.ACTIVE) {
					return;
				} else {
					worker = Thread.currentThread();
				}
			}
			if (cancelledBeforeStart) {
				release();
				return;
			}

			T value = null;
			Throwable failure = null;
			try {
				value = supplier.get();
			} catch (Throwable exception) {
				failure = exception;
			} finally {
				Thread.interrupted();
			}

			boolean publish;
			synchronized (BoundedAggregationOperationExecutor.this) {
				worker = null;
				publish = state == State.ACTIVE;
				state = State.COMPLETED;
				active.remove(this);
			}
			if (publish) {
				if (failure == null) {
					result.complete(value);
				} else {
					result.completeExceptionally(failure);
				}
			}
			release();
		}

		private void timeout() {
			synchronized (BoundedAggregationOperationExecutor.this) {
				if (state != State.PENDING || !pending.remove(this)) {
					return;
				}
				state = State.COMPLETED;
				timeout = null;
			}
			result.completeExceptionally(
					new TimeoutException("aggregation operation acquisition timed out")
			);
		}

		private void failBeforeStart(RejectedExecutionException failure) {
			boolean releaseSlot;
			synchronized (BoundedAggregationOperationExecutor.this) {
				releaseSlot = state == State.ACTIVE || state == State.CANCELLED_ACTIVE;
				if (state == State.COMPLETED) {
					return;
				}
				state = State.COMPLETED;
				active.remove(this);
			}
			result.completeExceptionally(failure);
			if (releaseSlot) {
				release();
			}
		}

		private void reject(RejectedExecutionException failure) {
			state = State.COMPLETED;
			result.completeExceptionally(failure);
		}

		private boolean cancel(boolean mayInterruptIfRunning) {
			Thread running = null;
			synchronized (BoundedAggregationOperationExecutor.this) {
				if (state == State.COMPLETED || state == State.CANCELLED_ACTIVE) {
					return false;
				}
				if (state == State.PENDING) {
					pending.remove(this);
					cancelTimeout();
					state = State.COMPLETED;
				} else {
					state = State.CANCELLED_ACTIVE;
					if (mayInterruptIfRunning) {
						running = worker;
					}
				}
			}
			boolean cancelled = result.cancelFromOperation(mayInterruptIfRunning);
			if (running != null) {
				running.interrupt();
			}
			return cancelled;
		}

		private void cancelTimeout() {
			if (timeout != null) {
				timeout.cancel(false);
				timeout = null;
			}
		}

		private final class OperationFuture extends CompletableFuture<T> {
			@Override
			public boolean cancel(boolean mayInterruptIfRunning) {
				return Operation.this.cancel(mayInterruptIfRunning);
			}

			private boolean cancelFromOperation(boolean mayInterruptIfRunning) {
				return super.cancel(mayInterruptIfRunning);
			}
		}
	}
}
