"""Lifecycle façade used by the UI and CLI: start/stop the worker, run calibration."""

from __future__ import annotations

from collections.abc import Callable

from taprivo.config import Config
from taprivo.core.energy import EnergyEngine
from taprivo.core.events import now_monotonic_ms
from taprivo.core.state import TrackingStatus
from taprivo.vision.calibration import CalibrationResult, CalibrationSession
from taprivo.vision.camera import CameraError, CameraSource
from taprivo.vision.frames import FrameStats
from taprivo.vision.squeeze import Levels, SqueezeDetector, SqueezeParams
from taprivo.vision.tracker import HandTracker, MediaPipeHandTracker, ModelError
from taprivo.vision.worker import PreviewCallback, TapsCallback, VisionWorker

SourceFactory = Callable[[int, Config, Callable[[], int]], CameraSource]
TrackerFactory = Callable[[], HandTracker]


def default_source_factory(index: int, config: Config, now_ms: Callable[[], int]) -> CameraSource:
    return CameraSource(
        index, width=config.camera.width, height=config.camera.height, now_ms=now_ms
    )


class VisionController:
    def __init__(
        self,
        engine: EnergyEngine,
        config: Config,
        *,
        source_factory: SourceFactory | None = None,
        tracker_factory: TrackerFactory | None = None,
        now_ms: Callable[[], int] = now_monotonic_ms,
    ) -> None:
        self._engine = engine
        self._config = config
        self._source_factory = source_factory or default_source_factory
        self._tracker_factory = tracker_factory or MediaPipeHandTracker
        self._now_ms = now_ms
        self._params = SqueezeParams.from_config(config.squeeze)
        self._detector = SqueezeDetector(self._params, lambda: engine.session_id)
        self._worker: VisionWorker | None = None
        self.device_index: int | None = None

    # -- state ---------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def detector(self) -> SqueezeDetector:
        return self._detector

    @property
    def error(self) -> str | None:
        return self._worker.error if self._worker is not None else None

    @property
    def calibration(self) -> CalibrationSession | None:
        return self._worker.calibration if self._worker is not None else None

    def stats(self) -> FrameStats:
        if self._worker is None:
            return FrameStats(0.0, 0.0, 0.0, 0)
        return self._worker.stats()

    def tracking_status(self) -> TrackingStatus:
        return self._engine.snapshot().tracking

    def now_ms(self) -> int:
        return self._now_ms()

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        device_index: int,
        preview: PreviewCallback | None = None,
        taps: TapsCallback | None = None,
    ) -> None:
        if self.running:
            return
        try:
            # fail fast (model checksum, import, GPU/OpenGL context) before the camera
            probe = self._tracker_factory()
            probe.close()
        except Exception as exc:
            raise ModelError(f"hand tracker could not start: {exc}") from exc
        worker = VisionWorker(
            self._engine,
            self._detector,
            lambda: self._source_factory(device_index, self._config, self._now_ms),
            self._tracker_factory,
            preview=preview,
            preview_fps=self._config.camera.preview_fps,
            taps=taps,
            now_ms=self._now_ms,
        )
        worker.start()
        worker.wait_started(10.0)
        if worker.error is not None:
            worker.join(2.0)
            self._worker = None
            raise CameraError(worker.error)
        self._worker = worker
        self.device_index = device_index

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.set_calibration(None)
            self._worker.stop()
            self._worker = None
        self.device_index = None

    # -- calibration ---------------------------------------------------------

    def begin_calibration(self) -> CalibrationSession:
        if self._worker is None or not self.running:
            raise RuntimeError("start the camera before calibrating")
        session = CalibrationSession(self._params, lambda: self._engine.session_id)
        session.start(self._now_ms())
        self._worker.set_calibration(session)
        return session

    def apply_calibration(self, result: CalibrationResult) -> None:
        if self.running and self._worker is not None:
            self._worker.apply_levels(result.levels)
            self._worker.set_calibration(None)
        else:
            self._detector.set_levels(result.levels)

    def levels(self) -> Levels:
        if self.running and self._worker is not None:
            return self._worker.levels()
        return self._detector.levels()

    def reset_levels(self) -> None:
        defaults = Levels(self._params.open_level, self._params.closed_level)
        if self.running and self._worker is not None:
            self._worker.apply_levels(defaults)
        else:
            self._detector.set_levels(defaults)
