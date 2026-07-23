from threading import Event, Thread

from drastic_agent.agent.execution import ExecutionManager


def test_execution_admission_is_bounded():
    manager = ExecutionManager(max_workers=1, max_pending=1)
    release = Event()
    started = Event()

    def blocked():
        started.set()
        release.wait()

    first = manager.submit(blocked)
    assert started.wait(1)
    second = manager.submit(lambda: None)
    rejected = manager.submit(lambda: None)

    assert first is not None
    assert second is not None
    assert rejected is None
    release.set()
    manager.shutdown()


def test_resource_reservation_is_atomic_until_execution_finishes():
    manager = ExecutionManager(max_workers=2, max_pending=2)
    release = Event()
    started = Event()

    def blocked():
        started.set()
        release.wait()

    first = manager.submit(blocked, resources={("repository", 7), ("job", 3)})
    assert started.wait(1)
    conflicting_job = manager.submit(lambda: None, resources={("job", 3)})
    conflicting_repository = manager.submit(lambda: None, resources={("repository", 7)})

    assert first is not None
    assert conflicting_job is None
    assert conflicting_repository is None
    release.set()
    manager.shutdown()


def test_waiting_admitted_work_is_not_dropped_during_orderly_shutdown():
    manager = ExecutionManager(max_workers=1, max_pending=1)
    release = Event()
    started = Event()
    pending_ran = Event()

    def blocked():
        started.set()
        release.wait()

    manager.submit(blocked)
    assert started.wait(1)
    manager.submit(pending_ran.set)

    shutdown = Thread(target=manager.shutdown)
    shutdown.start()
    release.set()
    shutdown.join(1)

    assert pending_ran.is_set()
    assert not shutdown.is_alive()


def test_queued_future_is_registered_by_operation_uuid_and_can_be_cancelled():
    manager = ExecutionManager(max_workers=1, max_pending=1)
    release = Event()
    started = Event()
    queued_ran = Event()

    def blocked():
        started.set()
        release.wait()

    manager.submit(blocked)
    assert started.wait(1)
    queued = manager.submit(queued_ran.set, operation_uuid="queued-operation")

    assert queued is not None
    assert manager.get_operation("queued-operation") is queued
    assert manager.cancel("queued-operation") is True
    assert queued.cancelled()
    assert manager.get_operation("queued-operation") is None

    release.set()
    manager.shutdown()
    assert not queued_ran.is_set()
