from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from drastic_agent.config import DefaultConfig, env_int


class ExecutionManager:
    """Bounded executor with atomic resource admission and cancellation lookup."""

    def __init__(self, max_workers=None, max_pending=None):
        self.max_workers = max_workers or env_int(
            "DRASTIC_AGENT_MAX_WORKERS", DefaultConfig.AGENT_MAX_WORKERS
        )
        self.max_pending = max_pending or env_int(
            "DRASTIC_AGENT_MAX_PENDING", DefaultConfig.AGENT_MAX_PENDING
        )
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="agent")
        self._lock = Lock()
        self._admitted = 0
        self._resources = set()
        self._operations = {}
        self._shutdown = False

    def submit(self, function, *args, resources=(), operation_uuid=None, **kwargs):
        resources = {resource for resource in resources if resource is not None}
        with self._lock:
            if self._shutdown or self._admitted >= self.max_workers + self.max_pending:
                return None
            if resources & self._resources:
                return None
            self._admitted += 1
            self._resources.update(resources)

        try:
            future = self._executor.submit(function, *args, **kwargs)
        except RuntimeError:
            with self._lock:
                self._admitted -= 1
                self._resources.difference_update(resources)
            return None

        with self._lock:
            if operation_uuid:
                self._operations[operation_uuid] = future

        def release(completed):
            with self._lock:
                self._admitted -= 1
                self._resources.difference_update(resources)
                if operation_uuid and self._operations.get(operation_uuid) is completed:
                    self._operations.pop(operation_uuid, None)

        future.add_done_callback(release)
        return future

    def register_operation(self, operation):
        with self._lock:
            self._operations[operation.uuid] = operation

    def unregister_operation(self, operation_uuid):
        with self._lock:
            self._operations.pop(operation_uuid, None)

    def get_operation(self, operation_uuid):
        with self._lock:
            return self._operations.get(operation_uuid)

    def cancel(self, operation_uuid):
        with self._lock:
            operation = self._operations.get(operation_uuid)
        return isinstance(operation, Future) and operation.cancel()

    def shutdown(self, wait=True):
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        # Admitted work must enter its operation code so it can emit a terminal
        # report after the agent cancellation event has been set.
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
