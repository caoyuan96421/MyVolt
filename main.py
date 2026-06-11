from __future__ import annotations

import json
import math
from pathlib import Path
import queue
import re
import sys
import time

from PySide6.QtCore import QLineF, QRectF, QThread, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSlider,
    QStatusBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices
    from PySide6.QtMultimediaWidgets import QVideoWidget
except ImportError:
    QCamera = None
    QMediaCaptureSession = None
    QMediaDevices = None
    QVideoWidget = None

import serial
from serial.tools import list_ports

from myvolt_protocol import (
    FIXED_BAUD_RATE,
    LINE_TERMINATOR,
    frame_command,
    is_error_line,
    parse_homed_status,
    parse_maximum_z_position,
    parse_position,
    parse_probe_measurement,
    parse_temperature,
    parse_tool_status,
)


STAGE_X_MAX_MM = 128.0
STAGE_Y_MAX_MM = 157.0
STAGE_CALIBRATION_Y_MAX_MM = 40.0
USER_STAGE_Y_MIN_MM = STAGE_CALIBRATION_Y_MAX_MM
DEFAULT_WORK_AREA_SIZE_MM = 50.0
DEFAULT_WORK_AREA = (
    (STAGE_X_MAX_MM - DEFAULT_WORK_AREA_SIZE_MM) / 2,
    USER_STAGE_Y_MIN_MM
    + (STAGE_Y_MAX_MM - USER_STAGE_Y_MIN_MM - DEFAULT_WORK_AREA_SIZE_MM) / 2,
    DEFAULT_WORK_AREA_SIZE_MM,
    DEFAULT_WORK_AREA_SIZE_MM,
)
MIN_WORK_AREA_SIZE_MM = 5.0
STAGE_VIEW_MARGIN_PX = 18.0
HEIGHT_COLORBAR_GAP_PX = 10.0
HEIGHT_COLORBAR_WIDTH_PX = 14.0
HEIGHT_COLORBAR_LABEL_WIDTH_PX = 46.0
STAGE_ZOOM_MIN = 1.0
STAGE_ZOOM_MAX = 8.0
STAGE_ZOOM_STEP = 1.2
MIN_PRINT_CIRCLE_RADIUS_MM = 1.0
CIRCLE_PRINT_SEGMENT_MM = 1.0
SERIAL_COMMAND_INTERVAL_S = 0.01
MAXIMUM_Z_RETURN_MARGIN_MM = 0.5
CAMERA_CONFIG_PATH = Path("camera.json")
CAMERA_EXPOSURE_MIN_EV = -4.0
CAMERA_EXPOSURE_MAX_EV = 4.0
CAMERA_EXPOSURE_SCALE = 10
Z_SWITCH_X_MM = 4.820494
Z_SWITCH_Y_MM = 7.966725
Z_SWITCH_REGION_DIAMETER_MM = 8.0
STAGE_PROBE_REGIONS = (
    ("Z probe", Z_SWITCH_X_MM, Z_SWITCH_Y_MM, Z_SWITCH_REGION_DIAMETER_MM),
    ("XY probe", 34.270011, 5.686648, 15.0),
)
MEASUREMENT_RE = re.compile(
    r"Measurement:\s+(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+))"
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class SerialThread(QThread):
    connected = Signal(str, int)
    disconnected = Signal()
    received_line = Signal(str)
    sent_line = Signal(str)
    error_line = Signal(str)
    status_message = Signal(str)

    def __init__(
        self,
        port: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._port = port
        self._commands: queue.PriorityQueue[tuple[int, int, str]] = queue.PriorityQueue()
        self._queue_index = 0
        self._stop_requested = False
        self._sequence = 1
        self._serial: serial.Serial | None = None
        self._awaiting_response = False
        self._last_write_time = 0.0

    def enqueue(self, payload: str, urgent: bool = False) -> None:
        payload = payload.strip()
        if not payload:
            return
        priority = 0 if urgent else 1
        self._queue_index += 1
        self._commands.put((priority, self._queue_index, payload))

    def clear_pending(self) -> None:
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break
        self._awaiting_response = False
        self._last_write_time = 0.0

    def has_pending(self) -> bool:
        return not self._commands.empty()

    def is_idle(self) -> bool:
        return self._commands.empty() and not self._awaiting_response

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            ser = self._open_serial()
            self._serial = ser
            self.connected.emit(self._port, FIXED_BAUD_RATE)
            self._read_write_loop(ser)
        except Exception as exc:
            self.error_line.emit(f"connection error: {exc}")
        finally:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None
            self.disconnected.emit()

    def _open_serial(self) -> serial.Serial:
        ser = serial.Serial(
            port=self._port,
            baudrate=FIXED_BAUD_RATE,
            timeout=0.05,
            write_timeout=0.5,
        )
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except Exception:
            pass
        return ser

    def _read_write_loop(self, ser: serial.Serial) -> None:
        while not self._stop_requested:
            self._drain_command_queue(ser)
            line = self._read_line(ser)
            if line:
                self._emit_received(line)

    def _drain_command_queue(self, ser: serial.Serial) -> None:
        if self._awaiting_response:
            return
        if time.monotonic() - self._last_write_time < SERIAL_COMMAND_INTERVAL_S:
            return

        try:
            _, _, payload = self._commands.get_nowait()
        except queue.Empty:
            return

        frame = frame_command(self._sequence, payload)
        self._sequence += 1
        self._awaiting_response = True
        self._last_write_time = time.monotonic()
        self._write_frame(ser, frame)

    def _write_frame(self, ser: serial.Serial, frame: str) -> None:
        ser.write(frame.encode("ascii") + LINE_TERMINATOR)
        ser.flush()
        self.sent_line.emit(frame)

    def _read_line(self, ser: serial.Serial) -> str:
        data = ser.readline()
        if not data:
            return ""
        return data.decode("utf-8", errors="replace").strip()

    def _emit_received(self, line: str) -> None:
        self._update_command_ack_state(line)
        self.received_line.emit(line)
        if is_error_line(line):
            self.error_line.emit(line)

    def _update_command_ack_state(self, line: str) -> None:
        text = line.strip()
        if (
            text == "ok"
            or is_error_line(text)
            or "Missing characters detected" in text
        ):
            self._awaiting_response = False


class StageView(QWidget):
    stage_clicked = Signal(float, float)
    work_area_changed = Signal(float, float, float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._x: float | None = None
        self._y: float | None = None
        self._z: float | None = None
        self._homed = False
        self._work_area = DEFAULT_WORK_AREA
        self._height_map_points: list[tuple[float, float, float]] = []
        self._print_circle: tuple[float, float, float] | None = None
        self._zoom = STAGE_ZOOM_MIN
        self._view_center_x = STAGE_X_MAX_MM / 2.0
        self._view_center_y = STAGE_Y_MAX_MM / 2.0
        self._drag_edges: set[str] = set()
        self._drag_start_stage: tuple[float, float] | None = None
        self._drag_start_area: tuple[float, float, float, float] | None = None
        self._drag_moved = False
        self._circle_drag_mode: str | None = None
        self._circle_drag_start_stage: tuple[float, float] | None = None
        self._circle_drag_start: tuple[float, float, float] | None = None
        self._work_area_edit_enabled = True
        self._print_circle_edit_enabled = False
        self._motion_enabled = False
        self._disabled_by_not_homed = False
        self._cross_cursor = self._build_cross_cursor()
        self.setMinimumSize(220, 260)
        self.setMouseTracking(True)
        self._update_cursor()

    def set_position(
        self,
        x: float | None,
        y: float | None,
        z: float | None,
        homed: bool,
    ) -> None:
        self._x = x
        self._y = y
        self._z = z
        self._homed = homed
        self.update()

    def set_homed(self, homed: bool) -> None:
        self._homed = homed
        self.update()

    def set_work_area(self, x: float, y: float, width: float, height: float) -> None:
        self._work_area = self._clamp_work_area(x, y, width, height)
        self._constrain_print_circle()
        self.update()

    def work_area(self) -> tuple[float, float, float, float]:
        return self._work_area

    def set_work_area_edit_enabled(self, enabled: bool) -> None:
        self._work_area_edit_enabled = enabled
        self._drag_edges = set()
        self._drag_start_stage = None
        self._drag_start_area = None
        self._drag_moved = False
        self._update_cursor()

    def set_print_circle_editing(self, enabled: bool) -> None:
        self._print_circle_edit_enabled = enabled
        self.set_work_area_edit_enabled(not enabled)
        if enabled and self._print_circle is None:
            self._print_circle = self._default_print_circle()
        self._circle_drag_mode = None
        self._circle_drag_start_stage = None
        self._circle_drag_start = None
        self._constrain_print_circle()
        self._update_cursor()
        self.update()

    def print_circle(self) -> tuple[float, float, float] | None:
        return self._print_circle

    def clear_print_circle(self) -> None:
        self._print_circle = None
        self._print_circle_edit_enabled = False
        self.set_work_area_edit_enabled(True)
        self._circle_drag_mode = None
        self._circle_drag_start_stage = None
        self._circle_drag_start = None
        self._update_cursor()
        self.update()

    def set_height_map_points(
        self, points: list[tuple[float, float, float]]
    ) -> None:
        self._height_map_points = list(points)
        self.update()

    def set_motion_enabled(
        self, enabled: bool, disabled_by_not_homed: bool = False
    ) -> None:
        self._motion_enabled = enabled
        self._disabled_by_not_homed = disabled_by_not_homed
        self._update_cursor()

    def clear_position(self) -> None:
        self._x = None
        self._y = None
        self._z = None
        self._homed = False
        self._height_map_points = []
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        viewport = self._stage_viewport_rect()
        bounds = self._stage_rect()
        position = event.position()
        if not viewport.contains(position) or not bounds.contains(position):
            event.ignore()
            return

        if self._print_circle_edit_enabled:
            self._handle_print_circle_press(position.x(), position.y(), bounds)
            event.accept()
            return

        hit = set()
        if self._work_area_edit_enabled:
            hit = self._work_area_hit(position.x(), position.y(), bounds)
        if hit:
            self._drag_edges = hit
            self._drag_start_stage = self._map_widget_point(
                position.x(), position.y(), bounds
            )
            self._drag_start_area = self._work_area
            self._drag_moved = False
            self._set_cursor_for_hit(hit)
            event.accept()
            return

        if not self._motion_enabled:
            event.ignore()
            return

        x, y = self._map_widget_point(position.x(), position.y(), bounds)
        self.stage_clicked.emit(x, y)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        viewport = self._stage_viewport_rect()
        bounds = self._stage_rect()
        position = event.position()

        if self._circle_drag_mode is not None:
            x, y = self._map_widget_point(position.x(), position.y(), bounds)
            self._update_print_circle_drag(x, y)
            event.accept()
            return

        if self._drag_edges and self._drag_start_stage and self._drag_start_area:
            x, y = self._map_widget_point(position.x(), position.y(), bounds)
            self._update_work_area_drag(x, y)
            event.accept()
            return

        hit = set()
        if viewport.contains(position) and bounds.contains(position):
            if self._print_circle_edit_enabled:
                hit = self._print_circle_hit(position.x(), position.y(), bounds)
            elif self._work_area_edit_enabled:
                hit = self._work_area_hit(position.x(), position.y(), bounds)
        if self._print_circle_edit_enabled:
            self._set_cursor_for_circle_hit(hit)
        else:
            self._set_cursor_for_hit(hit)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._circle_drag_mode:
            self._circle_drag_mode = None
            self._circle_drag_start_stage = None
            self._circle_drag_start = None
            self._update_cursor()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and self._drag_edges:
            click_stage_point = self._map_widget_point(
                event.position().x(), event.position().y(), self._stage_rect()
            )
            should_move_tool = not self._drag_moved and self._motion_enabled
            self._drag_edges = set()
            self._drag_start_stage = None
            self._drag_start_area = None
            self._drag_moved = False
            if should_move_tool:
                self.stage_clicked.emit(*click_stage_point)
            else:
                self.work_area_changed.emit(*self._work_area)
            self._update_cursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        old_bounds = self._stage_rect()
        viewport = self._stage_viewport_rect()
        position = event.position()
        if viewport.contains(position) and old_bounds.contains(position):
            anchor_x, anchor_y = self._map_widget_point(
                position.x(),
                position.y(),
                old_bounds,
            )
            zoom_x = position.x()
            zoom_y = position.y()
        else:
            anchor_x = self._view_center_x
            anchor_y = self._view_center_y
            zoom_x = viewport.center().x()
            zoom_y = viewport.center().y()

        factor = STAGE_ZOOM_STEP if delta > 0 else 1.0 / STAGE_ZOOM_STEP
        new_zoom = clamp(self._zoom * factor, STAGE_ZOOM_MIN, STAGE_ZOOM_MAX)
        if abs(new_zoom - self._zoom) <= 1e-9:
            event.accept()
            return

        self._zoom = new_zoom
        new_scale = self._stage_scale()
        viewport_center = viewport.center()
        self._view_center_x = anchor_x + (zoom_x - viewport_center.x()) / new_scale
        self._view_center_y = anchor_y - (zoom_y - viewport_center.y()) / new_scale
        self._clamp_view_center()
        self.update()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._clamp_view_center()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9fb"))

        viewport = self._stage_viewport_rect()
        bounds = self._stage_rect()
        painter.save()
        painter.setClipRect(viewport)
        self._draw_calibration_area(painter, bounds)
        self._draw_grid(painter, bounds)
        self._draw_work_area(painter, bounds)
        self._draw_height_map_points(painter, bounds)
        self._draw_print_circle(painter, bounds)
        self._draw_probe_regions(painter, bounds)
        self._draw_boundary(painter, bounds)
        self._draw_position_cross(painter, bounds)
        painter.restore()
        self._draw_height_colorbar(painter, bounds)

    def _stage_viewport_rect(self) -> QRectF:
        colorbar_slot = (
            HEIGHT_COLORBAR_GAP_PX
            + HEIGHT_COLORBAR_WIDTH_PX
            + HEIGHT_COLORBAR_LABEL_WIDTH_PX
        )
        width = max(1.0, self.width() - STAGE_VIEW_MARGIN_PX * 2 - colorbar_slot)
        height = max(1.0, self.height() - STAGE_VIEW_MARGIN_PX * 2)
        return QRectF(STAGE_VIEW_MARGIN_PX, STAGE_VIEW_MARGIN_PX, width, height)

    def _stage_scale(self) -> float:
        viewport = self._stage_viewport_rect()
        fit_scale = min(
            viewport.width() / STAGE_X_MAX_MM,
            viewport.height() / STAGE_Y_MAX_MM,
        )
        return fit_scale * self._zoom

    def _stage_rect(self) -> QRectF:
        viewport = self._stage_viewport_rect()
        scale = self._stage_scale()
        viewport_center = viewport.center()
        stage_width = STAGE_X_MAX_MM * scale
        stage_height = STAGE_Y_MAX_MM * scale
        left = viewport_center.x() - (STAGE_X_MAX_MM - self._view_center_x) * scale
        top = viewport_center.y() - self._view_center_y * scale
        return QRectF(left, top, stage_width, stage_height)

    def _clamp_view_center(self) -> None:
        viewport = self._stage_viewport_rect()
        scale = self._stage_scale()
        visible_width = viewport.width() / scale
        visible_height = viewport.height() / scale
        if visible_width >= STAGE_X_MAX_MM:
            self._view_center_x = STAGE_X_MAX_MM / 2.0
        else:
            half_width = visible_width / 2.0
            self._view_center_x = clamp(
                self._view_center_x,
                half_width,
                STAGE_X_MAX_MM - half_width,
            )
        if visible_height >= STAGE_Y_MAX_MM:
            self._view_center_y = STAGE_Y_MAX_MM / 2.0
        else:
            half_height = visible_height / 2.0
            self._view_center_y = clamp(
                self._view_center_y,
                half_height,
                STAGE_Y_MAX_MM - half_height,
            )

    def _draw_calibration_area(self, painter: QPainter, bounds: QRectF) -> None:
        _, bottom = self._map_stage_point(0.0, STAGE_CALIBRATION_Y_MAX_MM, bounds)
        calibration_rect = QRectF(
            bounds.left(),
            bounds.top(),
            bounds.width(),
            bottom - bounds.top(),
        )
        painter.fillRect(calibration_rect, QColor("#e6e8eb"))

    def _draw_grid(self, painter: QPainter, bounds: QRectF) -> None:
        grid_pen = QPen(QColor("#dfe4ea"), 1)
        grid_pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)

        for x in (0.0, 32.0, 64.0, 96.0, STAGE_X_MAX_MM):
            px, _ = self._map_stage_point(x, 0.0, bounds)
            painter.drawLine(QLineF(px, bounds.top(), px, bounds.bottom()))

        for y in (0.0, 39.25, 78.5, 117.75, STAGE_Y_MAX_MM):
            _, py = self._map_stage_point(0.0, y, bounds)
            painter.drawLine(QLineF(bounds.left(), py, bounds.right(), py))

    def _draw_boundary(self, painter: QPainter, bounds: QRectF) -> None:
        painter.setPen(QPen(QColor("#68707a"), 2))
        painter.drawRect(bounds)

    def _draw_work_area(self, painter: QPainter, bounds: QRectF) -> None:
        work_rect = self._work_area_widget_rect(bounds)
        painter.fillRect(work_rect, QColor(31, 115, 183, 35))
        pen_color = (
            QColor("#7a8793")
            if not self._work_area_edit_enabled
            else QColor("#1f73b7")
        )
        painter.setPen(QPen(pen_color, 2))
        painter.drawRect(work_rect)

    def _draw_print_circle(self, painter: QPainter, bounds: QRectF) -> None:
        if self._print_circle is None:
            return

        center_x, center_y, radius = self._print_circle
        px, py = self._map_stage_point(center_x, center_y, bounds)
        scale = bounds.width() / STAGE_X_MAX_MM
        pixel_radius = radius * scale
        circle_rect = QRectF(
            px - pixel_radius,
            py - pixel_radius,
            pixel_radius * 2,
            pixel_radius * 2,
        )
        color = (
            QColor("#8e24aa")
            if self._print_circle_edit_enabled
            else QColor("#5e35b1")
        )
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.setPen(QPen(color, 2))
        painter.drawEllipse(circle_rect)
        painter.drawLine(QLineF(px - 6.0, py, px + 6.0, py))
        painter.drawLine(QLineF(px, py - 6.0, px, py + 6.0))

    def _draw_height_map_points(self, painter: QPainter, bounds: QRectF) -> None:
        if not self._height_map_points:
            return

        min_height, max_height = self._height_bounds()
        radius = 4.5
        for x, y, height in self._height_map_points:
            color = self._height_map_color(height, min_height, max_height)
            px, py = self._map_stage_point(x, y, bounds)
            painter.setPen(QPen(QColor("#263238"), 1))
            painter.setBrush(color)
            painter.drawEllipse(
                QRectF(px - radius, py - radius, radius * 2, radius * 2)
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_height_colorbar(self, painter: QPainter, bounds: QRectF) -> None:
        del bounds
        if not self._height_map_points:
            return

        min_height, max_height = self._height_bounds()
        viewport = self._stage_viewport_rect()
        bar_left = viewport.right() + HEIGHT_COLORBAR_GAP_PX
        bar_rect = QRectF(
            bar_left,
            viewport.top(),
            HEIGHT_COLORBAR_WIDTH_PX,
            viewport.height(),
        )
        label_left = bar_rect.right() + 4.0
        label_rect = QRectF(
            label_left,
            viewport.top(),
            HEIGHT_COLORBAR_LABEL_WIDTH_PX - 4.0,
            viewport.height(),
        )

        gradient = QLinearGradient(
            bar_rect.left(),
            bar_rect.bottom(),
            bar_rect.left(),
            bar_rect.top(),
        )
        gradient.setColorAt(0.0, QColor("#2f6fc0"))
        gradient.setColorAt(0.5, QColor("#f4e65c"))
        gradient.setColorAt(1.0, QColor("#c94035"))
        painter.fillRect(bar_rect, QBrush(gradient))
        painter.setPen(QPen(QColor("#263238"), 1))
        painter.drawRect(bar_rect)

        painter.setPen(QPen(QColor("#263238"), 1))
        painter.drawText(
            QRectF(label_rect.left(), label_rect.top() - 2.0, label_rect.width(), 18.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            f"{max_height:.3f}",
        )
        painter.drawText(
            QRectF(
                label_rect.left(),
                label_rect.bottom() - 16.0,
                label_rect.width(),
                18.0,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            f"{min_height:.3f}",
        )

    def _height_bounds(self) -> tuple[float, float]:
        heights = [height for _x, _y, height in self._height_map_points]
        return min(heights), max(heights)

    def _height_map_color(
        self, height: float, min_height: float, max_height: float
    ) -> QColor:
        if max_height <= min_height:
            ratio = 0.5
        else:
            ratio = (height - min_height) / (max_height - min_height)
        ratio = clamp(ratio, 0.0, 1.0)

        low = QColor("#2f6fc0")
        middle = QColor("#f4e65c")
        high = QColor("#c94035")
        if ratio <= 0.5:
            return self._interpolate_color(low, middle, ratio * 2.0)
        return self._interpolate_color(middle, high, (ratio - 0.5) * 2.0)

    def _interpolate_color(self, start: QColor, end: QColor, ratio: float) -> QColor:
        return QColor(
            round(start.red() + (end.red() - start.red()) * ratio),
            round(start.green() + (end.green() - start.green()) * ratio),
            round(start.blue() + (end.blue() - start.blue()) * ratio),
        )

    def _draw_probe_regions(self, painter: QPainter, bounds: QRectF) -> None:
        scale = bounds.width() / STAGE_X_MAX_MM
        painter.setPen(QPen(QColor("#7b4b00"), 2))

        for label, x, y, diameter in STAGE_PROBE_REGIONS:
            px, py = self._map_stage_point(x, y, bounds)
            radius = diameter * scale / 2
            marker_rect = QRectF(px - radius, py - radius, radius * 2, radius * 2)
            label_rect = QRectF(px - 32.0, py - 9.0, 64.0, 18.0)
            painter.drawEllipse(marker_rect)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_position_cross(self, painter: QPainter, bounds: QRectF) -> None:
        if self._x is None or self._y is None:
            return

        px, py = self._map_stage_point(self._x, self._y, bounds)
        color = QColor("#1565c0") if self._homed else QColor("#9aa0a6")
        painter.setPen(QPen(color, 2))
        size = 10.0
        painter.drawLine(QLineF(px - size, py, px + size, py))
        painter.drawLine(QLineF(px, py - size, px, py + size))

    def _map_stage_point(self, x: float, y: float, bounds: QRectF) -> tuple[float, float]:
        clamped_x = max(0.0, min(STAGE_X_MAX_MM, x))
        clamped_y = max(0.0, min(STAGE_Y_MAX_MM, y))
        px = bounds.left() + (1.0 - clamped_x / STAGE_X_MAX_MM) * bounds.width()
        py = bounds.top() + (clamped_y / STAGE_Y_MAX_MM) * bounds.height()
        return px, py

    def _map_widget_point(
        self, px: float, py: float, bounds: QRectF
    ) -> tuple[float, float]:
        x_fraction = 1.0 - (px - bounds.left()) / bounds.width()
        y_fraction = (py - bounds.top()) / bounds.height()
        x = max(0.0, min(STAGE_X_MAX_MM, x_fraction * STAGE_X_MAX_MM))
        y = max(0.0, min(STAGE_Y_MAX_MM, y_fraction * STAGE_Y_MAX_MM))
        return x, y

    def _work_area_widget_rect(self, bounds: QRectF) -> QRectF:
        x, y, width, height = self._work_area
        left, top = self._map_stage_point(x + width, y, bounds)
        right, bottom = self._map_stage_point(x, y + height, bounds)
        return QRectF(left, top, right - left, bottom - top).normalized()

    def _work_area_hit(self, px: float, py: float, bounds: QRectF) -> set[str]:
        rect = self._work_area_widget_rect(bounds)
        tolerance = 8.0
        hit_rect = rect.adjusted(-tolerance, -tolerance, tolerance, tolerance)
        if not hit_rect.contains(px, py):
            return set()

        edges: set[str] = set()
        if abs(px - rect.left()) <= tolerance:
            edges.add("xmax")
        if abs(px - rect.right()) <= tolerance:
            edges.add("xmin")
        if abs(py - rect.top()) <= tolerance:
            edges.add("ymin")
        if abs(py - rect.bottom()) <= tolerance:
            edges.add("ymax")
        if edges:
            return edges
        if rect.contains(px, py):
            return {"move"}
        return set()

    def _handle_print_circle_press(
        self, px: float, py: float, bounds: QRectF
    ) -> None:
        hit = self._print_circle_hit(px, py, bounds)
        x, y = self._map_widget_point(px, py, bounds)
        if not hit:
            if self._point_in_work_area(x, y):
                _center_x, _center_y, radius = (
                    self._print_circle or self._default_print_circle()
                )
                self._print_circle = self._clamp_print_circle(x, y, radius)
                self.update()
            self._set_cursor_for_circle_hit(set())
            return

        self._circle_drag_mode = "radius" if "radius" in hit else "move"
        self._circle_drag_start_stage = (x, y)
        self._circle_drag_start = self._print_circle
        self._set_cursor_for_circle_hit(hit)

    def _print_circle_hit(self, px: float, py: float, bounds: QRectF) -> set[str]:
        if self._print_circle is None:
            return set()

        center_x, center_y, radius = self._print_circle
        x, y = self._map_widget_point(px, py, bounds)
        distance = math.hypot(x - center_x, y - center_y)
        tolerance = 8.0 / (bounds.width() / STAGE_X_MAX_MM)
        if abs(distance - radius) <= tolerance:
            return {"radius"}
        if distance < radius:
            return {"move"}
        return set()

    def _set_cursor_for_circle_hit(self, hit: set[str]) -> None:
        if "radius" in hit:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif "move" in hit:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._print_circle_edit_enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._update_cursor()

    def _update_print_circle_drag(self, current_x: float, current_y: float) -> None:
        if (
            self._circle_drag_mode is None
            or self._circle_drag_start_stage is None
            or self._circle_drag_start is None
        ):
            return

        start_x, start_y = self._circle_drag_start_stage
        center_x, center_y, radius = self._circle_drag_start
        if self._circle_drag_mode == "move":
            self._print_circle = self._clamp_print_circle(
                center_x + current_x - start_x,
                center_y + current_y - start_y,
                radius,
            )
        else:
            new_radius = math.hypot(current_x - center_x, current_y - center_y)
            self._print_circle = self._clamp_print_circle(
                center_x,
                center_y,
                new_radius,
            )
        self.update()

    def _default_print_circle(self) -> tuple[float, float, float]:
        x, y, width, height = self._work_area
        radius = max(
            MIN_PRINT_CIRCLE_RADIUS_MM,
            min(width, height) / 4.0,
        )
        return self._clamp_print_circle(
            x + width / 2.0,
            y + height / 2.0,
            radius,
        )

    def _constrain_print_circle(self) -> None:
        if self._print_circle is None:
            return
        center_x, center_y, radius = self._print_circle
        self._print_circle = self._clamp_print_circle(center_x, center_y, radius)

    def _clamp_print_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> tuple[float, float, float]:
        work_x, work_y, work_width, work_height = self._work_area
        max_radius = max(
            MIN_PRINT_CIRCLE_RADIUS_MM,
            min(work_width, work_height) / 2.0,
        )
        radius = clamp(radius, MIN_PRINT_CIRCLE_RADIUS_MM, max_radius)
        center_x = clamp(center_x, work_x + radius, work_x + work_width - radius)
        center_y = clamp(center_y, work_y + radius, work_y + work_height - radius)
        radius = min(
            radius,
            center_x - work_x,
            work_x + work_width - center_x,
            center_y - work_y,
            work_y + work_height - center_y,
        )
        radius = max(MIN_PRINT_CIRCLE_RADIUS_MM, radius)
        return center_x, center_y, radius

    def _point_in_work_area(self, x: float, y: float) -> bool:
        work_x, work_y, work_width, work_height = self._work_area
        return (
            work_x <= x <= work_x + work_width
            and work_y <= y <= work_y + work_height
        )

    def _set_cursor_for_hit(self, hit: set[str]) -> None:
        if not hit:
            self._update_cursor()
            return
        if hit == {"move"}:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        has_x = any(edge in hit for edge in ("xmin", "xmax"))
        has_y = any(edge in hit for edge in ("ymin", "ymax"))
        if has_x and has_y:
            if ("xmin" in hit and "ymin" in hit) or (
                "xmax" in hit and "ymax" in hit
            ):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif has_x:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif has_y:
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def _update_work_area_drag(self, current_x: float, current_y: float) -> None:
        if self._drag_start_stage is None or self._drag_start_area is None:
            return

        start_stage_x, start_stage_y = self._drag_start_stage
        start_x, start_y, start_width, start_height = self._drag_start_area
        dx = current_x - start_stage_x
        dy = current_y - start_stage_y
        if not self._drag_moved:
            if abs(dx) <= 0.05 and abs(dy) <= 0.05:
                return
            self._drag_moved = True

        x_min = start_x
        x_max = start_x + start_width
        y_min = start_y
        y_max = start_y + start_height

        if "move" in self._drag_edges:
            new_x = clamp(start_x + dx, 0.0, STAGE_X_MAX_MM - start_width)
            new_y = clamp(
                start_y + dy,
                USER_STAGE_Y_MIN_MM,
                STAGE_Y_MAX_MM - start_height,
            )
            self._work_area = (new_x, new_y, start_width, start_height)
        else:
            if "xmin" in self._drag_edges:
                x_min = clamp(start_x + dx, 0.0, x_max - MIN_WORK_AREA_SIZE_MM)
            if "xmax" in self._drag_edges:
                x_max = clamp(
                    start_x + start_width + dx,
                    x_min + MIN_WORK_AREA_SIZE_MM,
                    STAGE_X_MAX_MM,
                )
            if "ymin" in self._drag_edges:
                y_min = clamp(
                    start_y + dy,
                    USER_STAGE_Y_MIN_MM,
                    y_max - MIN_WORK_AREA_SIZE_MM,
                )
            if "ymax" in self._drag_edges:
                y_max = clamp(
                    start_y + start_height + dy,
                    y_min + MIN_WORK_AREA_SIZE_MM,
                    STAGE_Y_MAX_MM,
                )
            self._work_area = (x_min, y_min, x_max - x_min, y_max - y_min)

        self.update()

    def _clamp_work_area(
        self, x: float, y: float, width: float, height: float
    ) -> tuple[float, float, float, float]:
        width = clamp(width, MIN_WORK_AREA_SIZE_MM, STAGE_X_MAX_MM)
        height = clamp(
            height,
            MIN_WORK_AREA_SIZE_MM,
            STAGE_Y_MAX_MM - USER_STAGE_Y_MIN_MM,
        )
        x = clamp(x, 0.0, STAGE_X_MAX_MM - width)
        y = clamp(y, USER_STAGE_Y_MIN_MM, STAGE_Y_MAX_MM - height)
        return x, y, width, height

    def _update_cursor(self) -> None:
        if self._print_circle_edit_enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._motion_enabled:
            self.setCursor(self._cross_cursor)
        elif self._disabled_by_not_homed:
            self.setCursor(Qt.CursorShape.ForbiddenCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def _build_cross_cursor(self) -> QCursor:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#8a8f98"), 2))
        painter.drawLine(4, 12, 20, 12)
        painter.drawLine(12, 4, 12, 20)
        painter.end()
        return QCursor(pixmap, 12, 12)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MyVolt Controller")
        self.resize(1180, 760)

        self.serial_thread: SerialThread | None = None
        self.current_x: float | None = None
        self.current_y: float | None = None
        self.current_z: float | None = None
        self.current_e: float | None = None
        self.current_tool_type: str | None = None
        self.maximum_z_position: float | None = None
        self.probe_tool_offset: float | None = None
        self.dispenser_tool_offset: float | None = None
        self.preparation_in_progress = False
        self.awaiting_z_switch_measurement = False
        self.pending_tool_offset: float | None = None
        self.height_map_points: list[tuple[float, float, float]] = []
        self.height_map_plan: list[tuple[float, float]] = []
        self.height_map_index = 0
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_probe_phase: str | None = None
        self.print_circle_editing = False
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle: tuple[float, float, float] | None = None
        self.all_axes_homed = False
        self.motion_busy = False
        self.general_command_widgets: list[QWidget] = []
        self.setup_motion_widgets: list[QWidget] = []
        self.homed_motion_widgets: list[QWidget] = []
        self.camera_config: dict[str, object] = {}
        self.camera_exposure_compensation = 0.0
        self.camera_devices_by_id = {}
        self.alignment_cameras = [None, None]
        self.alignment_camera_sessions = [None, None]
        self.alignment_camera_combos: list[QComboBox] = []
        self.alignment_video_outputs: list[QWidget] = []
        self.alignment_refreshing = False

        self.port_combo = QComboBox()
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self.x_label = QLabel("X: --")
        self.y_label = QLabel("Y: --")
        self.z_label = QLabel("Z: --")
        self.e_label = QLabel("E: --")
        self.temp_label = QLabel("Temp: -- / -- C")
        self.probe_label = QLabel("Probe: --")
        self.homed_label = QLabel("Home: --")
        self.tool_label = QLabel("Dispenser: --")
        self.probe_tool_offset_label = QLabel("Undefined")
        self.dispenser_tool_offset_label = QLabel("Undefined")
        self.height_map_status_label = QLabel("No height map")
        self.print_status_label = QLabel("No print queued")
        self.error_label = QLabel("")

        self._build_ui()
        self._connect_ui()
        self.load_camera_config()
        self.refresh_alignment_cameras()
        self.refresh_ports()
        self.update_control_states()

    def _build_ui(self) -> None:
        root = QWidget()
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        workspace_layout = QHBoxLayout()
        workspace_layout.setSpacing(6)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(6)
        left_panel.addWidget(self._build_hardware_group())
        left_panel.addWidget(self._build_stage_control_group())
        left_panel.addWidget(self._build_temperature_group())
        left_panel.addStretch(1)

        right_panel = QVBoxLayout()
        right_panel.setSpacing(6)
        right_panel.addWidget(self._build_alignment_group())
        right_panel.addWidget(self._build_leveling_group())
        right_panel.addWidget(self._build_printing_group())
        right_panel.addStretch(1)

        workspace_layout.addLayout(left_panel, stretch=0)
        workspace_layout.addWidget(self._build_stage_group(), stretch=1)
        workspace_layout.addLayout(right_panel, stretch=0)
        main_layout.addLayout(workspace_layout, stretch=5)

        raw_group = QGroupBox("Raw Payload")
        raw_layout = QHBoxLayout(raw_group)
        self.raw_input = QLineEdit()
        self.raw_input.setPlaceholderText("Example: V1 X0 Y0")
        self.raw_send_button = QPushButton("Send")
        raw_layout.addWidget(self.raw_input, stretch=1)
        raw_layout.addWidget(self.raw_send_button)
        main_layout.addWidget(raw_group)
        self.general_command_widgets.extend([self.raw_input, self.raw_send_button])

        log_group = QGroupBox("Raw Log")
        log_layout = QVBoxLayout(log_group)
        log_layout.addWidget(self.log_view)
        main_layout.addWidget(log_group, stretch=1)

        self.setCentralWidget(root)
        self._build_status_bar()

    def _build_hardware_group(self) -> QGroupBox:
        group = QGroupBox("Hardware")
        group.setMaximumWidth(360)
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)
        self.refresh_button = QPushButton("Refresh")
        self.home_button = QPushButton("Home XY")
        self.prepare_probe_button = QPushButton("Prepare Probe")
        self.emergency_button = QPushButton("Emergency Stop")
        self.emergency_button.setObjectName("emergencyButton")
        layout.addWidget(QLabel("Port"), 0, 0)
        layout.addWidget(self.port_combo, 0, 1, 1, 3)
        layout.addWidget(self.refresh_button, 0, 4)
        layout.addWidget(self.connect_button, 1, 1, 1, 2)
        layout.addWidget(self.disconnect_button, 1, 3, 1, 2)
        layout.addWidget(self.home_button, 2, 1, 1, 2)
        layout.addWidget(self.prepare_probe_button, 2, 3, 1, 2)
        layout.addWidget(self.emergency_button, 3, 1, 1, 4)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.setup_motion_widgets.extend([self.home_button, self.prepare_probe_button])
        return group

    def _build_temperature_group(self) -> QGroupBox:
        group = QGroupBox("Temperature")
        group.setMaximumWidth(360)
        layout = QHBoxLayout(group)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 260.0)
        self.temp_spin.setDecimals(1)
        self.temp_spin.setValue(150.0)
        self.temp_spin.setSuffix(" C")
        self.heat_duration_spin = QSpinBox()
        self.heat_duration_spin.setRange(1, 3600)
        self.heat_duration_spin.setValue(300)
        self.heat_duration_spin.setSuffix(" s")
        self.set_temp_button = QPushButton("Set")
        self.stop_heat_button = QPushButton("Off")
        layout.addWidget(QLabel("Target"))
        layout.addWidget(self.temp_spin)
        layout.addWidget(QLabel("Duration"))
        layout.addWidget(self.heat_duration_spin)
        layout.addWidget(self.set_temp_button)
        layout.addWidget(self.stop_heat_button)
        self.general_command_widgets.extend(
            [
                self.temp_spin,
                self.heat_duration_spin,
                self.set_temp_button,
                self.stop_heat_button,
            ]
        )
        return group

    def _build_stage_control_group(self) -> QGroupBox:
        group = QGroupBox("Stage Control")
        group.setMaximumWidth(360)
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(4)
        layout.setVerticalSpacing(4)

        self.xy_step = QDoubleSpinBox()
        self.xy_step.setRange(0.001, 50.0)
        self.xy_step.setDecimals(3)
        self.xy_step.setValue(1.0)
        self.xy_step.setSuffix(" mm")
        self.xy_step.setMaximumWidth(92)
        self.x_minus_button = QPushButton("X-")
        self.x_plus_button = QPushButton("X+")
        self.y_minus_button = QPushButton("Y-")
        self.y_plus_button = QPushButton("Y+")

        self.z_step = QDoubleSpinBox()
        self.z_step.setRange(0.001, 10.0)
        self.z_step.setDecimals(3)
        self.z_step.setValue(0.1)
        self.z_step.setSuffix(" mm")
        self.z_step.setMaximumWidth(92)
        self.z_minus_button = QPushButton("Z-")
        self.z_plus_button = QPushButton("Z+")

        self.e_step = QDoubleSpinBox()
        self.e_step.setRange(0.0001, 10.0)
        self.e_step.setDecimals(4)
        self.e_step.setValue(0.01)
        self.e_step.setMaximumWidth(92)
        self.e_minus_button = QPushButton("E-")
        self.e_plus_button = QPushButton("E+")

        self.probe_fast_button = QPushButton("Probe R1")
        self.probe_slow_button = QPushButton("Probe R0.1")

        layout.addWidget(QLabel("XY"), 0, 0)
        layout.addWidget(self.xy_step, 0, 1, 1, 2)
        layout.addWidget(self.y_plus_button, 0, 3)
        layout.addWidget(self.x_minus_button, 1, 1)
        layout.addWidget(self.x_plus_button, 1, 2)
        layout.addWidget(self.y_minus_button, 1, 3)

        layout.addWidget(QLabel("Z"), 2, 0)
        layout.addWidget(self.z_step, 2, 1)
        layout.addWidget(self.z_minus_button, 2, 2)
        layout.addWidget(self.z_plus_button, 2, 3)

        layout.addWidget(QLabel("E"), 3, 0)
        layout.addWidget(self.e_step, 3, 1)
        layout.addWidget(self.e_minus_button, 3, 2)
        layout.addWidget(self.e_plus_button, 3, 3)

        layout.addWidget(QLabel("Probe"), 4, 0)
        layout.addWidget(self.probe_fast_button, 4, 1, 1, 2)
        layout.addWidget(self.probe_slow_button, 4, 3)

        self.x_minus_button.clicked.connect(
            lambda: self.jog_xy(-self.xy_step.value(), 0.0)
        )
        self.x_plus_button.clicked.connect(
            lambda: self.jog_xy(self.xy_step.value(), 0.0)
        )
        self.y_minus_button.clicked.connect(
            lambda: self.jog_xy(0.0, -self.xy_step.value())
        )
        self.y_plus_button.clicked.connect(
            lambda: self.jog_xy(0.0, self.xy_step.value())
        )
        self.z_minus_button.clicked.connect(lambda: self.jog_z(-self.z_step.value()))
        self.z_plus_button.clicked.connect(lambda: self.jog_z(self.z_step.value()))
        self.e_minus_button.clicked.connect(lambda: self.jog_e(-self.e_step.value()))
        self.e_plus_button.clicked.connect(lambda: self.jog_e(self.e_step.value()))

        self.homed_motion_widgets.extend(
            [
                self.xy_step,
                self.x_minus_button,
                self.x_plus_button,
                self.y_minus_button,
                self.y_plus_button,
                self.z_step,
                self.e_step,
                self.z_minus_button,
                self.z_plus_button,
                self.e_minus_button,
                self.e_plus_button,
                self.probe_fast_button,
                self.probe_slow_button,
            ]
        )
        return group

    def _build_leveling_group(self) -> QGroupBox:
        group = QGroupBox("Leveling")
        group.setMaximumWidth(360)
        layout = QFormLayout(group)
        self.height_map_x_points_spin = QSpinBox()
        self.height_map_x_points_spin.setRange(2, 50)
        self.height_map_x_points_spin.setValue(5)
        self.height_map_y_points_spin = QSpinBox()
        self.height_map_y_points_spin.setRange(2, 50)
        self.height_map_y_points_spin.setValue(5)
        point_count_layout = QHBoxLayout()
        point_count_layout.addWidget(QLabel("X"))
        point_count_layout.addWidget(self.height_map_x_points_spin)
        point_count_layout.addWidget(QLabel("Y"))
        point_count_layout.addWidget(self.height_map_y_points_spin)

        self.delete_height_map_button = QPushButton("Delete Height Map")
        self.save_height_map_button = QPushButton("Save")
        self.load_height_map_button = QPushButton("Load")
        self.start_height_map_button = QPushButton("Start Probing")
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.delete_height_map_button)
        button_layout.addWidget(self.save_height_map_button)
        button_layout.addWidget(self.load_height_map_button)
        button_layout.addWidget(self.start_height_map_button)

        layout.addRow("Probe offset", self.probe_tool_offset_label)
        layout.addRow("Dispenser offset", self.dispenser_tool_offset_label)
        layout.addRow("Probe points", point_count_layout)
        layout.addRow("Height map", self.height_map_status_label)
        layout.addRow("", button_layout)
        self.homed_motion_widgets.append(self.start_height_map_button)
        return group

    def _build_printing_group(self) -> QGroupBox:
        group = QGroupBox("Printing")
        group.setMaximumWidth(360)
        layout = QFormLayout(group)

        self.print_speed_spin = QDoubleSpinBox()
        self.print_speed_spin.setRange(0.1, 1000.0)
        self.print_speed_spin.setDecimals(1)
        self.print_speed_spin.setValue(200.0)
        self.print_speed_spin.setSuffix(" mm/s")

        self.print_height_spin = QDoubleSpinBox()
        self.print_height_spin.setRange(0.0, 10.0)
        self.print_height_spin.setDecimals(3)
        self.print_height_spin.setValue(0.15)
        self.print_height_spin.setSuffix(" mm")

        self.print_kick_spin = QDoubleSpinBox()
        self.print_kick_spin.setRange(0.0, 5000.0)
        self.print_kick_spin.setDecimals(0)
        self.print_kick_spin.setValue(200.0)
        self.print_kick_spin.setSuffix(" um")

        self.print_retract_spin = QDoubleSpinBox()
        self.print_retract_spin.setRange(0.0, 5000.0)
        self.print_retract_spin.setDecimals(0)
        self.print_retract_spin.setValue(200.0)
        self.print_retract_spin.setSuffix(" um")

        self.print_max_length_spin = QDoubleSpinBox()
        self.print_max_length_spin.setRange(1.0, 1000.0)
        self.print_max_length_spin.setDecimals(1)
        self.print_max_length_spin.setValue(30.0)
        self.print_max_length_spin.setSuffix(" mm")

        self.print_travel_height_spin = QDoubleSpinBox()
        self.print_travel_height_spin.setRange(0.0, 20.0)
        self.print_travel_height_spin.setDecimals(3)
        self.print_travel_height_spin.setValue(2.0)
        self.print_travel_height_spin.setSuffix(" mm")

        self.print_circle_button = QPushButton("Print Circle")

        layout.addRow("Speed", self.print_speed_spin)
        layout.addRow("Print height", self.print_height_spin)
        layout.addRow("Kick", self.print_kick_spin)
        layout.addRow("Retract", self.print_retract_spin)
        layout.addRow("Max length", self.print_max_length_spin)
        layout.addRow("Travel height", self.print_travel_height_spin)
        layout.addRow("Status", self.print_status_label)
        layout.addRow("", self.print_circle_button)

        self.printing_widgets = [
            self.print_speed_spin,
            self.print_height_spin,
            self.print_kick_spin,
            self.print_retract_spin,
            self.print_max_length_spin,
            self.print_travel_height_spin,
        ]
        return group

    def _build_alignment_group(self) -> QGroupBox:
        group = QGroupBox("Alignment")
        group.setMaximumWidth(360)
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)

        self.alignment_camera_1_combo = QComboBox()
        self.alignment_camera_2_combo = QComboBox()
        self.alignment_camera_1_view = self._build_camera_view()
        self.alignment_camera_2_view = self._build_camera_view()
        self.alignment_camera_refresh_button = QPushButton("Refresh Cameras")
        self.alignment_exposure_slider = QSlider(Qt.Orientation.Horizontal)
        self.alignment_exposure_slider.setRange(
            round(CAMERA_EXPOSURE_MIN_EV * CAMERA_EXPOSURE_SCALE),
            round(CAMERA_EXPOSURE_MAX_EV * CAMERA_EXPOSURE_SCALE),
        )
        self.alignment_exposure_slider.setValue(
            round(self.camera_exposure_compensation * CAMERA_EXPOSURE_SCALE)
        )
        self.alignment_exposure_value_label = QLabel(
            self.camera_exposure_label_text()
        )
        self.alignment_exposure_support_label = QLabel("Exposure support: --")

        layout.addWidget(QLabel("View 1"), 0, 0)
        layout.addWidget(QLabel("View 2"), 0, 1)
        layout.addWidget(self.alignment_camera_1_combo, 1, 0)
        layout.addWidget(self.alignment_camera_2_combo, 1, 1)
        layout.addWidget(self.alignment_camera_1_view, 2, 0)
        layout.addWidget(self.alignment_camera_2_view, 2, 1)
        layout.addWidget(self.alignment_camera_refresh_button, 3, 0, 1, 2)
        layout.addWidget(QLabel("Exposure"), 4, 0)
        layout.addWidget(self.alignment_exposure_slider, 5, 0, 1, 2)
        layout.addWidget(self.alignment_exposure_value_label, 4, 1)
        layout.addWidget(self.alignment_exposure_support_label, 6, 0, 1, 2)

        self.alignment_camera_combos = [
            self.alignment_camera_1_combo,
            self.alignment_camera_2_combo,
        ]
        self.alignment_video_outputs = [
            self.alignment_camera_1_view,
            self.alignment_camera_2_view,
        ]

        self.alignment_camera_1_combo.currentIndexChanged.connect(
            lambda _index: self.on_alignment_camera_changed(0)
        )
        self.alignment_camera_2_combo.currentIndexChanged.connect(
            lambda _index: self.on_alignment_camera_changed(1)
        )
        self.alignment_camera_refresh_button.clicked.connect(
            self.refresh_alignment_cameras
        )
        self.alignment_exposure_slider.valueChanged.connect(
            self.on_alignment_exposure_changed
        )
        return group

    def _build_camera_view(self) -> QWidget:
        if QVideoWidget is not None:
            view = QVideoWidget()
            view.setMinimumSize(150, 110)
            return view

        label = QLabel("Qt Multimedia unavailable")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(150, 110)
        label.setStyleSheet(
            "background: #202124; color: #f1f3f4; border: 1px solid #5f6368;"
        )
        return label

    def _build_stage_group(self) -> QGroupBox:
        group = QGroupBox("Stage View")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)
        self.stage_view = StageView()
        self.stage_view.stage_clicked.connect(self.move_to_stage_position)
        layout.addWidget(self.stage_view)
        return group

    def _build_status_bar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        status.addPermanentWidget(self.x_label)
        status.addPermanentWidget(self.y_label)
        status.addPermanentWidget(self.z_label)
        status.addPermanentWidget(self.e_label)
        status.addPermanentWidget(self.temp_label)
        status.addPermanentWidget(self.probe_label)
        status.addPermanentWidget(self.homed_label)
        status.addPermanentWidget(self.tool_label)
        status.addPermanentWidget(self.error_label, stretch=1)

    def _connect_ui(self) -> None:
        self.connect_button.clicked.connect(self.connect_serial)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.home_button.clicked.connect(self.home_stage)
        self.prepare_probe_button.clicked.connect(self.prepare_probe)
        self.emergency_button.clicked.connect(self.emergency_stop)
        self.set_temp_button.clicked.connect(self.set_temperature)
        self.stop_heat_button.clicked.connect(lambda: self.send_payload("M142"))
        self.probe_fast_button.clicked.connect(lambda: self.manual_probe("R1"))
        self.probe_slow_button.clicked.connect(lambda: self.manual_probe("R0.1"))
        self.delete_height_map_button.clicked.connect(self.delete_height_map)
        self.save_height_map_button.clicked.connect(self.save_height_map)
        self.load_height_map_button.clicked.connect(self.load_height_map)
        self.start_height_map_button.clicked.connect(self.start_height_map_probing)
        self.print_circle_button.clicked.connect(self.handle_print_circle_button)
        self.raw_send_button.clicked.connect(self.send_raw_payload)
        self.raw_input.returnPressed.connect(self.send_raw_payload)

    def refresh_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.clear()
        ports = list(list_ports.comports())
        for port in ports:
            label = f"{port.device} - {port.description}"
            self.port_combo.addItem(label, port.device)
        if not ports:
            self.port_combo.addItem("No serial ports found", "")
        elif current:
            index = self.port_combo.findData(current)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)

    def load_camera_config(self) -> None:
        self.camera_config = {}
        if not CAMERA_CONFIG_PATH.exists():
            return

        try:
            with CAMERA_CONFIG_PATH.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"! failed to load camera config: {exc}")
            return

        if isinstance(data, dict):
            exposure = data.get("exposure_compensation", 0.0)
            try:
                self.camera_exposure_compensation = clamp(
                    float(exposure),
                    CAMERA_EXPOSURE_MIN_EV,
                    CAMERA_EXPOSURE_MAX_EV,
                )
            except (TypeError, ValueError):
                self.camera_exposure_compensation = 0.0
            self.camera_config = {
                "view_1": str(data.get("view_1", "") or ""),
                "view_2": str(data.get("view_2", "") or ""),
                "exposure_compensation": self.camera_exposure_compensation,
            }
            if hasattr(self, "alignment_exposure_slider"):
                self.alignment_exposure_slider.blockSignals(True)
                self.alignment_exposure_slider.setValue(
                    round(
                        self.camera_exposure_compensation
                        * CAMERA_EXPOSURE_SCALE
                    )
                )
                self.alignment_exposure_slider.blockSignals(False)
                self.alignment_exposure_value_label.setText(
                    self.camera_exposure_label_text()
                )

    def save_camera_config(self) -> None:
        data = {
            "view_1": self.alignment_camera_combos[0].currentData() or "",
            "view_2": self.alignment_camera_combos[1].currentData() or "",
            "exposure_compensation": self.camera_exposure_compensation,
        }
        self.camera_config = dict(data)
        try:
            with CAMERA_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save camera config: {exc}")

    def refresh_alignment_cameras(self) -> None:
        current_ids = [
            combo.currentData() or self.camera_config.get(f"view_{index + 1}", "")
            for index, combo in enumerate(self.alignment_camera_combos)
        ]

        self.stop_alignment_cameras()
        self.camera_devices_by_id = {}
        if QMediaDevices is None:
            self.alignment_refreshing = True
            for combo in self.alignment_camera_combos:
                combo.blockSignals(True)
                combo.clear()
                combo.addItem("Qt Multimedia unavailable", "")
                combo.blockSignals(False)
            self.alignment_refreshing = False
            return

        devices = list(QMediaDevices.videoInputs())
        for device in devices:
            device_id = self.camera_device_id(device)
            self.camera_devices_by_id[device_id] = device

        self.alignment_refreshing = True
        for slot, combo in enumerate(self.alignment_camera_combos):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("None", "")
            if devices:
                for device in devices:
                    device_id = self.camera_device_id(device)
                    combo.addItem(device.description(), device_id)
            else:
                combo.addItem("No cameras found", "")

            saved_id = current_ids[slot]
            if saved_id:
                index = combo.findData(saved_id)
                if index >= 0:
                    combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self.alignment_refreshing = False

        for slot in range(len(self.alignment_camera_combos)):
            self.start_alignment_camera(slot)

    def camera_device_id(self, device) -> str:
        device_id = device.id()
        try:
            return bytes(device_id).decode("utf-8", errors="replace")
        except TypeError:
            return str(device_id)

    def on_alignment_camera_changed(self, slot: int) -> None:
        if self.alignment_refreshing:
            return
        self.start_alignment_camera(slot)
        self.save_camera_config()

    def start_alignment_camera(self, slot: int) -> None:
        self.stop_alignment_camera(slot)
        if (
            QCamera is None
            or QMediaCaptureSession is None
            or not self.alignment_video_outputs
        ):
            return

        device_id = self.alignment_camera_combos[slot].currentData()
        if not device_id:
            return

        device = self.camera_devices_by_id.get(device_id)
        if device is None:
            return

        camera = QCamera(device, self)
        session = QMediaCaptureSession(self)
        session.setCamera(camera)
        session.setVideoOutput(self.alignment_video_outputs[slot])
        try:
            camera.errorOccurred.connect(
                lambda _error, message, camera_slot=slot: self.append_log(
                    f"! camera {camera_slot + 1}: {message}"
                )
            )
        except (AttributeError, TypeError):
            pass
        camera.start()
        self.alignment_cameras[slot] = camera
        self.alignment_camera_sessions[slot] = session
        self.apply_alignment_exposure()
        self.update_alignment_exposure_support()

    def on_alignment_exposure_changed(self, value: int) -> None:
        self.camera_exposure_compensation = value / CAMERA_EXPOSURE_SCALE
        self.alignment_exposure_value_label.setText(
            self.camera_exposure_label_text()
        )
        self.apply_alignment_exposure()
        self.save_camera_config()

    def camera_exposure_label_text(self) -> str:
        return f"{self.camera_exposure_compensation:+.1f} EV"

    def apply_alignment_exposure(self) -> None:
        for camera in self.alignment_cameras:
            if camera is None:
                continue
            if not self.camera_supports_exposure_compensation(camera):
                continue
            setter = getattr(camera, "setExposureCompensation", None)
            if callable(setter):
                setter(self.camera_exposure_compensation)

    def camera_supports_exposure_compensation(self, camera) -> bool:
        if QCamera is None or camera is None:
            return False

        feature_enum = getattr(QCamera, "Feature", None)
        exposure_feature = getattr(feature_enum, "ExposureCompensation", None)
        supported_features = getattr(camera, "supportedFeatures", None)
        if exposure_feature is None or not callable(supported_features):
            return False

        try:
            return bool(supported_features() & exposure_feature)
        except TypeError:
            return False

    def update_alignment_exposure_support(self) -> None:
        active_cameras = [
            camera for camera in self.alignment_cameras if camera is not None
        ]
        supported_count = sum(
            1
            for camera in active_cameras
            if self.camera_supports_exposure_compensation(camera)
        )

        if not hasattr(self, "alignment_exposure_support_label"):
            return
        if not active_cameras:
            self.alignment_exposure_support_label.setText("Exposure support: --")
            self.alignment_exposure_slider.setEnabled(False)
            return

        total_count = len(active_cameras)
        self.alignment_exposure_support_label.setText(
            f"Exposure support: {supported_count}/{total_count}"
        )
        self.alignment_exposure_slider.setEnabled(supported_count > 0)

    def stop_alignment_camera(self, slot: int) -> None:
        camera = self.alignment_cameras[slot]
        if camera is not None:
            camera.stop()
            camera.deleteLater()
            self.alignment_cameras[slot] = None

        session = self.alignment_camera_sessions[slot]
        if session is not None:
            session.deleteLater()
            self.alignment_camera_sessions[slot] = None
        self.update_alignment_exposure_support()

    def stop_alignment_cameras(self) -> None:
        for slot in range(len(self.alignment_cameras)):
            self.stop_alignment_camera(slot)

    def connect_serial(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "No Port", "Select a serial port first.")
            return

        self.current_x = None
        self.current_y = None
        self.current_z = None
        self.current_e = None
        self.current_tool_type = None
        self.maximum_z_position = None
        self.all_axes_homed = False
        self.motion_busy = False
        self.reset_tool_offsets()
        self.reset_height_map()
        self.reset_printing_state()
        self.stage_view.clear_position()
        self.serial_thread = SerialThread(port=port)
        self.serial_thread.connected.connect(self.on_connected)
        self.serial_thread.disconnected.connect(self.on_disconnected)
        self.serial_thread.sent_line.connect(lambda line: self.append_log(f"> {line}"))
        self.serial_thread.received_line.connect(self.on_received_line)
        self.serial_thread.error_line.connect(self.on_error_line)
        self.serial_thread.status_message.connect(lambda text: self.append_log(f"# {text}"))
        self.serial_thread.start()

        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self.update_control_states()
        self.append_log(f"# opening {port}")

    def disconnect_serial(self) -> None:
        if self.serial_thread is not None:
            self.serial_thread.stop()
            self.serial_thread.wait(1500)

    def on_connected(self, port: str, baud: int) -> None:
        self.append_log(f"# connected to {port} at {baud} baud")
        self.statusBar().showMessage(f"Connected to {port} at {baud} baud", 5000)
        self.update_control_states()

    def on_disconnected(self) -> None:
        self.append_log("# disconnected")
        self.serial_thread = None
        self.current_tool_type = None
        self.maximum_z_position = None
        self.all_axes_homed = False
        self.motion_busy = False
        self.reset_tool_offsets()
        self.reset_height_map()
        self.reset_printing_state()
        self.homed_label.setText("Home: --")
        self.homed_label.setStyleSheet("")
        self.tool_label.setText("Dispenser: --")
        self.stage_view.clear_position()
        self.update_control_states()

    def send_payload(
        self, payload: str, urgent: bool = False, force: bool = False
    ) -> bool:
        if self.serial_thread is None:
            self.append_log(f"! not connected: {payload}")
            return False
        if self.motion_busy and not urgent and not force:
            self.append_log(f"! command blocked during M400 synchronization: {payload}")
            return False
        self.serial_thread.enqueue(payload, urgent=urgent)
        return True

    def reset_tool_offsets(self) -> None:
        self.probe_tool_offset = None
        self.dispenser_tool_offset = None
        self.preparation_in_progress = False
        self.awaiting_z_switch_measurement = False
        self.pending_tool_offset = None
        self.update_tool_offset_labels()

    def reset_height_map(self) -> None:
        self.height_map_points = []
        self.height_map_plan = []
        self.height_map_index = 0
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_probe_phase = None
        if hasattr(self, "stage_view"):
            self.stage_view.set_height_map_points([])
        if hasattr(self, "height_map_status_label"):
            self.height_map_status_label.setText("No height map")

    def reset_printing_state(self) -> None:
        self.print_circle_editing = False
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle = None
        if hasattr(self, "stage_view"):
            self.stage_view.clear_print_circle()
        if hasattr(self, "print_circle_button"):
            self.print_circle_button.setText("Print Circle")
        if hasattr(self, "print_status_label"):
            self.print_status_label.setText("No print queued")

    def update_tool_offset_labels(self) -> None:
        self.probe_tool_offset_label.setText(
            f"{self.probe_tool_offset:.6f} mm"
            if self.probe_tool_offset is not None
            else "Undefined"
        )
        self.dispenser_tool_offset_label.setText(
            f"{self.dispenser_tool_offset:.6f} mm"
            if self.dispenser_tool_offset is not None
            else "Undefined"
        )

    def clear_tool_offset_for_tool(self, tool_type: str) -> None:
        if tool_type == "Probe":
            self.probe_tool_offset = None
            self.append_log("# probe tool offset: Undefined")
        elif tool_type == "Dispenser":
            self.dispenser_tool_offset = None
            self.append_log("# dispenser tool offset: Undefined")
        else:
            return

        self.preparation_in_progress = False
        self.awaiting_z_switch_measurement = False
        self.pending_tool_offset = None
        self.update_tool_offset_labels()

    def controller_log_text(self, line: str) -> str:
        text = line.strip()
        while text.startswith("~"):
            text = text[1:].strip()
        if text.lower().startswith("log:"):
            text = text[4:].strip()
        return text

    def update_tool_offset_from_line(self, line: str) -> None:
        text = self.controller_log_text(line)

        if text == "Preparing tool":
            self.preparation_in_progress = True
            self.awaiting_z_switch_measurement = False
            self.pending_tool_offset = None
            return

        if not self.preparation_in_progress:
            return

        if text.startswith("Measure at switch: z-switch (z-min)"):
            self.awaiting_z_switch_measurement = True
            return

        if self.awaiting_z_switch_measurement:
            match = MEASUREMENT_RE.search(text)
            if match is not None:
                self.pending_tool_offset = float(match.group("value"))
                self.awaiting_z_switch_measurement = False
                return

        if text.startswith("Preparing tool -- completed"):
            self.commit_pending_tool_offset()

    def commit_pending_tool_offset(self) -> None:
        if self.pending_tool_offset is not None:
            if self.current_tool_type == "Probe":
                self.probe_tool_offset = self.pending_tool_offset
                self.append_log(
                    f"# probe tool offset: {self.probe_tool_offset:.6f} mm"
                )
            else:
                self.dispenser_tool_offset = self.pending_tool_offset
                self.append_log(
                    f"# dispenser tool offset: {self.dispenser_tool_offset:.6f} mm"
                )
            self.update_tool_offset_labels()

        self.preparation_in_progress = False
        self.awaiting_z_switch_measurement = False
        self.pending_tool_offset = None

    def update_control_states(self) -> None:
        connected = self.serial_thread is not None
        can_issue = (
            connected
            and not self.motion_busy
            and not self.height_map_active
            and not self.printing_active
        )
        homed_motion_allowed = can_issue and self.all_axes_homed
        has_height_map = bool(self.height_map_points)

        self.connect_button.setEnabled(not connected)
        self.disconnect_button.setEnabled(connected)
        self.port_combo.setEnabled(not connected)
        self.refresh_button.setEnabled(not connected)
        self.emergency_button.setEnabled(connected)

        for widget in self.general_command_widgets:
            widget.setEnabled(can_issue)
        for widget in self.setup_motion_widgets:
            widget.setEnabled(can_issue)
        for widget in self.homed_motion_widgets:
            widget.setEnabled(homed_motion_allowed)
        self.height_map_x_points_spin.setEnabled(not self.height_map_active)
        self.height_map_y_points_spin.setEnabled(not self.height_map_active)
        height_map_idle = (
            not self.height_map_active
            and not self.printing_active
            and not self.print_circle_editing
        )
        self.delete_height_map_button.setEnabled(
            bool(self.height_map_points) and height_map_idle
        )
        self.save_height_map_button.setEnabled(
            bool(self.height_map_points) and height_map_idle
        )
        self.load_height_map_button.setEnabled(can_issue and height_map_idle)
        self.start_height_map_button.setEnabled(
            homed_motion_allowed and not self.height_map_active
        )
        if hasattr(self, "printing_widgets"):
            for widget in self.printing_widgets:
                widget.setEnabled(can_issue)
        if hasattr(self, "print_circle_button"):
            self.print_circle_button.setEnabled(
                can_issue and has_height_map and not self.height_map_active
            )
        if hasattr(self, "stage_view"):
            self.stage_view.set_motion_enabled(
                homed_motion_allowed,
                disabled_by_not_homed=connected and not self.all_axes_homed,
            )
            if not self.print_circle_editing:
                self.stage_view.set_work_area_edit_enabled(
                    not self.height_map_active and not self.printing_active
                )

    def warn_not_homed(self) -> None:
        QMessageBox.warning(
            self,
            "Axes Not Homed",
            (
                "Motion is disabled until the controller reports all axes homed.\n\n"
                "Use Home XY / Prepare Probe as needed and wait for HOMED in the status bar."
            ),
            QMessageBox.StandardButton.Ok,
        )
        self.append_log("! motion blocked: axes are not all homed")

    def send_synchronized_motion(
        self, *payloads: str, require_homed: bool = True
    ) -> bool:
        if self.motion_busy:
            self.append_log("! command blocked: waiting for M400 synchronization")
            return False
        if require_homed and not self.all_axes_homed:
            self.warn_not_homed()
            return False
        if self.serial_thread is None:
            self.append_log("! not connected")
            return False

        queued_any = False
        for payload in payloads:
            if self.send_payload(payload):
                queued_any = True
        if not queued_any:
            return False

        if not self.send_payload("M400"):
            return False
        self.motion_busy = True
        self.update_control_states()
        return True

    def update_motion_sync(self, line: str) -> None:
        if not self.motion_busy:
            return
        if self.height_map_active:
            if self.height_map_finishing and (
                line == "empty" or line.startswith("positionUpdate") or is_error_line(line)
            ):
                self.finish_height_map()
            return
        if self.printing_active:
            if is_error_line(line):
                self.cancel_printing("Print failed")
                self.motion_busy = False
                self.update_control_states()
                return
            if self.print_preparing:
                if (
                    (
                        line == "ok"
                        or line == "empty"
                        or line.startswith("positionUpdate")
                    )
                    and self.serial_thread is not None
                    and self.serial_thread.is_idle()
                ):
                    self.queue_prepared_circle_print()
                return
            if (
                (line == "ok" or line == "empty")
                and self.serial_thread is not None
                and self.serial_thread.is_idle()
            ):
                self.finish_printing()
            return
        if line == "empty" or line.startswith("positionUpdate") or is_error_line(line):
            self.motion_busy = False
            self.update_control_states()

    def raw_payload_command(self, payload: str) -> str:
        return payload.strip().split(maxsplit=1)[0].upper()

    def raw_payload_is_motion(self, payload: str) -> bool:
        return self.raw_payload_command(payload) in {
            "G0",
            "G1",
            "V1",
            "V2",
            "V3",
            "V4",
            "V5",
        }

    def ensure_probe_ready(self) -> bool:
        if self.current_tool_type == "Probe":
            return True

        current = self.current_tool_type or "unknown"
        QMessageBox.warning(
            self,
            "Probe Required",
            (
                "The probe should be installed first.\n\n"
                f"Current controller-reported dispenser type: {current}\n"
                "Probing requires dispenser type: Probe"
            ),
            QMessageBox.StandardButton.Ok,
        )
        return False

    def home_stage(self) -> None:
        self.send_synchronized_motion("V5", require_homed=False)

    def prepare_probe(self) -> None:
        self.send_synchronized_motion("V3 Z", require_homed=False)

    def manual_probe(self, probe_option: str) -> None:
        if not self.ensure_probe_ready():
            return
        self.send_synchronized_motion(f"V4 {probe_option}")

    def handle_print_circle_button(self) -> None:
        if self.print_circle_editing:
            self.start_circle_print()
            return
        self.enter_circle_print_editing()

    def enter_circle_print_editing(self) -> None:
        if self.serial_thread is None:
            self.append_log("! not connected")
            return
        if self.motion_busy or self.height_map_active or self.printing_active:
            self.append_log("! circle print blocked: waiting for active motion")
            return
        if not self.height_map_points:
            QMessageBox.warning(
                self,
                "Height Map Required",
                "Create or load a height map before printing.",
                QMessageBox.StandardButton.Ok,
            )
            return

        self.print_circle_editing = True
        self.stage_view.set_print_circle_editing(True)
        self.print_circle_button.setText("Start")
        self.print_status_label.setText("Edit circle")
        self.update_control_states()

    def start_circle_print(self) -> None:
        if self.serial_thread is None:
            self.append_log("! not connected")
            return
        if self.motion_busy or self.height_map_active or self.printing_active:
            self.append_log("! circle print blocked: waiting for active motion")
            return
        if not self.height_map_points:
            QMessageBox.warning(
                self,
                "Height Map Required",
                "Create or load a height map before printing.",
                QMessageBox.StandardButton.Ok,
            )
            return

        circle = self.stage_view.print_circle()
        if circle is None:
            self.append_log("! no print circle is defined")
            return

        self.print_circle_editing = False
        self.stage_view.set_print_circle_editing(False)
        self.print_circle_button.setText("Print Circle")
        self.printing_active = True
        self.print_preparing = True
        self.pending_print_circle = circle
        self.motion_busy = True
        self.print_status_label.setText("Preparing print tool")
        self.append_log("# circle print preparing tool")
        self.send_payload("V3 Z", force=True)
        self.send_payload("M400", force=True)
        self.update_control_states()

    def queue_prepared_circle_print(self) -> None:
        circle = self.pending_print_circle
        if circle is None:
            self.cancel_printing("Print failed")
            self.motion_busy = False
            self.append_log("! no pending circle print after preparation")
            self.update_control_states()
            return

        commands = self.build_circle_print_commands(circle)
        if not commands:
            self.cancel_printing("Print failed")
            self.motion_busy = False
            self.append_log("! no circle print commands were generated")
            self.update_control_states()
            return

        self.print_preparing = False
        self.pending_print_circle = None
        self.print_status_label.setText("Printing circle")
        self.append_log(f"# circle print queued: {len(commands)} commands")
        for payload in commands:
            self.send_payload(payload, force=True)
        self.update_control_states()

    def build_circle_print_commands(
        self, circle: tuple[float, float, float]
    ) -> list[str]:
        center_x, center_y, radius = circle
        speed = self.print_speed_spin.value()
        print_height = self.print_height_spin.value()
        travel_height = self.print_travel_height_spin.value()
        kick = self.print_kick_spin.value() / 1000.0
        retract = self.print_retract_spin.value() / 1000.0
        max_length = self.print_max_length_spin.value()

        commands: list[str] = []
        commands.append("V102")
        first_trace_chunk = True
        for chunk in self.circle_print_chunks(center_x, center_y, radius, max_length):
            if len(chunk) < 2:
                continue
            start_x, start_y = chunk[0]
            end_x, end_y = chunk[-1]
            start_z = self.interpolate_height(start_x, start_y)
            end_z = self.interpolate_height(end_x, end_y)

            if first_trace_chunk:
                commands.append(f"V1 X{start_x:.6f} Y{start_y:.6f}")
                first_trace_chunk = False
            commands.append(f"V102 Z{travel_height:g}")
            commands.append(f"V1 Z{start_z:.6f} D F{speed:g}")
            commands.append(
                f"V1 X{start_x:.6f} Y{start_y:.6f} Z{start_z:.6f} D F{speed:g}"
            )
            commands.append(f"V102 Z{print_height:g}")
            commands.append(
                f"V1 X{start_x:.6f} Y{start_y:.6f} Z{start_z:.6f} D F{speed:g}"
            )
            if kick:
                commands.append(f"V1 E{kick:g} F{speed:g}")

            for x, y in chunk[1:]:
                z = self.interpolate_height(x, y)
                commands.append(f"V1 X{x:.6f} Y{y:.6f} Z{z:.6f} D F{speed:g}")

            if retract:
                commands.append(f"V1 E{-retract:g} F{speed:g}")
            commands.append("V102")
            commands.append(f"V102 Z{travel_height:g}")
            commands.append(f"V1 Z{end_z:.6f} D F{speed:g}")
            commands.append("V102")

        commands.append("V102")
        commands.extend(self.return_to_z_switch_commands())
        commands.append("M400")
        return commands

    def return_to_z_switch_commands(self) -> list[str]:
        if self.maximum_z_position is None:
            return ["V3 Z"]
        return_z = self.maximum_z_position - MAXIMUM_Z_RETURN_MARGIN_MM
        return [
            f"V1 Z{return_z:.6f}",
            f"V1 X{Z_SWITCH_X_MM:.6f} Y{Z_SWITCH_Y_MM:.6f}",
        ]

    def circle_print_chunks(
        self,
        center_x: float,
        center_y: float,
        radius: float,
        max_length: float,
    ) -> list[list[tuple[float, float]]]:
        circumference = 2.0 * math.pi * radius
        chunk_count = max(1, math.ceil(circumference / max_length))
        chunks: list[list[tuple[float, float]]] = []
        for chunk_index in range(chunk_count):
            start_angle = 2.0 * math.pi * chunk_index / chunk_count
            end_angle = 2.0 * math.pi * (chunk_index + 1) / chunk_count
            arc_length = radius * (end_angle - start_angle)
            segment_count = max(
                1,
                math.ceil(arc_length / CIRCLE_PRINT_SEGMENT_MM),
            )
            chunk: list[tuple[float, float]] = []
            for segment_index in range(segment_count + 1):
                angle = start_angle + (
                    end_angle - start_angle
                ) * segment_index / segment_count
                chunk.append(
                    (
                        center_x + math.cos(angle) * radius,
                        center_y + math.sin(angle) * radius,
                    )
                )
            chunks.append(chunk)
        return chunks

    def interpolate_height(self, x: float, y: float) -> float:
        if not self.height_map_points:
            raise ValueError("height map is empty")

        weighted_sum = 0.0
        weight_total = 0.0
        for point_x, point_y, height in self.height_map_points:
            distance = math.hypot(x - point_x, y - point_y)
            if distance <= 1e-9:
                return height
            weight = 1.0 / (distance * distance)
            weighted_sum += height * weight
            weight_total += weight
        return weighted_sum / weight_total

    def finish_printing(self) -> None:
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle = None
        self.motion_busy = False
        self.stage_view.clear_print_circle()
        self.print_status_label.setText("Print complete")
        self.update_control_states()

    def cancel_printing(self, status: str) -> None:
        if not self.printing_active and not self.print_circle_editing:
            return
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle = None
        self.print_circle_editing = False
        self.stage_view.clear_print_circle()
        self.print_circle_button.setText("Print Circle")
        self.print_status_label.setText(status)

    def save_height_map(self) -> None:
        if self.height_map_active or self.printing_active or self.print_circle_editing:
            self.append_log("! cannot save height map while motion/editing is active")
            return
        if not self.height_map_points:
            QMessageBox.warning(
                self,
                "No Height Map",
                "There are no height-map points to save.",
                QMessageBox.StandardButton.Ok,
            )
            return

        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Height Map",
            "height_map.json",
            "MyVolt Height Map (*.json);;All Files (*)",
        )
        if not path:
            return
        if "." not in path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]:
            path += ".json"

        data = {
            "version": 1,
            "stage": {
                "x_max_mm": STAGE_X_MAX_MM,
                "y_max_mm": STAGE_Y_MAX_MM,
                "user_y_min_mm": USER_STAGE_Y_MIN_MM,
            },
            "work_area": self.height_map_work_area_data(),
            "points": [
                {"x": x, "y": y, "z": z}
                for x, y, z in self.height_map_points
            ],
        }

        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not save height map:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(f"! failed to save height map: {exc}")
            return

        self.append_log(f"# saved height map: {path}")

    def load_height_map(self) -> None:
        if (
            self.motion_busy
            or self.height_map_active
            or self.printing_active
            or self.print_circle_editing
        ):
            self.append_log("! cannot load height map while motion/editing is active")
            return

        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Height Map",
            "",
            "MyVolt Height Map (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            points = self.parse_height_map_points(data)
            work_area = self.parse_height_map_work_area(data)
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.critical(
                self,
                "Load Failed",
                f"Could not load height map:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(f"! failed to load height map: {exc}")
            return

        self.height_map_points = points
        self.height_map_plan = []
        self.height_map_index = 0
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_probe_phase = None
        if work_area is not None:
            self.stage_view.set_work_area(*work_area)
        self.stage_view.set_height_map_points(self.height_map_points)
        self.height_map_status_label.setText(self.height_map_summary())
        self.append_log(f"# loaded height map: {path}")
        self.update_control_states()

    def height_map_work_area_data(self) -> dict[str, float]:
        x, y, width, height = self.stage_view.work_area()
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    def parse_height_map_work_area(
        self, data
    ) -> tuple[float, float, float, float] | None:
        if not isinstance(data, dict):
            return None
        work_area = data.get("work_area")
        if not isinstance(work_area, dict):
            return None
        values = (
            float(work_area["x"]),
            float(work_area["y"]),
            float(work_area["width"]),
            float(work_area["height"]),
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("height-map work_area contains non-finite values")
        return values

    def parse_height_map_points(self, data) -> list[tuple[float, float, float]]:
        if not isinstance(data, dict):
            raise ValueError("height-map file must contain a JSON object")
        raw_points = data.get("points")
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError("height-map file has no points")

        points: list[tuple[float, float, float]] = []
        for index, point in enumerate(raw_points, start=1):
            if isinstance(point, dict):
                x = float(point["x"])
                y = float(point["y"])
                z = float(point["z"])
            elif isinstance(point, list) and len(point) >= 3:
                x = float(point[0])
                y = float(point[1])
                z = float(point[2])
            else:
                raise ValueError(f"height-map point {index} is invalid")

            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError(
                    f"height-map point {index} contains non-finite values"
                )
            if not (0.0 <= x <= STAGE_X_MAX_MM and 0.0 <= y <= STAGE_Y_MAX_MM):
                raise ValueError(f"height-map point {index} is outside the stage")
            points.append((x, y, z))

        return points

    def delete_height_map(self) -> None:
        if self.height_map_active or self.printing_active or self.print_circle_editing:
            self.append_log("! cannot delete height map while motion/editing is active")
            return
        self.reset_height_map()
        self.update_control_states()

    def start_height_map_probing(self) -> None:
        if self.serial_thread is None:
            self.append_log("! not connected")
            return
        if self.motion_busy or self.height_map_active or self.print_circle_editing:
            self.append_log("! height map blocked: waiting for active motion")
            return
        if not self.all_axes_homed:
            self.warn_not_homed()
            return
        if not self.ensure_probe_ready():
            return

        self.height_map_plan = self.build_height_map_plan()
        if not self.height_map_plan:
            self.append_log("! height map has no probe points")
            return

        self.height_map_points = []
        self.height_map_index = 0
        self.height_map_active = True
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_probe_phase = None
        self.motion_busy = True
        self.stage_view.set_height_map_points([])
        self.height_map_status_label.setText(
            f"Probing 0/{len(self.height_map_plan)}"
        )
        self.append_log(
            f"# height map probing started: {len(self.height_map_plan)} points"
        )
        self.update_control_states()

        self.send_payload("V201 E1", force=True)
        self.queue_next_height_map_point()

    def build_height_map_plan(self) -> list[tuple[float, float]]:
        x, y, width, height = self.stage_view.work_area()
        x_count = self.height_map_x_points_spin.value()
        y_count = self.height_map_y_points_spin.value()
        xs = [
            x + width * index / (x_count - 1)
            for index in range(x_count)
        ]
        ys = [
            y + height * index / (y_count - 1)
            for index in range(y_count)
        ]
        return [(point_x, point_y) for point_y in ys for point_x in xs]

    def queue_next_height_map_point(self) -> None:
        if not self.height_map_active:
            return

        if self.height_map_index >= len(self.height_map_plan):
            self.finish_height_map_sequence()
            return

        x, y = self.height_map_plan[self.height_map_index]
        point_number = self.height_map_index + 1
        point_id = f"heightmap_{point_number}"
        probe_option = "R1" if self.height_map_index == 0 else "R0.1"
        self.height_map_probe_phase = (
            "coarse" if self.height_map_index == 0 else "fine"
        )
        self.height_map_waiting_for_probe = True
        self.height_map_status_label.setText(
            f"{self.height_map_probe_phase.title()} "
            f"{self.height_map_index}/{len(self.height_map_plan)}"
        )
        self.send_payload(f"V1 X{x:.6f} Y{y:.6f}", force=True)
        self.send_payload("M400", force=True)
        self.send_payload(f"V4 {probe_option} I{point_id}", force=True)

    def record_height_map_probe(self, probe) -> None:
        if not self.height_map_active or not self.height_map_waiting_for_probe:
            return

        if self.height_map_probe_phase == "coarse":
            self.append_log(
                "# height map coarse point 1: "
                f"X{probe.x:.4f} Y{probe.y:.4f} Z{probe.z:.6f}"
            )
            self.height_map_probe_phase = "fine"
            self.height_map_status_label.setText(
                f"Fine {self.height_map_index}/{len(self.height_map_plan)}"
            )
            self.send_payload("V4 R0.1 Iheightmap_1", force=True)
            return

        self.height_map_waiting_for_probe = False
        self.height_map_points.append((probe.x, probe.y, probe.z))
        self.stage_view.set_height_map_points(self.height_map_points)
        self.height_map_index += 1
        self.height_map_probe_phase = None
        self.height_map_status_label.setText(
            f"Probing {self.height_map_index}/{len(self.height_map_plan)}"
        )
        self.append_log(
            "# height map point "
            f"{self.height_map_index}/{len(self.height_map_plan)}: "
            f"X{probe.x:.4f} Y{probe.y:.4f} Z{probe.z:.6f}"
        )
        self.queue_next_height_map_point()

    def finish_height_map_sequence(self) -> None:
        self.height_map_waiting_for_probe = False
        self.height_map_probe_phase = None
        self.height_map_finishing = True
        self.height_map_status_label.setText(
            f"Finishing {len(self.height_map_points)}/{len(self.height_map_plan)}"
        )
        self.send_payload("V201 E0", force=True)
        for payload in self.return_to_z_switch_commands():
            self.send_payload(payload, force=True)
        self.send_payload("M400", force=True)

    def finish_height_map(self) -> None:
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_probe_phase = None
        self.motion_busy = False
        self.height_map_status_label.setText(self.height_map_summary())
        self.append_log(f"# height map completed: {self.height_map_summary()}")
        self.update_control_states()

    def height_map_summary(self) -> str:
        if not self.height_map_points:
            return "No height map"
        heights = [height for _x, _y, height in self.height_map_points]
        return (
            f"{len(self.height_map_points)} points, "
            f"Z {min(heights):.4f}..{max(heights):.4f}"
        )

    def cancel_height_map(self, status: str) -> None:
        if not self.height_map_active and not self.height_map_finishing:
            return
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_probe_phase = None
        self.height_map_status_label.setText(status)

    def emergency_stop(self) -> None:
        self.cancel_height_map("Height map stopped")
        self.cancel_printing("Print stopped")
        self.motion_busy = False
        if self.serial_thread is not None:
            self.serial_thread.clear_pending()
        self.send_payload("M18", urgent=True)
        self.update_control_states()

    def send_raw_payload(self) -> None:
        payload = self.raw_input.text().strip()
        if not payload:
            return
        if payload.startswith("N") and "*" in payload:
            self.append_log("! enter an unframed payload, not a full N... command")
            return
        if self.motion_busy:
            self.append_log("! raw command blocked: waiting for M400 synchronization")
            return
        command = self.raw_payload_command(payload)
        if command == "V4" and not self.ensure_probe_ready():
            return
        if self.raw_payload_is_motion(payload):
            sent = self.send_synchronized_motion(payload)
        else:
            sent = self.send_payload(payload)
        if sent:
            self.raw_input.clear()

    def set_temperature(self) -> None:
        target = self.temp_spin.value()
        if target <= 0:
            self.send_payload("M142")
        else:
            duration = self.heat_duration_spin.value()
            self.send_payload(f"M141 T{target:g} D{duration}")

    def move_to_stage_position(self, x: float, y: float) -> None:
        self.send_synchronized_motion(f"V1 X{x:.6f} Y{y:.6f}")

    def jog_xy(self, dx: float, dy: float) -> None:
        self.send_synchronized_motion(f"V2 X{dx:g} Y{dy:g}")

    def jog_z(self, dz: float) -> None:
        if self.current_z is None:
            self.append_log("! cannot jog Z before receiving a positionUpdate")
            return
        target = self.current_z + dz
        self.send_synchronized_motion(f"V1 Z{target:.6f}")

    def jog_e(self, de: float) -> None:
        self.send_synchronized_motion(f"V1 E{de:g}")

    def on_received_line(self, line: str) -> None:
        self.append_log(f"< {line}")
        maximum_z = parse_maximum_z_position(line)
        if maximum_z is not None:
            self.maximum_z_position = maximum_z
            self.append_log(f"# maximum Z position: {maximum_z:.6f} mm")

        self.update_motion_sync(line)
        self.update_tool_offset_from_line(line)

        position = parse_position(line)
        if position is not None:
            self.current_x = position.x
            self.current_y = position.y
            self.current_z = position.z
            self.current_e = position.e
            self.x_label.setText(f"X: {position.x:.3f}")
            self.y_label.setText(f"Y: {position.y:.3f}")
            self.z_label.setText(f"Z: {position.z:.3f}")
            self.e_label.setText(f"E: {position.e:.4f}")
            self.stage_view.set_position(
                position.x,
                position.y,
                position.z,
                self.all_axes_homed,
            )
            return

        temperature = parse_temperature(line)
        if temperature is not None:
            self.temp_label.setText(
                f"Temp: {temperature.current:.1f} / {temperature.target:.1f} C"
            )
            return

        probe = parse_probe_measurement(line)
        if probe is not None:
            self.probe_label.setText(f"Probe: Z {probe.z:.4f}")
            self.record_height_map_probe(probe)
            return

        homed = parse_homed_status(line)
        if homed is not None:
            self.all_axes_homed = homed.all_homed
            if homed.all_homed:
                self.homed_label.setText("HOMED")
                self.homed_label.setStyleSheet("color: #137333; font-weight: 700;")
            else:
                axes = "/".join(homed.unhomed_axes)
                suffix = "axis" if len(homed.unhomed_axes) == 1 else "axes"
                self.homed_label.setText(f"{axes} {suffix} NOT HOMED")
                self.homed_label.setStyleSheet("color: #b00020; font-weight: 700;")
            self.stage_view.set_homed(self.all_axes_homed)
            self.update_control_states()
            return

        tool = parse_tool_status(line)
        if tool is not None:
            self.current_tool_type = tool.tool_type
            self.maximum_z_position = None
            self.clear_tool_offset_for_tool(tool.tool_type)
            if tool.version is None:
                self.tool_label.setText(f"Dispenser: {tool.tool_type}")
            else:
                self.tool_label.setText(f"Dispenser: {tool.tool_type} v{tool.version}")

    def on_error_line(self, line: str) -> None:
        self.motion_busy = False
        self.cancel_height_map("Height map failed")
        self.cancel_printing("Print failed")
        self.preparation_in_progress = False
        self.awaiting_z_switch_measurement = False
        self.pending_tool_offset = None
        self.update_control_states()
        self.error_label.setText(f"ERROR: {line}")
        self.append_log(f"! {line}")

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
        self.stop_alignment_cameras()
        if self.serial_thread is not None:
            self.serial_thread.stop()
            self.serial_thread.wait(1500)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
