import signal

from app.services.runtime import build_celery_runtime_outbox_service


def main() -> int:
    service = build_celery_runtime_outbox_service()

    def stop_worker(signum, frame):
        service.shutdown(wait=False)

    signal.signal(signal.SIGINT, stop_worker)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_worker)
    try:
        service.run_forever()
    finally:
        service.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
