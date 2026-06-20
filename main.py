from __future__ import annotations

import json
import math
from pathlib import Path
import queue
import re
import sys
import time

from PySide6.QtCore import QLineF, QRectF, QSize, QSizeF, QThread, QTimer, Qt, Signal
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
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSlider,
    QStatusBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices
except ImportError:
    QCamera = None
    QMediaCaptureSession = None
    QMediaDevices = None

try:
    from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
except ImportError:
    QGraphicsVideoItem = None

try:
    from PySide6.QtMultimediaWidgets import QVideoWidget
except ImportError:
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
PCS_AXIS_LENGTH_MM = 10.0
WORKFLOW_COMPLETE_STYLE = "color: #137333; font-weight: 700;"
WORKFLOW_PENDING_STYLE = "color: black; font-weight: 500;"
MIN_PRINT_CIRCLE_RADIUS_MM = 1.0
CIRCLE_PRINT_SEGMENT_MM = 1.0
SERIAL_COMMAND_INTERVAL_S = 0.01
DEFAULT_STAY_ALIVE_POLL_SECONDS = 60
STAY_ALIVE_PAYLOAD = "M400"
MAXIMUM_Z_RETURN_MARGIN_MM = 0.5
DEFAULT_HEIGHT_MAP_MAX_DEVIATION_WARNING_MM = 0.5
CAMERA_CONFIG_PATH = Path("camera.json")
ANCHOR_CONFIG_PATH = Path("anchors.json")
CONN_CONFIG_PATH = Path("conn.json")
LEVELING_CONFIG_PATH = Path("leveling.json")
DOTPRINTING_CONFIG_PATH = Path("dotprinting.json")
OPTIONS_CONFIG_PATH = Path("options.json")
DEFAULT_PATTERN_ALIGNMENT_HEIGHT_MM = 2.0
DEFAULT_ALIGNMENT_WORK_AREA_MARGIN_MM = 2.0
DEFAULT_ALIGNMENT_COARSE_XY_STEP_MM = 1.0
DEFAULT_ALIGNMENT_FINE_XY_STEP_MM = 0.05
ALIGNMENT_POINT_LABELS = ("1", "2", "3", "4")
CAMERA_EXPOSURE_MIN_EV = -4.0
CAMERA_EXPOSURE_MAX_EV = 4.0
CAMERA_EXPOSURE_SCALE = 10
CAMERA_VIEW_BASE_WIDTH_PX = 220
CAMERA_VIEW_MIN_WIDTH_PX = 150
CAMERA_VIEW_ZOOM_MIN = 1.0
CAMERA_VIEW_ZOOM_MAX = 8.0
CAMERA_VIEW_ZOOM_STEP = 1.2
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


class OptionsDialog(QDialog):
    def __init__(
        self,
        stay_alive_poll_seconds: int,
        auto_margin_enabled: bool,
        auto_margin_mm: float,
        height_map_max_deviation_mm: float,
        use_four_point_alignment: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setModal(True)

        self.stay_alive_spin = QSpinBox()
        self.auto_margin_check = QCheckBox("Enable")
        self.auto_margin_spin = QDoubleSpinBox()
        self.height_map_deviation_spin = QDoubleSpinBox()
        self.four_point_alignment_check = QCheckBox("Use 4-point alignment")

        self._build_ui(
            stay_alive_poll_seconds,
            auto_margin_enabled,
            auto_margin_mm,
            height_map_max_deviation_mm,
            use_four_point_alignment,
        )

    def _build_ui(
        self,
        stay_alive_poll_seconds: int,
        auto_margin_enabled: bool,
        auto_margin_mm: float,
        height_map_max_deviation_mm: float,
        use_four_point_alignment: bool,
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        form = QFormLayout()

        self.stay_alive_spin.setRange(1, 3600)
        self.stay_alive_spin.setValue(stay_alive_poll_seconds)
        self.stay_alive_spin.setSuffix(" s")
        form.addRow("Stay alive polling period", self.stay_alive_spin)

        margin_widget = QWidget()
        margin_layout = QHBoxLayout(margin_widget)
        margin_layout.setContentsMargins(0, 0, 0, 0)
        margin_layout.setSpacing(6)
        self.auto_margin_check.setChecked(auto_margin_enabled)
        self.auto_margin_spin.setRange(0.0, 100.0)
        self.auto_margin_spin.setDecimals(3)
        self.auto_margin_spin.setValue(auto_margin_mm)
        self.auto_margin_spin.setSuffix(" mm")
        self.auto_margin_spin.setEnabled(auto_margin_enabled)
        self.auto_margin_check.toggled.connect(self.auto_margin_spin.setEnabled)
        margin_layout.addWidget(self.auto_margin_check)
        margin_layout.addWidget(self.auto_margin_spin, stretch=1)
        form.addRow("Auto margin for height map", margin_widget)

        self.height_map_deviation_spin.setRange(0.0, 100.0)
        self.height_map_deviation_spin.setDecimals(3)
        self.height_map_deviation_spin.setValue(height_map_max_deviation_mm)
        self.height_map_deviation_spin.setSuffix(" mm")
        form.addRow("Height map warning deviation", self.height_map_deviation_spin)

        self.four_point_alignment_check.setChecked(use_four_point_alignment)
        form.addRow("", self.four_point_alignment_check)

        layout.addLayout(form)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Cancel")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        layout.addLayout(button_layout)

    def stay_alive_poll_seconds(self) -> int:
        return self.stay_alive_spin.value()

    def auto_margin_enabled(self) -> bool:
        return self.auto_margin_check.isChecked()

    def auto_margin_mm(self) -> float:
        return self.auto_margin_spin.value()

    def height_map_max_deviation_mm(self) -> float:
        return self.height_map_deviation_spin.value()

    def use_four_point_alignment(self) -> bool:
        return self.four_point_alignment_check.isChecked()


class RotatableCameraView(QGraphicsView):
    rotation_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._video_item = QGraphicsVideoItem()
        self._rotation_degrees = 0
        self._zoom_factor = CAMERA_VIEW_ZOOM_MIN
        self._native_size = QSizeF(320.0, 180.0)
        self._video_item.setSize(self._native_size)
        self._scene.addItem(self._video_item)
        self.setScene(self._scene)
        self.setMinimumWidth(CAMERA_VIEW_MIN_WIDTH_PX)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.setStyleSheet("background: #202124; border: 1px solid #5f6368;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._connect_native_size_signal()
        self._apply_rotation()

    def video_output(self):
        return self._video_item

    def rotation_degrees(self) -> int:
        return self._rotation_degrees

    def set_rotation_degrees(self, degrees: int, notify: bool = False) -> None:
        degrees = degrees % 360
        if self._rotation_degrees == degrees:
            return
        self._rotation_degrees = degrees
        self._apply_rotation()
        if notify:
            self.rotation_changed.emit(self._rotation_degrees)

    def rotate_view(self, delta_degrees: int) -> None:
        self.set_rotation_degrees(self._rotation_degrees + delta_degrees, notify=True)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        rotate_ccw_action = menu.addAction("Rotate View CCW")
        rotate_cw_action = menu.addAction("Rotate View CW")
        selected_action = menu.exec(event.globalPos())
        if selected_action == rotate_ccw_action:
            self.rotate_view(-90)
        elif selected_action == rotate_cw_action:
            self.rotate_view(90)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_video()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        old_zoom = self._zoom_factor
        steps = delta / 120.0
        new_zoom = clamp(
            old_zoom * (CAMERA_VIEW_ZOOM_STEP ** steps),
            CAMERA_VIEW_ZOOM_MIN,
            CAMERA_VIEW_ZOOM_MAX,
        )
        if abs(new_zoom - old_zoom) <= 1e-9:
            event.accept()
            return

        self._zoom_factor = new_zoom
        self.scale(new_zoom / old_zoom, new_zoom / old_zoom)
        event.accept()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        video_size = self._rotated_video_size()
        if video_size.width() <= 0.0:
            return super().heightForWidth(width)
        frame = self.frameWidth() * 2
        content_width = max(1, width - frame)
        content_height = round(
            content_width * video_size.height() / video_size.width()
        )
        return max(1, content_height + frame)

    def sizeHint(self) -> QSize:
        video_size = self._rotated_video_size()
        width = min(
            max(round(video_size.width()), CAMERA_VIEW_MIN_WIDTH_PX),
            CAMERA_VIEW_BASE_WIDTH_PX,
        )
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(
            CAMERA_VIEW_MIN_WIDTH_PX,
            self.heightForWidth(CAMERA_VIEW_MIN_WIDTH_PX),
        )

    def _apply_rotation(self) -> None:
        rect = self._video_item.boundingRect()
        self._video_item.setTransformOriginPoint(rect.center())
        self._video_item.setRotation(self._rotation_degrees)
        self._scene.setSceneRect(self._video_item.sceneBoundingRect())
        self.updateGeometry()
        self._fit_video()

    def _fit_video(self) -> None:
        scene_rect = self._scene.sceneRect()
        if scene_rect.isEmpty():
            return
        self.resetTransform()
        self.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        if abs(self._zoom_factor - 1.0) > 1e-9:
            self.scale(self._zoom_factor, self._zoom_factor)

    def _connect_native_size_signal(self) -> None:
        try:
            self._video_item.nativeSizeChanged.connect(self._set_native_size)
        except AttributeError:
            pass

    def _set_native_size(self, size: QSizeF) -> None:
        if size.width() <= 0.0 or size.height() <= 0.0:
            return
        self._native_size = QSizeF(size)
        self._video_item.setSize(self._native_size)
        self._apply_rotation()

    def _rotated_video_size(self) -> QSizeF:
        if self._rotation_degrees % 180 == 0:
            return self._native_size
        return QSizeF(self._native_size.height(), self._native_size.width())


class AlignmentProcedureDialog(QDialog):
    def __init__(
        self,
        controller,
        mode: str,
        default_anchors: list[tuple[float, float]],
        point_count: int,
        default_alignment_height: float,
        default_coarse_xy_step: float,
        default_fine_xy_step: float,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.mode = mode
        self.point_count = point_count
        self.step_labels: list[QLabel] = []
        self.anchor_spins: list[QDoubleSpinBox] = []
        self.alignment_height_spin = QDoubleSpinBox()
        self.coarse_xy_step_spin = QDoubleSpinBox()
        self.fine_xy_step_spin = QDoubleSpinBox()
        self.using_fine_jog_step = False
        self.jog_step_label = QLabel("")
        self.setWindowTitle("Alignment Procedure")
        self.setModal(False)
        self._build_ui(
            default_anchors,
            default_alignment_height,
            default_coarse_xy_step,
            default_fine_xy_step,
        )

    def _build_ui(
        self,
        default_anchors: list[tuple[float, float]],
        default_alignment_height: float,
        default_coarse_xy_step: float,
        default_fine_xy_step: float,
    ) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = "Click & Align" if self.mode == "click" else "Align to Anchors"
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(title_label)

        if self.mode == "anchors":
            layout.addWidget(self._build_anchor_group(default_anchors))

        layout.addWidget(
            self._build_settings_group(
                default_alignment_height,
                default_coarse_xy_step,
                default_fine_xy_step,
            )
        )
        layout.addWidget(self._build_flow_group())
        layout.addWidget(self._build_jog_group())

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        action_layout = QHBoxLayout()
        action_layout.addStretch(1)
        self.primary_button = QPushButton("Start")
        self.cancel_button = QPushButton("Cancel")
        action_layout.addWidget(self.primary_button)
        action_layout.addWidget(self.cancel_button)
        layout.addLayout(action_layout)

        self.primary_button.clicked.connect(
            self.controller.handle_alignment_dialog_primary
        )
        self.cancel_button.clicked.connect(
            lambda: self.controller.cancel_pattern_alignment("Alignment cancelled")
        )
        self.set_current_step(0)

    def _build_anchor_group(
        self,
        default_anchors: list[tuple[float, float]],
    ) -> QGroupBox:
        group = QGroupBox("Anchor PCS Positions")
        layout = QGridLayout(group)
        layout.addWidget(QLabel(""), 0, 0)
        layout.addWidget(QLabel("X"), 0, 1)
        layout.addWidget(QLabel("Y"), 0, 2)

        for row, (x_value, y_value) in enumerate(default_anchors, start=1):
            x_spin = self._build_anchor_spin(x_value)
            y_spin = self._build_anchor_spin(y_value)
            self.anchor_spins.extend([x_spin, y_spin])
            layout.addWidget(QLabel(f"Anchor {row}"), row, 0)
            layout.addWidget(x_spin, row, 1)
            layout.addWidget(y_spin, row, 2)

        return group

    def _build_anchor_spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-10000.0, 10000.0)
        spin.setDecimals(6)
        spin.setValue(value)
        spin.setSuffix(" mm")
        return spin

    def _build_settings_group(
        self,
        default_alignment_height: float,
        default_coarse_xy_step: float,
        default_fine_xy_step: float,
    ) -> QGroupBox:
        group = QGroupBox("Settings")
        layout = QFormLayout(group)
        self.alignment_height_spin.setRange(0.0, 20.0)
        self.alignment_height_spin.setDecimals(3)
        self.alignment_height_spin.setValue(default_alignment_height)
        self.alignment_height_spin.setSuffix(" mm")
        layout.addRow("Align Z", self.alignment_height_spin)
        self._configure_xy_step_spin(
            self.coarse_xy_step_spin,
            default_coarse_xy_step,
        )
        self._configure_xy_step_spin(
            self.fine_xy_step_spin,
            default_fine_xy_step,
        )
        layout.addRow("Coarse XY step", self.coarse_xy_step_spin)
        layout.addRow("Fine XY step", self.fine_xy_step_spin)
        return group

    def _configure_xy_step_spin(
        self, spin: QDoubleSpinBox, default_value: float
    ) -> None:
        spin.setRange(0.001, 50.0)
        spin.setDecimals(3)
        spin.setValue(default_value)
        spin.setSuffix(" mm")

    def _build_flow_group(self) -> QGroupBox:
        group = QGroupBox("Workflow")
        layout = QVBoxLayout(group)
        layout.setSpacing(3)

        steps = []
        for index in range(self.point_count):
            label = ALIGNMENT_POINT_LABELS[index]
            if self.mode == "click":
                steps.extend(
                    [
                        f"Select point {label}",
                        f"Go to point {label}",
                        f"Fine-align point {label}",
                    ]
                )
            else:
                steps.extend(
                    [
                        f"Go to anchor {label}",
                        f"Fine-align anchor {label}",
                    ]
                )
        for index, step in enumerate(steps):
            label = QLabel(step)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(24)
            self.step_labels.append(label)
            layout.addWidget(label)
            if index < len(steps) - 1:
                arrow = QLabel("v")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(arrow)
        return group

    def _build_jog_group(self) -> QGroupBox:
        group = QGroupBox("XY Jog")
        layout = QGridLayout(group)
        self.jog_step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.jog_step_label, 0, 0, 1, 3)
        self._add_jog_pad(layout, 1)
        self.coarse_xy_step_spin.valueChanged.connect(
            lambda _value: self.update_jog_step_label()
        )
        self.fine_xy_step_spin.valueChanged.connect(
            lambda _value: self.update_jog_step_label()
        )
        self.update_jog_step_label()
        return group

    def _add_jog_pad(
        self,
        layout: QGridLayout,
        row: int,
    ) -> None:
        y_plus = QPushButton("Y+")
        x_minus = QPushButton("X-")
        x_plus = QPushButton("X+")
        y_minus = QPushButton("Y-")
        layout.addWidget(y_plus, row, 1)
        layout.addWidget(x_minus, row + 1, 0)
        layout.addWidget(x_plus, row + 1, 2)
        layout.addWidget(y_minus, row + 2, 1)
        x_minus.clicked.connect(lambda: self._jog_xy(-1.0, 0.0))
        x_plus.clicked.connect(lambda: self._jog_xy(1.0, 0.0))
        y_minus.clicked.connect(lambda: self._jog_xy(0.0, -1.0))
        y_plus.clicked.connect(lambda: self._jog_xy(0.0, 1.0))

    def _jog_xy(self, x_direction: float, y_direction: float) -> None:
        self.controller.alignment_dialog_jog_xy(x_direction, y_direction)

    def anchor_points(self) -> list[tuple[float, float]]:
        points = []
        for index in range(0, len(self.anchor_spins), 2):
            points.append(
                (
                    self.anchor_spins[index].value(),
                    self.anchor_spins[index + 1].value(),
                )
            )
        return points

    def alignment_height(self) -> float:
        return self.alignment_height_spin.value()

    def coarse_xy_step(self) -> float:
        return self.coarse_xy_step_spin.value()

    def fine_xy_step(self) -> float:
        return self.fine_xy_step_spin.value()

    def active_xy_step(self) -> float:
        if self.using_fine_jog_step:
            return self.fine_xy_step()
        return self.coarse_xy_step()

    def set_jog_step_mode(self, use_fine_step: bool) -> None:
        self.using_fine_jog_step = use_fine_step
        self.update_jog_step_label()

    def update_jog_step_label(self) -> None:
        mode = "Fine" if self.using_fine_jog_step else "Coarse"
        self.jog_step_label.setText(f"{mode} step: {self.active_xy_step():.3f} mm")

    def set_anchor_inputs_enabled(self, enabled: bool) -> None:
        for spin in self.anchor_spins:
            spin.setEnabled(enabled)

    def set_current_step(self, index: int | None) -> None:
        for step_index, label in enumerate(self.step_labels):
            if index is not None and step_index == index:
                label.setStyleSheet(
                    "background: #fff3cd; border: 1px solid #f0b429; "
                    "font-weight: 700; padding: 3px;"
                )
            else:
                label.setStyleSheet(
                    "background: #f1f3f4; border: 1px solid #dadce0; "
                    "padding: 3px;"
                )

    def set_primary_action(self, text: str, enabled: bool = True) -> None:
        self.primary_button.setText(text)
        self.primary_button.setEnabled(enabled)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)


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
        if is_error_line(line):
            self.clear_pending()
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
    pattern_point_selected = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._x: float | None = None
        self._y: float | None = None
        self._z: float | None = None
        self._homed = False
        self._work_area = DEFAULT_WORK_AREA
        self._height_map_points: list[tuple[float, float, float]] = []
        self._height_map_preview_points: list[tuple[float, float]] = []
        self._print_circle: tuple[float, float, float] | None = None
        self._pattern_points: list[tuple[int, float, float]] = []
        self._pattern_alignment_points: list[tuple[float, float]] = []
        self._pcs_axis_points: tuple[
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
        ] | None = None
        self._pattern_select_enabled = False
        self._selected_pattern_index: int | None = None
        self._hovered_pattern_index: int | None = None
        self._pattern_print_states: dict[int, str] = {}
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
        old_work_area = self._work_area
        self._work_area = self._clamp_work_area(x, y, width, height)
        self._constrain_print_circle()
        self.update()
        if self._work_area != old_work_area:
            self.work_area_changed.emit(*self._work_area)

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

    def set_height_map_preview_points(
        self, points: list[tuple[float, float]]
    ) -> None:
        self._height_map_preview_points = list(points)
        self.update()

    def set_pattern_points(self, points: list[tuple[int, float, float]]) -> None:
        self._pattern_points = list(points)
        valid_indices = {index for index, _x, _y in self._pattern_points}
        self._pattern_print_states = {
            index: state
            for index, state in self._pattern_print_states.items()
            if index in valid_indices
        }
        self.update()

    def set_pattern_alignment_points(
        self, points: list[tuple[float, float]]
    ) -> None:
        self._pattern_alignment_points = list(points)
        self.update()

    def set_pcs_axis_points(
        self,
        points: tuple[
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
        ] | None,
    ) -> None:
        self._pcs_axis_points = points
        self.update()

    def clear_pattern_points(self) -> None:
        self._pattern_points = []
        self._pattern_alignment_points = []
        self._pcs_axis_points = None
        self._pattern_select_enabled = False
        self._selected_pattern_index = None
        self._hovered_pattern_index = None
        self._pattern_print_states = {}
        self.update()

    def set_pattern_selection_enabled(self, enabled: bool) -> None:
        self._pattern_select_enabled = enabled
        if not enabled:
            self._set_hovered_pattern_index(None)
        self._update_cursor()

    def set_selected_pattern_index(self, index: int | None) -> None:
        self._selected_pattern_index = index
        self.update()

    def set_pattern_print_states(self, states: dict[int, str]) -> None:
        self._pattern_print_states = dict(states)
        self.update()

    def visible_stage_bottom_left(self) -> tuple[float, float]:
        viewport = self._stage_viewport_rect()
        return self._map_widget_point(
            viewport.left(),
            viewport.bottom(),
            self._stage_rect(),
        )

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

        if self._pattern_select_enabled:
            point_index = self._pattern_point_hit(position.x(), position.y(), bounds)
            if point_index is not None:
                self._selected_pattern_index = point_index
                self.pattern_point_selected.emit(point_index)
                self.update()
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

        if self._pattern_select_enabled:
            hovered_index = None
            if viewport.contains(position) and bounds.contains(position):
                hovered_index = self._pattern_point_hit(
                    position.x(), position.y(), bounds
                )
            self._set_hovered_pattern_index(hovered_index)
            self.setCursor(Qt.CursorShape.CrossCursor)
            super().mouseMoveEvent(event)
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
        self._draw_pattern_points(painter, bounds)
        self._draw_height_map_preview_points(painter, bounds)
        self._draw_height_map_points(painter, bounds)
        self._draw_pcs_axes(painter, bounds)
        self._draw_pattern_alignment_points(painter, bounds)
        self._draw_print_circle(painter, bounds)
        self._draw_probe_regions(painter, bounds)
        self._draw_boundary(painter, bounds)
        self._draw_position_cross(painter, bounds)
        painter.restore()
        self._draw_height_colorbar(painter, bounds)

    def leaveEvent(self, event) -> None:
        self._set_hovered_pattern_index(None)
        super().leaveEvent(event)

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

    def _draw_pattern_points(self, painter: QPainter, bounds: QRectF) -> None:
        if not self._pattern_points:
            return

        radius = 3.8
        for index, x, y in self._pattern_points:
            if not (0.0 <= x <= STAGE_X_MAX_MM and 0.0 <= y <= STAGE_Y_MAX_MM):
                continue
            px, py = self._map_stage_point(x, y, bounds)
            is_hovered = index == self._hovered_pattern_index
            is_selected = index == self._selected_pattern_index
            print_state = self._pattern_print_states.get(index)
            point_radius = radius + 1.2 if is_hovered else radius
            if print_state == "printing":
                fill_color = QColor("#ffd54f")
            elif print_state == "printed":
                fill_color = QColor("#43a047")
            elif is_hovered:
                fill_color = QColor("#ffb300")
            else:
                fill_color = QColor("#00a7b5")
            pen_color = QColor("#d81b60") if is_selected else QColor("#1b1f23")
            pen_width = 2 if is_selected else 1
            painter.setPen(QPen(pen_color, pen_width))
            painter.setBrush(fill_color)
            painter.drawEllipse(
                QRectF(
                    px - point_radius,
                    py - point_radius,
                    point_radius * 2,
                    point_radius * 2,
                )
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_height_map_preview_points(
        self, painter: QPainter, bounds: QRectF
    ) -> None:
        if not self._height_map_preview_points:
            return

        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.setPen(QPen(QColor("#455a64"), 1))
        radius = 2.6
        for x, y in self._height_map_preview_points:
            if not (0.0 <= x <= STAGE_X_MAX_MM and 0.0 <= y <= STAGE_Y_MAX_MM):
                continue
            px, py = self._map_stage_point(x, y, bounds)
            painter.drawEllipse(
                QRectF(px - radius, py - radius, radius * 2, radius * 2)
            )

    def _draw_pattern_alignment_points(
        self, painter: QPainter, bounds: QRectF
    ) -> None:
        if not self._pattern_alignment_points:
            return

        painter.setPen(QPen(QColor("#d50000"), 2))
        size = 7.0
        for x, y in self._pattern_alignment_points:
            if not (0.0 <= x <= STAGE_X_MAX_MM and 0.0 <= y <= STAGE_Y_MAX_MM):
                continue
            px, py = self._map_stage_point(x, y, bounds)
            painter.drawLine(QLineF(px - size, py - size, px + size, py + size))
            painter.drawLine(QLineF(px - size, py + size, px + size, py - size))

    def _draw_pcs_axes(self, painter: QPainter, bounds: QRectF) -> None:
        if self._pcs_axis_points is None:
            return

        origin, x_axis, y_axis = self._pcs_axis_points
        if not all(
            math.isfinite(value)
            for point in (origin, x_axis, y_axis)
            for value in point
        ):
            return

        origin_px = self._map_stage_point_unclamped(*origin, bounds)
        x_px = self._map_stage_point_unclamped(*x_axis, bounds)
        y_px = self._map_stage_point_unclamped(*y_axis, bounds)
        color = QColor("#0b8f3a")
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.setPen(QPen(color, 2.4))
        self._draw_arrow(painter, origin_px, x_px)
        self._draw_arrow(painter, origin_px, y_px)

        painter.setPen(QPen(color, 1))
        label_size = QRectF(0.0, 0.0, 42.0, 18.0)
        painter.drawText(
            label_size.translated(x_px[0] + 4.0, x_px[1] - 9.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "PCS X",
        )
        painter.drawText(
            label_size.translated(y_px[0] + 4.0, y_px[1] - 9.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "PCS Y",
        )

    def _draw_arrow(
        self,
        painter: QPainter,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> None:
        start_x, start_y = start
        end_x, end_y = end
        if math.hypot(end_x - start_x, end_y - start_y) <= 1.0:
            return

        painter.drawLine(QLineF(start_x, start_y, end_x, end_y))
        angle = math.atan2(end_y - start_y, end_x - start_x)
        head_length = 9.0
        head_angle = math.radians(28.0)
        for sign in (-1.0, 1.0):
            head_x = end_x - head_length * math.cos(angle + sign * head_angle)
            head_y = end_y - head_length * math.sin(angle + sign * head_angle)
            painter.drawLine(QLineF(end_x, end_y, head_x, head_y))

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

    def _map_stage_point_unclamped(
        self, x: float, y: float, bounds: QRectF
    ) -> tuple[float, float]:
        px = bounds.left() + (1.0 - x / STAGE_X_MAX_MM) * bounds.width()
        py = bounds.top() + (y / STAGE_Y_MAX_MM) * bounds.height()
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

    def _pattern_point_hit(
        self, px: float, py: float, bounds: QRectF
    ) -> int | None:
        tolerance = 9.0
        best_index = None
        best_distance = tolerance
        for index, x, y in self._pattern_points:
            if not (0.0 <= x <= STAGE_X_MAX_MM and 0.0 <= y <= STAGE_Y_MAX_MM):
                continue
            point_x, point_y = self._map_stage_point(x, y, bounds)
            distance = math.hypot(px - point_x, py - point_y)
            if distance <= best_distance:
                best_index = index
                best_distance = distance
        return best_index

    def _set_hovered_pattern_index(self, index: int | None) -> None:
        if self._hovered_pattern_index == index:
            return
        self._hovered_pattern_index = index
        self.update()

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
        if self._pattern_select_enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
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
        self.height_map_abort_requested = False
        self.height_map_probe_phase: str | None = None
        self.height_map_completed = False
        self.pattern_points: list[tuple[float, float]] = []
        self.pattern_file_path: str | None = None
        self.pattern_work_offset_x = 0.0
        self.pattern_work_offset_y = 0.0
        self.pattern_transform_a11 = -1.0
        self.pattern_transform_a12 = 0.0
        self.pattern_transform_a21 = 0.0
        self.pattern_transform_a22 = -1.0
        self.pattern_rotation_deg = 0.0
        self.pattern_scale = 1.0
        self.pattern_scale_x = 1.0
        self.pattern_scale_y = 1.0
        self.pattern_orthogonality_error_deg = 0.0
        self.pattern_alignment_height = DEFAULT_PATTERN_ALIGNMENT_HEIGHT_MM
        self.alignment_coarse_xy_step = DEFAULT_ALIGNMENT_COARSE_XY_STEP_MM
        self.alignment_fine_xy_step = DEFAULT_ALIGNMENT_FINE_XY_STEP_MM
        self.use_four_point_alignment = False
        self.pattern_alignment_mode: str | None = None
        self.pattern_alignment_state: str | None = None
        self.pattern_alignment_first: tuple[int, float, float, float, float] | None = None
        self.pattern_alignment_pending_index: int | None = None
        self.pattern_alignment_point_count = 2
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_anchor_points: list[tuple[float, float]] | None = None
        self.pattern_alignment_anchor_first: tuple[float, float, float, float] | None = None
        self.pattern_alignment_reference_points: list[tuple[float, float]] = []
        self.pattern_alignment_measurements: list[
            tuple[float, float, float, float]
        ] = []
        self.pattern_alignment_completed = False
        self.alignment_procedure_dialog: AlignmentProcedureDialog | None = None
        self.pattern_print_active = False
        self.pending_pattern_print_points: list[tuple[int, float, float]] | None = None
        self.pattern_print_dot_states: dict[int, str] = {}
        self.pattern_print_command_events: list[tuple[str, int] | None] = []
        self.pattern_print_total_dots = 0
        self.print_circle_editing = False
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle: tuple[float, float, float] | None = None
        self.all_axes_homed = False
        self.motion_busy = False
        self.serial_connected = False
        self.general_command_widgets: list[QWidget] = []
        self.setup_motion_widgets: list[QWidget] = []
        self.homed_motion_widgets: list[QWidget] = []
        self.saved_serial_port = ""
        self.stay_alive_enabled = False
        self.stay_alive_poll_seconds = DEFAULT_STAY_ALIVE_POLL_SECONDS
        self.auto_height_map_margin_enabled = True
        self.auto_height_map_margin_mm = DEFAULT_ALIGNMENT_WORK_AREA_MARGIN_MM
        self.height_map_max_deviation_warning_mm = (
            DEFAULT_HEIGHT_MAP_MAX_DEVIATION_WARNING_MM
        )
        self.port_refreshing = False
        self.camera_config: dict[str, object] = {}
        self.camera_exposure_compensation = 0.0
        self.camera_view_rotations = [0, 0]
        self.pattern_anchor_defaults: list[tuple[float, float]] | None = None
        self.camera_devices_by_id = {}
        self.alignment_cameras = [None, None]
        self.alignment_camera_sessions = [None, None]
        self.alignment_camera_combos: list[QComboBox] = []
        self.alignment_video_outputs: list[QWidget] = []
        self.alignment_refreshing = False
        self.leveling_config_loading = False
        self.dotprinting_config_loading = False

        self.port_combo = QComboBox()
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)
        self.stay_alive_check = QCheckBox("Stay alive")
        self.stay_alive_timer = QTimer(self)
        self.apply_stay_alive_poll_interval()

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
        self.load_options_config()
        self.load_conn_config()
        self.load_anchor_config()
        self.load_leveling_config()
        self.load_dotprinting_config()
        self.load_camera_config()
        self.refresh_alignment_cameras()
        self.refresh_ports()
        self.initialize_pattern_transform_to_visible_bottom_left()
        self.update_height_map_preview()
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
        left_panel.addWidget(self._build_workflow_group())
        self.stage_control_group = self._build_stage_control_group()
        self.stage_control_group.setVisible(False)
        left_panel.addWidget(self.stage_control_group)
        left_panel.addWidget(self._build_temperature_group())
        left_panel.addWidget(self._build_raw_log_group(), stretch=1)

        center_panel = QVBoxLayout()
        center_panel.setSpacing(6)
        center_panel.addWidget(self._build_stage_group(), stretch=1)
        center_panel.addWidget(self._build_alignment_group())
        center_panel.addWidget(self._build_raw_payload_group())

        right_panel = QVBoxLayout()
        right_panel.setSpacing(6)
        right_panel.addWidget(self._build_pattern_group())
        right_panel.addWidget(self._build_leveling_group())
        self.printing_group = self._build_printing_group()
        self.printing_group.setVisible(False)
        right_panel.addWidget(self.printing_group)
        right_panel.addStretch(1)

        workspace_layout.addLayout(left_panel, stretch=0)
        workspace_layout.addLayout(center_panel, stretch=1)
        workspace_layout.addLayout(right_panel, stretch=0)
        main_layout.addLayout(workspace_layout, stretch=1)

        self.setCentralWidget(root)
        self._build_menu_bar()
        self._build_status_bar()

    def _build_menu_bar(self) -> None:
        options_action = self.menuBar().addAction("Options...")
        options_action.triggered.connect(self.show_options_dialog)

    def show_options_dialog(self) -> None:
        dialog = OptionsDialog(
            self.stay_alive_poll_seconds,
            self.auto_height_map_margin_enabled,
            self.auto_height_map_margin_mm,
            self.height_map_max_deviation_warning_mm,
            self.use_four_point_alignment,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self.stay_alive_poll_seconds = dialog.stay_alive_poll_seconds()
        self.auto_height_map_margin_enabled = dialog.auto_margin_enabled()
        self.auto_height_map_margin_mm = dialog.auto_margin_mm()
        self.height_map_max_deviation_warning_mm = (
            dialog.height_map_max_deviation_mm()
        )
        self.use_four_point_alignment = dialog.use_four_point_alignment()
        self.apply_stay_alive_poll_interval()
        self.save_options_config()
        self.append_log("# options updated")

    def _build_raw_payload_group(self) -> QGroupBox:
        group = QGroupBox("Raw Payload")
        layout = QHBoxLayout(group)
        self.raw_input = QLineEdit()
        self.raw_input.setPlaceholderText("Example: V1 X0 Y0")
        self.raw_send_button = QPushButton("Send")
        layout.addWidget(self.raw_input, stretch=1)
        layout.addWidget(self.raw_send_button)
        self.general_command_widgets.extend([self.raw_input, self.raw_send_button])
        return group

    def _build_raw_log_group(self) -> QGroupBox:
        group = QGroupBox("Raw Log")
        group.setMaximumWidth(360)
        layout = QVBoxLayout(group)
        layout.addWidget(self.log_view)
        return group

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
        layout.addWidget(self.stay_alive_check, 2, 1, 1, 4)
        layout.addWidget(self.home_button, 3, 1, 1, 2)
        layout.addWidget(self.prepare_probe_button, 3, 3, 1, 2)
        layout.addWidget(self.emergency_button, 4, 1, 1, 4)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.setup_motion_widgets.extend([self.home_button, self.prepare_probe_button])
        return group

    def _build_workflow_group(self) -> QGroupBox:
        group = QGroupBox("Workflow")
        group.setMaximumWidth(360)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.workflow_status_labels: dict[str, QLabel] = {}
        for key, text in (
            ("connected", "Connected"),
            ("homed", "Stage Homed"),
            ("height_map", "Height map completed"),
            ("aligned", "Aligned"),
            ("pattern", "Pattern Loaded"),
            ("dispenser", "Dispensor Loaded"),
        ):
            label = QLabel(text)
            label.setStyleSheet(WORKFLOW_PENDING_STYLE)
            self.workflow_status_labels[key] = label
            layout.addWidget(label)
        return group

    def _build_temperature_group(self) -> QGroupBox:
        group = QGroupBox("Temperature")
        group.setMaximumWidth(360)
        layout = QGridLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)
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
        layout.addWidget(QLabel("Target"), 0, 0)
        layout.addWidget(self.temp_spin, 0, 1)
        layout.addWidget(QLabel("Duration"), 0, 2)
        layout.addWidget(self.heat_duration_spin, 0, 3)
        layout.addWidget(self.set_temp_button, 1, 0, 1, 2)
        layout.addWidget(self.stop_heat_button, 1, 2, 1, 2)
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
        button_widget = QWidget()
        button_layout = QVBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(4)
        button_layout.addWidget(self.delete_height_map_button)
        button_layout.addWidget(self.save_height_map_button)
        button_layout.addWidget(self.load_height_map_button)
        button_layout.addWidget(self.start_height_map_button)
        for button in (
            self.delete_height_map_button,
            self.save_height_map_button,
            self.load_height_map_button,
            self.start_height_map_button,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )

        layout.addRow("Probe offset", self.probe_tool_offset_label)
        layout.addRow("Dispenser offset", self.dispenser_tool_offset_label)
        layout.addRow("Probe points", point_count_layout)
        layout.addRow("Height map", self.height_map_status_label)
        layout.addRow(button_widget)
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
        layout.addWidget(self.alignment_exposure_value_label, 4, 1)
        layout.addWidget(self.alignment_exposure_slider, 5, 0, 1, 2)
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
        self.connect_alignment_view_rotation_signals()
        return group

    def _build_camera_view(self) -> QWidget:
        if QGraphicsVideoItem is not None:
            return RotatableCameraView()

        if QVideoWidget is not None:
            view = QVideoWidget()
            view.setMinimumSize(CAMERA_VIEW_MIN_WIDTH_PX, 90)
            return view

        label = QLabel("Qt Multimedia unavailable")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumSize(CAMERA_VIEW_MIN_WIDTH_PX, 90)
        label.setStyleSheet(
            "background: #202124; color: #f1f3f4; border: 1px solid #5f6368;"
        )
        return label

    def connect_alignment_view_rotation_signals(self) -> None:
        for slot, view in enumerate(self.alignment_video_outputs):
            rotation_changed = getattr(view, "rotation_changed", None)
            if rotation_changed is not None:
                rotation_changed.connect(
                    lambda degrees, camera_slot=slot: (
                        self.on_alignment_view_rotation_changed(
                            camera_slot,
                            degrees,
                        )
                    )
                )

    def _build_pattern_group(self) -> QGroupBox:
        group = QGroupBox("Pattern")
        group.setMaximumWidth(360)
        layout = QFormLayout(group)

        self.pattern_load_button = QPushButton("Load")
        self.pattern_align_button = QPushButton("Click && Align")
        self.pattern_anchor_align_button = QPushButton("Align to Anchors")
        self.pattern_reset_button = QPushButton("Reset")
        self.pattern_save_alignment_button = QPushButton("Save Alignment")
        self.pattern_load_alignment_button = QPushButton("Load Alignment")
        self.pattern_print_button = QPushButton("Print Dots")

        action_layout = QHBoxLayout()
        action_layout.addWidget(self.pattern_load_button)
        action_layout.addWidget(self.pattern_reset_button)
        alignment_mode_layout = QHBoxLayout()
        alignment_mode_layout.addWidget(self.pattern_align_button)
        alignment_mode_layout.addWidget(self.pattern_anchor_align_button)
        alignment_file_layout = QHBoxLayout()
        alignment_file_layout.addWidget(self.pattern_save_alignment_button)
        alignment_file_layout.addWidget(self.pattern_load_alignment_button)

        self.pattern_stats_label = QLabel("No pattern")
        self.pattern_transform_label = QLabel("Offset: --\nRotation: --\nScale: --")
        self.pattern_status_label = QLabel("Idle")

        self.pattern_print_height_spin = QDoubleSpinBox()
        self.pattern_print_height_spin.setRange(0.0, 10.0)
        self.pattern_print_height_spin.setDecimals(3)
        self.pattern_print_height_spin.setValue(0.15)
        self.pattern_print_height_spin.setSuffix(" mm")

        self.pattern_kick_spin = QDoubleSpinBox()
        self.pattern_kick_spin.setRange(0.0, 5000.0)
        self.pattern_kick_spin.setDecimals(0)
        self.pattern_kick_spin.setValue(200.0)
        self.pattern_kick_spin.setSuffix(" um")

        self.pattern_retract_spin = QDoubleSpinBox()
        self.pattern_retract_spin.setRange(0.0, 5000.0)
        self.pattern_retract_spin.setDecimals(0)
        self.pattern_retract_spin.setValue(200.0)
        self.pattern_retract_spin.setSuffix(" um")

        self.pattern_travel_height_spin = QDoubleSpinBox()
        self.pattern_travel_height_spin.setRange(0.0, 20.0)
        self.pattern_travel_height_spin.setDecimals(3)
        self.pattern_travel_height_spin.setValue(2.0)
        self.pattern_travel_height_spin.setSuffix(" mm")

        self.pattern_lifting_speed_spin = QDoubleSpinBox()
        self.pattern_lifting_speed_spin.setRange(0.01, 1000.0)
        self.pattern_lifting_speed_spin.setDecimals(2)
        self.pattern_lifting_speed_spin.setValue(1.0)
        self.pattern_lifting_speed_spin.setSuffix(" mm/s")

        layout.addRow("", action_layout)
        layout.addRow("", alignment_mode_layout)
        layout.addRow("", alignment_file_layout)
        layout.addRow("Stats", self.pattern_stats_label)
        layout.addRow("Transform", self.pattern_transform_label)
        layout.addRow("Print height", self.pattern_print_height_spin)
        layout.addRow("Kick", self.pattern_kick_spin)
        layout.addRow("Retract", self.pattern_retract_spin)
        layout.addRow("Travel height", self.pattern_travel_height_spin)
        layout.addRow("Lifting speed", self.pattern_lifting_speed_spin)
        layout.addRow("Status", self.pattern_status_label)
        layout.addRow("", self.pattern_print_button)
        return group

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
        self.port_combo.currentIndexChanged.connect(
            lambda _index: self.on_port_selection_changed()
        )
        self.connect_button.clicked.connect(self.connect_serial)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.stay_alive_check.toggled.connect(self.on_stay_alive_toggled)
        self.stay_alive_timer.timeout.connect(self.send_stay_alive_query)
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
        self.height_map_x_points_spin.valueChanged.connect(
            lambda _value: self.on_leveling_settings_changed()
        )
        self.height_map_y_points_spin.valueChanged.connect(
            lambda _value: self.on_leveling_settings_changed()
        )
        self.print_circle_button.clicked.connect(self.handle_print_circle_button)
        self.pattern_load_button.clicked.connect(self.load_pattern)
        self.pattern_align_button.clicked.connect(self.handle_pattern_align_button)
        self.pattern_anchor_align_button.clicked.connect(
            self.handle_pattern_anchor_align_button
        )
        self.pattern_reset_button.clicked.connect(self.reset_pattern_alignment)
        self.pattern_save_alignment_button.clicked.connect(self.save_pattern_alignment)
        self.pattern_load_alignment_button.clicked.connect(self.load_pattern_alignment)
        self.pattern_print_button.clicked.connect(self.on_pattern_print_button_clicked)
        for spin in (
            self.pattern_print_height_spin,
            self.pattern_kick_spin,
            self.pattern_retract_spin,
            self.pattern_travel_height_spin,
            self.pattern_lifting_speed_spin,
        ):
            spin.valueChanged.connect(lambda _value: self.save_dotprinting_config())
        self.stage_view.pattern_point_selected.connect(self.on_pattern_point_selected)
        self.stage_view.work_area_changed.connect(
            lambda _x, _y, _width, _height: self.on_leveling_settings_changed()
        )
        self.raw_send_button.clicked.connect(self.send_raw_payload)
        self.raw_input.returnPressed.connect(self.send_raw_payload)

    def refresh_ports(self) -> None:
        current = self.port_combo.currentData() or self.saved_serial_port
        self.port_refreshing = True
        try:
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
        finally:
            self.port_refreshing = False

    def apply_stay_alive_poll_interval(self) -> None:
        interval_ms = max(1, int(self.stay_alive_poll_seconds)) * 1000
        self.stay_alive_timer.setInterval(interval_ms)

    def load_options_config(self) -> None:
        if not OPTIONS_CONFIG_PATH.exists():
            self.apply_stay_alive_poll_interval()
            return

        try:
            with OPTIONS_CONFIG_PATH.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"! failed to load options config: {exc}")
            self.apply_stay_alive_poll_interval()
            return

        if not isinstance(data, dict):
            self.append_log("! failed to load options config: expected JSON object")
            self.apply_stay_alive_poll_interval()
            return

        try:
            stay_alive_seconds = data.get("stay_alive_poll_seconds")
            if stay_alive_seconds is not None:
                value = float(stay_alive_seconds)
                if not math.isfinite(value):
                    raise ValueError("stay_alive_poll_seconds must be finite")
                self.stay_alive_poll_seconds = int(
                    clamp(round(value), 1, 3600)
                )

            auto_margin = data.get("auto_height_map_margin", {})
            if auto_margin is None:
                auto_margin = {}
            if isinstance(auto_margin, dict):
                self.auto_height_map_margin_enabled = bool(
                    auto_margin.get(
                        "enabled",
                        self.auto_height_map_margin_enabled,
                    )
                )
                margin_value = auto_margin.get(
                    "margin_mm",
                    data.get("auto_height_map_margin_mm"),
                )
            else:
                margin_value = data.get("auto_height_map_margin_mm")
            if margin_value is not None:
                value = float(margin_value)
                if not math.isfinite(value):
                    raise ValueError("auto height-map margin must be finite")
                self.auto_height_map_margin_mm = clamp(value, 0.0, 100.0)

            deviation_value = data.get("height_map_max_deviation_warning_mm")
            if deviation_value is not None:
                value = float(deviation_value)
                if not math.isfinite(value):
                    raise ValueError("height-map warning deviation must be finite")
                self.height_map_max_deviation_warning_mm = clamp(
                    value,
                    0.0,
                    100.0,
                )
            if "use_four_point_alignment" in data:
                self.use_four_point_alignment = bool(
                    data["use_four_point_alignment"]
                )
        except (TypeError, ValueError) as exc:
            self.append_log(f"! failed to parse options config: {exc}")

        self.apply_stay_alive_poll_interval()

    def save_options_config(self) -> None:
        data = {
            "version": 1,
            "stay_alive_poll_seconds": self.stay_alive_poll_seconds,
            "auto_height_map_margin": {
                "enabled": self.auto_height_map_margin_enabled,
                "margin_mm": self.auto_height_map_margin_mm,
            },
            "height_map_max_deviation_warning_mm": (
                self.height_map_max_deviation_warning_mm
            ),
            "use_four_point_alignment": self.use_four_point_alignment,
        }
        try:
            with OPTIONS_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save options config: {exc}")

    def load_conn_config(self) -> None:
        self.saved_serial_port = ""
        self.stay_alive_enabled = False
        if not CONN_CONFIG_PATH.exists():
            return

        try:
            with CONN_CONFIG_PATH.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"! failed to load connection config: {exc}")
            return

        if isinstance(data, dict):
            port = str(data.get("port", "") or "")
            self.stay_alive_enabled = bool(data.get("stay_alive", False))
        else:
            port = str(data or "")
        self.saved_serial_port = port
        if hasattr(self, "stay_alive_check"):
            self.stay_alive_check.blockSignals(True)
            self.stay_alive_check.setChecked(self.stay_alive_enabled)
            self.stay_alive_check.blockSignals(False)
            self.update_stay_alive_timer()

    def save_conn_config(self, port: str | None = None) -> None:
        port = str(port or self.port_combo.currentData() or self.saved_serial_port or "")
        if not port:
            return

        self.saved_serial_port = port
        data = {
            "version": 1,
            "port": port,
            "stay_alive": self.stay_alive_enabled,
        }
        try:
            with CONN_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save connection config: {exc}")

    def on_stay_alive_toggled(self, checked: bool) -> None:
        self.stay_alive_enabled = checked
        self.save_conn_config()
        self.update_stay_alive_timer()

    def update_stay_alive_timer(self) -> None:
        if self.stay_alive_enabled and self.serial_thread is not None:
            if not self.stay_alive_timer.isActive():
                self.stay_alive_timer.start()
        else:
            self.stay_alive_timer.stop()

    def send_stay_alive_query(self) -> None:
        if not self.stay_alive_enabled or self.serial_thread is None:
            self.update_stay_alive_timer()
            return
        if self.motion_busy or self.height_map_active or self.printing_active:
            return
        if not self.serial_thread.is_idle():
            return
        self.send_payload(STAY_ALIVE_PAYLOAD)

    def on_port_selection_changed(self) -> None:
        if self.port_refreshing:
            return
        port = self.port_combo.currentData()
        if port:
            self.save_conn_config(str(port))

    def load_anchor_config(self) -> None:
        if not ANCHOR_CONFIG_PATH.exists():
            return

        try:
            with ANCHOR_CONFIG_PATH.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"! failed to load anchor config: {exc}")
            return

        try:
            if isinstance(data, dict) and "alignment_z" in data:
                self.pattern_alignment_height = clamp(
                    float(data["alignment_z"]),
                    0.0,
                    20.0,
                )
            if isinstance(data, dict) and "coarse_xy_step" in data:
                self.alignment_coarse_xy_step = clamp(
                    float(data["coarse_xy_step"]),
                    0.001,
                    50.0,
                )
            if isinstance(data, dict) and "fine_xy_step" in data:
                self.alignment_fine_xy_step = clamp(
                    float(data["fine_xy_step"]),
                    0.001,
                    50.0,
                )
            if self.anchor_config_has_points(data):
                self.pattern_anchor_defaults = self.parse_anchor_config_data(data)
        except (KeyError, TypeError, ValueError) as exc:
            self.append_log(f"! failed to parse anchor config: {exc}")

    def anchor_config_has_points(self, data) -> bool:
        if isinstance(data, list):
            return len(data) >= 2
        if not isinstance(data, dict):
            return False
        if "anchors" in data:
            return self.anchor_config_has_points(data["anchors"])
        return "anchor_1" in data and "anchor_2" in data

    def parse_anchor_config_data(
        self, data
    ) -> list[tuple[float, float]]:
        if isinstance(data, dict):
            if "anchors" in data:
                data = data["anchors"]
            else:
                points = []
                for index in range(1, 5):
                    key = f"anchor_{index}"
                    if key in data:
                        points.append(self.parse_anchor_point_data(data[key]))
                if len(points) >= 2:
                    return points

        if isinstance(data, list) and len(data) >= 2:
            return [self.parse_anchor_point_data(point) for point in data]

        raise ValueError("expected at least two anchor points")

    def parse_anchor_point_data(self, data) -> tuple[float, float]:
        if isinstance(data, dict):
            return (float(data["x"]), float(data["y"]))
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            return (float(data[0]), float(data[1]))
        raise ValueError("expected anchor point with x and y")

    def save_anchor_config(
        self,
        anchor_points: list[tuple[float, float]] | None = None,
        alignment_height: float | None = None,
        coarse_xy_step: float | None = None,
        fine_xy_step: float | None = None,
    ) -> None:
        if anchor_points is not None:
            self.pattern_anchor_defaults = anchor_points
        if alignment_height is not None:
            self.pattern_alignment_height = clamp(alignment_height, 0.0, 20.0)
        if coarse_xy_step is not None:
            self.alignment_coarse_xy_step = clamp(coarse_xy_step, 0.001, 50.0)
        if fine_xy_step is not None:
            self.alignment_fine_xy_step = clamp(fine_xy_step, 0.001, 50.0)

        data = {
            "version": 1,
            "alignment_z": self.pattern_alignment_height,
            "coarse_xy_step": self.alignment_coarse_xy_step,
            "fine_xy_step": self.alignment_fine_xy_step,
        }
        if self.pattern_anchor_defaults is not None:
            data["anchors"] = [
                {"x": x, "y": y}
                for x, y in self.pattern_anchor_defaults
            ]
        try:
            with ANCHOR_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save anchor config: {exc}")

    def save_anchor_config_from_dialog(self) -> None:
        dialog = self.alignment_procedure_dialog
        if dialog is None:
            return
        anchor_points = dialog.anchor_points() if dialog.mode == "anchors" else None
        self.save_anchor_config(
            anchor_points,
            dialog.alignment_height(),
            dialog.coarse_xy_step(),
            dialog.fine_xy_step(),
        )

    def on_alignment_anchor_inputs_changed(self) -> None:
        self.save_anchor_config_from_dialog()
        dialog = self.alignment_procedure_dialog
        if dialog is None or dialog.mode != "anchors":
            return
        self.set_pattern_alignment_reference_points(list(dialog.anchor_points()))

    def load_leveling_config(self) -> None:
        if not LEVELING_CONFIG_PATH.exists():
            return

        try:
            with LEVELING_CONFIG_PATH.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"! failed to load leveling config: {exc}")
            return

        self.leveling_config_loading = True
        try:
            x_points, y_points = self.parse_leveling_probe_counts(data)
            if x_points is not None:
                self.height_map_x_points_spin.setValue(x_points)
            if y_points is not None:
                self.height_map_y_points_spin.setValue(y_points)

            work_area = self.parse_height_map_work_area(data)
            if work_area is not None:
                self.stage_view.set_work_area(*work_area)
        except (KeyError, TypeError, ValueError) as exc:
            self.append_log(f"! failed to parse leveling config: {exc}")
        finally:
            self.leveling_config_loading = False

        self.update_height_map_preview()

    def parse_leveling_probe_counts(
        self, data
    ) -> tuple[int | None, int | None]:
        if not isinstance(data, dict):
            raise ValueError("leveling config must contain a JSON object")

        raw_counts = data.get("probe_points", {})
        if raw_counts is None:
            raw_counts = {}
        if not isinstance(raw_counts, dict):
            raise ValueError("probe_points must contain x and y counts")

        x_value = raw_counts.get("x", data.get("x_points"))
        y_value = raw_counts.get("y", data.get("y_points"))
        return (
            self.clamp_leveling_probe_count(
                x_value,
                self.height_map_x_points_spin,
            ),
            self.clamp_leveling_probe_count(
                y_value,
                self.height_map_y_points_spin,
            ),
        )

    def clamp_leveling_probe_count(
        self, value, spin: QSpinBox
    ) -> int | None:
        if value is None:
            return None
        count_value = float(value)
        if not math.isfinite(count_value):
            raise ValueError("probe point count must be finite")
        count = int(round(count_value))
        return int(clamp(count, spin.minimum(), spin.maximum()))

    def save_leveling_config(self) -> None:
        if self.leveling_config_loading:
            return
        if not hasattr(self, "height_map_x_points_spin"):
            return

        data = {
            "version": 1,
            "probe_points": {
                "x": self.height_map_x_points_spin.value(),
                "y": self.height_map_y_points_spin.value(),
            },
            "work_area": self.height_map_work_area_data(),
        }
        try:
            with LEVELING_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save leveling config: {exc}")

    def on_leveling_settings_changed(self) -> None:
        self.update_height_map_preview()
        self.save_leveling_config()

    def load_dotprinting_config(self) -> None:
        if not DOTPRINTING_CONFIG_PATH.exists():
            return

        try:
            with DOTPRINTING_CONFIG_PATH.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            self.append_log(f"! failed to load dot printing config: {exc}")
            return

        if not isinstance(data, dict):
            self.append_log("! failed to load dot printing config: expected JSON object")
            return

        self.dotprinting_config_loading = True
        try:
            self.apply_dotprinting_spin_value(
                data,
                "print_height_mm",
                self.pattern_print_height_spin,
            )
            self.apply_dotprinting_spin_value(
                data,
                "kick_um",
                self.pattern_kick_spin,
            )
            self.apply_dotprinting_spin_value(
                data,
                "retract_um",
                self.pattern_retract_spin,
            )
            self.apply_dotprinting_spin_value(
                data,
                "travel_height_mm",
                self.pattern_travel_height_spin,
            )
            self.apply_dotprinting_spin_value(
                data,
                "lifting_speed_mm_s",
                self.pattern_lifting_speed_spin,
            )
        except (TypeError, ValueError) as exc:
            self.append_log(f"! failed to parse dot printing config: {exc}")
        finally:
            self.dotprinting_config_loading = False

    def apply_dotprinting_spin_value(
        self,
        data: dict,
        key: str,
        spin: QDoubleSpinBox,
    ) -> None:
        if key not in data:
            return
        value = float(data[key])
        if not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        spin.setValue(clamp(value, spin.minimum(), spin.maximum()))

    def save_dotprinting_config(self) -> None:
        if self.dotprinting_config_loading:
            return
        if not hasattr(self, "pattern_lifting_speed_spin"):
            return

        data = {
            "version": 1,
            "print_height_mm": self.pattern_print_height_spin.value(),
            "kick_um": self.pattern_kick_spin.value(),
            "retract_um": self.pattern_retract_spin.value(),
            "travel_height_mm": self.pattern_travel_height_spin.value(),
            "lifting_speed_mm_s": self.pattern_lifting_speed_spin.value(),
        }
        try:
            with DOTPRINTING_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save dot printing config: {exc}")

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
            self.camera_view_rotations = [
                self.parse_camera_rotation(data.get("view_1_rotation_degrees", 0)),
                self.parse_camera_rotation(data.get("view_2_rotation_degrees", 0)),
            ]
            self.camera_config = {
                "view_1": str(data.get("view_1", "") or ""),
                "view_2": str(data.get("view_2", "") or ""),
                "exposure_compensation": self.camera_exposure_compensation,
                "view_1_rotation_degrees": self.camera_view_rotations[0],
                "view_2_rotation_degrees": self.camera_view_rotations[1],
            }
            self.apply_alignment_camera_rotations()
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
        self.camera_view_rotations = [
            self.alignment_camera_view_rotation(0),
            self.alignment_camera_view_rotation(1),
        ]
        data = {
            "view_1": self.alignment_camera_combos[0].currentData() or "",
            "view_2": self.alignment_camera_combos[1].currentData() or "",
            "exposure_compensation": self.camera_exposure_compensation,
            "view_1_rotation_degrees": self.camera_view_rotations[0],
            "view_2_rotation_degrees": self.camera_view_rotations[1],
        }
        self.camera_config = dict(data)
        try:
            with CAMERA_CONFIG_PATH.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            self.append_log(f"! failed to save camera config: {exc}")

    def parse_camera_rotation(self, value) -> int:
        try:
            return round(float(value)) % 360
        except (TypeError, ValueError):
            return 0

    def alignment_camera_view_rotation(self, slot: int) -> int:
        if slot >= len(self.alignment_video_outputs):
            return self.camera_view_rotations[slot]
        rotation_getter = getattr(
            self.alignment_video_outputs[slot],
            "rotation_degrees",
            None,
        )
        if callable(rotation_getter):
            return int(rotation_getter()) % 360
        return self.camera_view_rotations[slot]

    def apply_alignment_camera_rotations(self) -> None:
        for slot, rotation in enumerate(self.camera_view_rotations):
            if slot >= len(self.alignment_video_outputs):
                continue
            rotation_setter = getattr(
                self.alignment_video_outputs[slot],
                "set_rotation_degrees",
                None,
            )
            if callable(rotation_setter):
                rotation_setter(rotation, notify=False)

    def on_alignment_view_rotation_changed(self, slot: int, degrees: int) -> None:
        self.camera_view_rotations[slot] = degrees % 360
        self.save_camera_config()

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
        session.setVideoOutput(
            self.alignment_camera_video_output(self.alignment_video_outputs[slot])
        )
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

    def alignment_camera_video_output(self, view: QWidget):
        output_getter = getattr(view, "video_output", None)
        if callable(output_getter):
            return output_getter()
        return view

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

        self.save_conn_config(str(port))
        self.current_x = None
        self.current_y = None
        self.current_z = None
        self.current_e = None
        self.current_tool_type = None
        self.maximum_z_position = None
        self.all_axes_homed = False
        self.serial_connected = False
        self.motion_busy = False
        self.reset_tool_offsets()
        self.reset_height_map()
        self.reset_printing_state()
        self.stage_view.clear_position()
        self.serial_thread = SerialThread(port=port)
        self.serial_thread.connected.connect(self.on_connected)
        self.serial_thread.disconnected.connect(self.on_disconnected)
        self.serial_thread.sent_line.connect(self.on_sent_line)
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
        self.serial_connected = True
        self.append_log(f"# connected to {port} at {baud} baud")
        self.statusBar().showMessage(f"Connected to {port} at {baud} baud", 5000)
        self.update_stay_alive_timer()
        self.update_control_states()

    def on_disconnected(self) -> None:
        self.append_log("# disconnected")
        self.serial_thread = None
        self.serial_connected = False
        self.update_stay_alive_timer()
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
        self.height_map_abort_requested = False
        self.height_map_probe_phase = None
        self.height_map_completed = False
        if hasattr(self, "stage_view"):
            self.stage_view.set_height_map_points([])
        if hasattr(self, "height_map_status_label"):
            self.height_map_status_label.setText("No height map")

    def reset_printing_state(self) -> None:
        self.print_circle_editing = False
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle = None
        self.pending_pattern_print_points = None
        self.pattern_print_active = False
        self.pattern_print_command_events = []
        self.reset_pattern_print_progress()
        if hasattr(self, "stage_view"):
            self.stage_view.clear_print_circle()
        if hasattr(self, "print_circle_button"):
            self.print_circle_button.setText("Print Circle")
        if hasattr(self, "print_status_label"):
            self.print_status_label.setText("No print queued")

    def load_pattern(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Pattern",
            "",
            "Pattern Files (*.csv *.json);;All Files (*)",
        )
        if not path:
            return

        try:
            points = self.parse_pattern_file(Path(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.critical(
                self,
                "Load Pattern Failed",
                f"Could not load pattern:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(f"! failed to load pattern: {exc}")
            return

        self.pattern_points = points
        self.pattern_file_path = path
        self.pattern_alignment_state = None
        self.pattern_alignment_mode = None
        self.pattern_alignment_first = None
        self.pattern_alignment_pending_index = None
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.stage_view.set_pattern_selection_enabled(False)
        self.stage_view.set_selected_pattern_index(None)
        self.reset_pattern_print_progress()
        self.update_pattern_display()
        self.pattern_status_label.setText(f"Loaded {Path(path).name}")
        self.update_control_states()

    def save_pattern_alignment(self) -> None:
        if (
            self.pattern_alignment_state is not None
            or self.motion_busy
            or self.height_map_active
            or self.printing_active
        ):
            self.append_log("! cannot save alignment while pattern motion is active")
            return

        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Pattern Alignment",
            "pattern_alignment.json",
            "MyVolt Pattern Alignment (*.json);;All Files (*)",
        )
        if not path:
            return
        if "." not in path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]:
            path += ".json"

        data = {
            "version": 2,
            "transform": self.pattern_alignment_data(),
            "alignment_points": self.pattern_alignment_reference_data(),
            "stage": {
                "x_max_mm": STAGE_X_MAX_MM,
                "y_max_mm": STAGE_Y_MAX_MM,
            },
        }
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)
                file.write("\n")
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not save alignment:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(f"! failed to save alignment: {exc}")
            return

        self.append_log(f"# saved pattern alignment: {path}")

    def load_pattern_alignment(self) -> None:
        if (
            self.pattern_alignment_state is not None
            or self.motion_busy
            or self.height_map_active
            or self.printing_active
        ):
            self.append_log("! cannot load alignment while motion is active")
            return

        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Pattern Alignment",
            "",
            "MyVolt Pattern Alignment (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            alignment = self.parse_pattern_alignment_data(data)
            alignment_measurements = self.parse_pattern_alignment_measurement_data(data)
            alignment_points = self.parse_pattern_alignment_reference_data(data)
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.critical(
                self,
                "Load Failed",
                f"Could not load alignment:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            self.append_log(f"! failed to load alignment: {exc}")
            return

        self.set_pattern_transform_matrix(
            alignment["a11"],
            alignment["a12"],
            alignment["a21"],
            alignment["a22"],
            alignment["offset_x"],
            alignment["offset_y"],
            update_display=False,
        )
        self.pattern_alignment_state = None
        self.pattern_alignment_mode = None
        self.pattern_alignment_first = None
        self.pattern_alignment_pending_index = None
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_completed = True
        self.pattern_alignment_measurements = alignment_measurements
        self.pattern_alignment_reference_points = alignment_points
        self.stage_view.set_pattern_selection_enabled(False)
        self.stage_view.set_selected_pattern_index(None)
        self.reset_pattern_print_progress()
        self.update_pattern_display()
        self.pattern_status_label.setText(f"Loaded alignment {Path(path).name}")
        self.append_log(f"# loaded pattern alignment: {path}")
        self.update_control_states()

    def pattern_alignment_data(self) -> dict[str, object]:
        return {
            "offset_x": self.pattern_work_offset_x,
            "offset_y": self.pattern_work_offset_y,
            "matrix": [
                [self.pattern_transform_a11, self.pattern_transform_a12],
                [self.pattern_transform_a21, self.pattern_transform_a22],
            ],
            "rotation_deg": self.pattern_rotation_deg,
            "scale": self.pattern_scale,
            "scale_x": self.pattern_scale_x,
            "scale_y": self.pattern_scale_y,
            "orthogonality_error_deg": self.pattern_orthogonality_error_deg,
        }

    def pattern_alignment_reference_data(self) -> list[dict[str, object]]:
        if self.pattern_alignment_measurements:
            return [
                {
                    "nominal": {"x": nominal_x, "y": nominal_y},
                    "measured": {"x": stage_x, "y": stage_y},
                }
                for nominal_x, nominal_y, stage_x, stage_y
                in self.pattern_alignment_measurements
            ]
        return [
            {"nominal": {"x": x, "y": y}}
            for x, y in self.pattern_alignment_reference_points
        ]

    def pattern_bbox_data(self) -> dict[str, float]:
        if not self.pattern_points:
            return {"x_min": 0.0, "x_max": 0.0, "y_min": 0.0, "y_max": 0.0}
        xs = [x for x, _y in self.pattern_points]
        ys = [y for _x, y in self.pattern_points]
        return {
            "x_min": min(xs),
            "x_max": max(xs),
            "y_min": min(ys),
            "y_max": max(ys),
        }

    def parse_pattern_alignment_data(self, data) -> dict[str, float]:
        if not isinstance(data, dict):
            raise ValueError("alignment JSON must be an object")
        transform = data.get("transform", data)
        if not isinstance(transform, dict):
            raise ValueError("alignment JSON transform must be an object")

        offset_x = float(transform.get("offset_x", transform.get("work_offset_x")))
        offset_y = float(transform.get("offset_y", transform.get("work_offset_y")))
        matrix = transform.get("matrix")
        if matrix is not None:
            a11, a12, a21, a22 = self.parse_transform_matrix(matrix)
        else:
            rotation_deg = float(
                transform.get("rotation_deg", transform.get("rotation", 0.0))
            )
            scale = float(transform.get("scale", 1.0))
            if scale <= 0.0:
                raise ValueError("alignment scale must be positive")
            a11, a12, a21, a22 = self.similarity_matrix_from_rotation_scale(
                rotation_deg,
                scale,
            )
        values = {
            "offset_x": offset_x,
            "offset_y": offset_y,
            "a11": a11,
            "a12": a12,
            "a21": a21,
            "a22": a22,
        }
        if any(not math.isfinite(value) for value in values.values()):
            raise ValueError("alignment contains non-finite values")
        return values

    def parse_transform_matrix(self, matrix) -> tuple[float, float, float, float]:
        if (
            isinstance(matrix, list)
            and len(matrix) == 2
            and all(isinstance(row, list) and len(row) == 2 for row in matrix)
        ):
            return (
                float(matrix[0][0]),
                float(matrix[0][1]),
                float(matrix[1][0]),
                float(matrix[1][1]),
            )
        if isinstance(matrix, dict):
            return (
                float(matrix["a11"]),
                float(matrix["a12"]),
                float(matrix["a21"]),
                float(matrix["a22"]),
            )
        raise ValueError("alignment matrix must be a 2x2 array or object")

    def parse_pattern_alignment_measurement_data(
        self, data
    ) -> list[tuple[float, float, float, float]]:
        if not isinstance(data, dict):
            return []
        raw_points = self.raw_alignment_points_from_data(data)
        if not raw_points:
            return []

        measurements: list[tuple[float, float, float, float]] = []
        for index, point in enumerate(raw_points, start=1):
            if not isinstance(point, dict) or "measured" not in point:
                continue
            nominal = point.get("nominal", point)
            measured = point["measured"]
            nominal_x, nominal_y = self.parse_xy_pair(nominal)
            stage_x, stage_y = self.parse_xy_pair(measured)
            if not all(
                math.isfinite(value)
                for value in (nominal_x, nominal_y, stage_x, stage_y)
            ):
                raise ValueError(
                    f"alignment measurement {index} contains non-finite values"
                )
            measurements.append((nominal_x, nominal_y, stage_x, stage_y))
        return measurements

    def parse_pattern_alignment_reference_data(
        self, data
    ) -> list[tuple[float, float]]:
        if not isinstance(data, dict):
            return []
        raw_points = self.raw_alignment_points_from_data(data)
        if raw_points is None:
            return []
        if not isinstance(raw_points, list):
            raise ValueError("alignment points must be a list")

        points: list[tuple[float, float]] = []
        for index, point in enumerate(raw_points, start=1):
            if isinstance(point, dict) and "nominal" in point:
                point = point["nominal"]
            try:
                x, y = self.parse_xy_pair(point)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"alignment point {index} is invalid") from exc
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(
                    f"alignment point {index} contains non-finite values"
                )
            points.append((x, y))
        return points

    def raw_alignment_points_from_data(self, data):
        for key in ("alignment_points", "alignment_anchors", "reference_points"):
            if key in data:
                return data[key]
        return []

    def parse_xy_pair(self, point) -> tuple[float, float]:
        if isinstance(point, dict):
            return float(point["x"]), float(point["y"])
        if isinstance(point, list) and len(point) >= 2:
            return float(point[0]), float(point[1])
        if isinstance(point, tuple) and len(point) >= 2:
            return float(point[0]), float(point[1])
        raise ValueError("expected x/y pair")

    def parse_pattern_file(self, path: Path) -> list[tuple[float, float]]:
        if path.suffix.lower() == ".json":
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
            raw_points = data.get("points") if isinstance(data, dict) else data
            if not isinstance(raw_points, list):
                raise ValueError("pattern JSON must contain a points list")
            return self.parse_pattern_points(raw_points)

        points: list[tuple[float, float]] = []
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                parts = [part.strip() for part in text.split(",")]
                if len(parts) < 2:
                    raise ValueError(f"line {line_number}: expected x,y")
                if line_number == 1 and parts[0].lower() in {"x", "pcs_x"}:
                    continue
                try:
                    point = (float(parts[0]), float(parts[1]))
                except ValueError as exc:
                    raise ValueError(f"line {line_number}: invalid number") from exc
                points.append(point)
        if not points:
            raise ValueError("pattern contains no points")
        return points

    def parse_pattern_points(self, raw_points) -> list[tuple[float, float]]:
        points: list[tuple[float, float]] = []
        for index, point in enumerate(raw_points, start=1):
            if isinstance(point, dict):
                x = float(point["x"])
                y = float(point["y"])
            elif isinstance(point, list) and len(point) >= 2:
                x = float(point[0])
                y = float(point[1])
            else:
                raise ValueError(f"pattern point {index} is invalid")
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError(f"pattern point {index} contains non-finite values")
            points.append((x, y))
        if not points:
            raise ValueError("pattern contains no points")
        return points

    def initialize_pattern_transform_to_visible_bottom_left(self) -> None:
        offset_x, offset_y = self.stage_view.visible_stage_bottom_left()
        self.set_pattern_transform_matrix(
            -1.0,
            0.0,
            0.0,
            -1.0,
            offset_x,
            offset_y,
            update_display=False,
        )
        self.update_pattern_display()

    def reset_pattern_alignment(self) -> None:
        self.pattern_alignment_state = None
        self.pattern_alignment_mode = None
        self.pattern_alignment_first = None
        self.pattern_alignment_pending_index = None
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_measurements = []
        self.pattern_alignment_completed = False
        self.clear_pattern_alignment_reference_points()
        self.stage_view.set_pattern_selection_enabled(False)
        self.stage_view.set_selected_pattern_index(None)
        self.reset_pattern_print_progress()
        self.initialize_pattern_transform_to_visible_bottom_left()
        if self.pattern_points:
            self.pattern_status_label.setText("Reset to visible bottom-left")
        else:
            self.pattern_status_label.setText("No pattern")
        self.update_control_states()

    def update_pattern_display(self) -> None:
        if not hasattr(self, "pattern_stats_label"):
            return

        if not self.pattern_points:
            self.pattern_stats_label.setText("No pattern")
            self.pattern_transform_label.setText(self.pattern_transform_text())
            if hasattr(self, "stage_view"):
                self.stage_view.clear_pattern_points()
            self.update_pattern_alignment_reference_display()
            return

        xs = [x for x, _y in self.pattern_points]
        ys = [y for _x, y in self.pattern_points]
        self.pattern_stats_label.setText(
            f"{len(self.pattern_points)} pts, "
            f"bbox {max(xs) - min(xs):.3f} x {max(ys) - min(ys):.3f} mm"
        )
        self.pattern_transform_label.setText(self.pattern_transform_text())
        self.stage_view.set_pattern_points(self.transformed_pattern_points())
        self.update_pattern_alignment_reference_display()

    def pattern_transform_text(self) -> str:
        return (
            f"Offset: X{self.pattern_work_offset_x:.3f} "
            f"Y{self.pattern_work_offset_y:.3f}\n"
            "Matrix:\n"
            f"[{self.pattern_transform_a11:.6f} "
            f"{self.pattern_transform_a12:.6f}]\n"
            f"[{self.pattern_transform_a21:.6f} "
            f"{self.pattern_transform_a22:.6f}]\n"
            f"Rotation: {self.pattern_rotation_deg:.3f} deg\n"
            f"Scale: X{self.pattern_scale_x:.5f} "
            f"Y{self.pattern_scale_y:.5f}\n"
            f"Orthogonality error: "
            f"{self.pattern_orthogonality_error_deg:.3f} deg"
        )

    def transformed_pattern_points(self) -> list[tuple[int, float, float]]:
        return [
            (index, *self.pattern_point_to_stage(point))
            for index, point in enumerate(self.pattern_points)
        ]

    def transformed_pattern_positions(self) -> list[tuple[float, float]]:
        return [
            self.pattern_point_to_stage(point)
            for point in self.pattern_points
        ]

    def transformed_pattern_alignment_reference_positions(
        self,
    ) -> list[tuple[float, float]]:
        positions: list[tuple[float, float]] = []
        if self.pattern_alignment_measurements:
            positions.extend(
                (stage_x, stage_y)
                for _nominal_x, _nominal_y, stage_x, stage_y
                in self.pattern_alignment_measurements
            )
        positions.extend(
            self.pattern_point_to_stage(point)
            for point in self.pattern_alignment_reference_points[
                len(self.pattern_alignment_measurements):
            ]
        )
        return positions

    def set_leveling_work_area_to_aligned_pattern_bounds(self) -> None:
        positions = (
            self.transformed_pattern_positions()
            + self.transformed_pattern_alignment_reference_positions()
        )
        positions = [
            (x, y)
            for x, y in positions
            if math.isfinite(x) and math.isfinite(y)
        ]
        if not positions:
            return

        xs = [x for x, _y in positions]
        ys = [y for _x, y in positions]
        margin = (
            self.auto_height_map_margin_mm
            if self.auto_height_map_margin_enabled
            else 0.0
        )
        x_min = min(xs) - margin
        x_max = max(xs) + margin
        y_min = min(ys) - margin
        y_max = max(ys) + margin
        width = max(MIN_WORK_AREA_SIZE_MM, x_max - x_min)
        height = max(MIN_WORK_AREA_SIZE_MM, y_max - y_min)
        center_x = (x_min + x_max) / 2.0
        center_y = (y_min + y_max) / 2.0
        self.stage_view.set_work_area(
            center_x - width / 2.0,
            center_y - height / 2.0,
            width,
            height,
        )
        margin_text = (
            f"with {margin:g} mm margin"
            if margin > 0.0
            else "without margin"
        )
        self.append_log(
            "# auto leveling work area set from aligned pattern "
            f"{margin_text}"
        )

    def set_pattern_alignment_reference_points(
        self, points: list[tuple[float, float]]
    ) -> None:
        self.pattern_alignment_reference_points = list(points)
        self.update_pattern_alignment_reference_display()

    def clear_pattern_alignment_reference_points(self) -> None:
        self.pattern_alignment_reference_points = []
        self.pattern_alignment_measurements = []
        if hasattr(self, "stage_view"):
            self.stage_view.set_pattern_alignment_points([])
        self.update_pcs_axis_display()

    def update_pattern_alignment_reference_display(self) -> None:
        if not hasattr(self, "stage_view"):
            return
        self.stage_view.set_pattern_alignment_points(
            self.transformed_pattern_alignment_reference_positions()
        )
        self.update_pcs_axis_display()

    def update_pcs_axis_display(self) -> None:
        if not hasattr(self, "stage_view"):
            return
        self.stage_view.set_pcs_axis_points(
            (
                self.pattern_point_to_stage((0.0, 0.0)),
                self.pattern_point_to_stage((PCS_AXIS_LENGTH_MM, 0.0)),
                self.pattern_point_to_stage((0.0, PCS_AXIS_LENGTH_MM)),
            )
        )

    def pattern_point_to_stage(self, point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        stage_x = (
            self.pattern_work_offset_x
            + self.pattern_transform_a11 * x
            + self.pattern_transform_a12 * y
        )
        stage_y = (
            self.pattern_work_offset_y
            + self.pattern_transform_a21 * x
            + self.pattern_transform_a22 * y
        )
        return stage_x, stage_y

    def set_pattern_transform_matrix(
        self,
        a11: float,
        a12: float,
        a21: float,
        a22: float,
        offset_x: float,
        offset_y: float,
        *,
        update_display: bool = True,
    ) -> None:
        self.pattern_transform_a11 = a11
        self.pattern_transform_a12 = a12
        self.pattern_transform_a21 = a21
        self.pattern_transform_a22 = a22
        self.pattern_work_offset_x = offset_x
        self.pattern_work_offset_y = offset_y
        self.update_pattern_transform_metrics()
        if update_display:
            self.update_pattern_display()

    def update_pattern_transform_metrics(self) -> None:
        self.pattern_scale_x = math.hypot(
            self.pattern_transform_a11,
            self.pattern_transform_a21,
        )
        self.pattern_scale_y = math.hypot(
            self.pattern_transform_a12,
            self.pattern_transform_a22,
        )
        self.pattern_scale = (self.pattern_scale_x + self.pattern_scale_y) / 2.0
        if self.pattern_scale_x > 1e-12:
            self.pattern_rotation_deg = math.degrees(
                math.atan2(
                    -self.pattern_transform_a21,
                    -self.pattern_transform_a11,
                )
            )
        else:
            self.pattern_rotation_deg = 0.0

        if self.pattern_scale_x > 1e-12 and self.pattern_scale_y > 1e-12:
            dot = (
                self.pattern_transform_a11 * self.pattern_transform_a12
                + self.pattern_transform_a21 * self.pattern_transform_a22
            )
            cosine = clamp(
                dot / (self.pattern_scale_x * self.pattern_scale_y),
                -1.0,
                1.0,
            )
            angle = math.degrees(math.acos(cosine))
            self.pattern_orthogonality_error_deg = angle - 90.0
        else:
            self.pattern_orthogonality_error_deg = 0.0

    def similarity_matrix_from_rotation_scale(
        self,
        rotation_deg: float,
        scale: float,
    ) -> tuple[float, float, float, float]:
        radians = math.radians(rotation_deg)
        cos_theta = math.cos(radians)
        sin_theta = math.sin(radians)
        return (
            -scale * cos_theta,
            scale * sin_theta,
            -scale * sin_theta,
            -scale * cos_theta,
        )

    def set_pattern_transform_from_alignment(
        self,
        first_index: int,
        first_stage: tuple[float, float],
        second_index: int | None = None,
        second_stage: tuple[float, float] | None = None,
    ) -> None:
        first_point = self.pattern_points[first_index]
        second_point = (
            self.pattern_points[second_index]
            if second_index is not None and second_stage is not None
            else None
        )
        self.set_pattern_transform_from_points(
            first_point,
            first_stage,
            second_point,
            second_stage,
        )

    def set_pattern_transform_from_points(
        self,
        first_point: tuple[float, float],
        first_stage: tuple[float, float],
        second_point: tuple[float, float] | None = None,
        second_stage: tuple[float, float] | None = None,
    ) -> None:
        measurements = [
            (first_point[0], first_point[1], first_stage[0], first_stage[1])
        ]
        if second_point is not None and second_stage is not None:
            measurements.append(
                (second_point[0], second_point[1], second_stage[0], second_stage[1])
            )
        self.apply_pattern_transform_from_measurements(
            measurements,
            warn_on_scale=True,
        )

    def apply_pattern_transform_from_measurements(
        self,
        measurements: list[tuple[float, float, float, float]],
        *,
        warn_on_scale: bool = False,
    ) -> bool:
        if not measurements:
            return False
        try:
            if len(measurements) == 1:
                a11 = self.pattern_transform_a11
                a12 = self.pattern_transform_a12
                a21 = self.pattern_transform_a21
                a22 = self.pattern_transform_a22
                nominal_x, nominal_y, stage_x, stage_y = measurements[0]
                offset_x = stage_x - (a11 * nominal_x + a12 * nominal_y)
                offset_y = stage_y - (a21 * nominal_x + a22 * nominal_y)
            elif len(measurements) == 2:
                a11, a12, a21, a22, offset_x, offset_y = (
                    self.fit_similarity_transform(measurements)
                )
            else:
                a11, a12, a21, a22, offset_x, offset_y = (
                    self.fit_affine_transform(measurements)
                )
        except ValueError as exc:
            self.append_log(f"! alignment transform failed: {exc}")
            QMessageBox.warning(
                self,
                "Alignment Failed",
                f"Could not fit the alignment transform:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            return False

        self.set_pattern_transform_matrix(
            a11,
            a12,
            a21,
            a22,
            offset_x,
            offset_y,
        )
        if warn_on_scale:
            self.warn_if_pattern_scale_deviation()
        return True

    def fit_similarity_transform(
        self,
        measurements: list[tuple[float, float, float, float]],
    ) -> tuple[float, float, float, float, float, float]:
        first_x, first_y, first_stage_x, first_stage_y = measurements[0]
        second_x, second_y, second_stage_x, second_stage_y = measurements[1]
        first_point = (first_x, first_y)
        second_point = (second_x, second_y)
        first_stage = (first_stage_x, first_stage_y)
        second_stage = (second_stage_x, second_stage_y)

        first_flipped = (-first_point[0], -first_point[1])
        second_flipped = (-second_point[0], -second_point[1])
        pattern_dx = second_flipped[0] - first_flipped[0]
        pattern_dy = second_flipped[1] - first_flipped[1]
        stage_dx = second_stage[0] - first_stage[0]
        stage_dy = second_stage[1] - first_stage[1]
        pattern_distance = math.hypot(pattern_dx, pattern_dy)
        stage_distance = math.hypot(stage_dx, stage_dy)
        if pattern_distance <= 1e-9 or stage_distance <= 1e-9:
            raise ValueError("alignment points must be distinct")
        scale = stage_distance / pattern_distance
        rotation_deg = math.degrees(
            math.atan2(stage_dy, stage_dx)
            - math.atan2(pattern_dy, pattern_dx)
        )
        a11, a12, a21, a22 = self.similarity_matrix_from_rotation_scale(
            rotation_deg,
            scale,
        )
        offset_x = first_stage[0] - (a11 * first_point[0] + a12 * first_point[1])
        offset_y = first_stage[1] - (a21 * first_point[0] + a22 * first_point[1])
        return a11, a12, a21, a22, offset_x, offset_y

    def fit_affine_transform(
        self,
        measurements: list[tuple[float, float, float, float]],
    ) -> tuple[float, float, float, float, float, float]:
        normal = [[0.0, 0.0, 0.0] for _row in range(3)]
        rhs_x = [0.0, 0.0, 0.0]
        rhs_y = [0.0, 0.0, 0.0]
        for nominal_x, nominal_y, stage_x, stage_y in measurements:
            row = (nominal_x, nominal_y, 1.0)
            for outer in range(3):
                rhs_x[outer] += row[outer] * stage_x
                rhs_y[outer] += row[outer] * stage_y
                for inner in range(3):
                    normal[outer][inner] += row[outer] * row[inner]

        coeff_x = self.solve_3x3(normal, rhs_x)
        coeff_y = self.solve_3x3(normal, rhs_y)
        return (
            coeff_x[0],
            coeff_x[1],
            coeff_y[0],
            coeff_y[1],
            coeff_x[2],
            coeff_y[2],
        )

    def solve_3x3(
        self,
        matrix: list[list[float]],
        vector: list[float],
    ) -> tuple[float, float, float]:
        augmented = [
            [matrix[row][0], matrix[row][1], matrix[row][2], vector[row]]
            for row in range(3)
        ]
        for column in range(3):
            pivot_row = max(
                range(column, 3),
                key=lambda row: abs(augmented[row][column]),
            )
            pivot = augmented[pivot_row][column]
            if abs(pivot) <= 1e-12:
                raise ValueError(
                    "alignment points are degenerate; choose non-collinear anchors"
                )
            if pivot_row != column:
                augmented[column], augmented[pivot_row] = (
                    augmented[pivot_row],
                    augmented[column],
                )
            pivot = augmented[column][column]
            for item in range(column, 4):
                augmented[column][item] /= pivot
            for row in range(3):
                if row == column:
                    continue
                factor = augmented[row][column]
                for item in range(column, 4):
                    augmented[row][item] -= factor * augmented[column][item]
        return augmented[0][3], augmented[1][3], augmented[2][3]

    def warn_if_pattern_scale_deviation(self) -> None:
        max_deviation = max(
            abs(self.pattern_scale_x - 1.0),
            abs(self.pattern_scale_y - 1.0),
        )
        if max_deviation <= 0.02:
            return
        QMessageBox.warning(
            self,
            "Pattern Scale Warning",
            (
                "Measured pattern scale differs from nominal by more than 2%.\n\n"
                f"Scale X: {self.pattern_scale_x:.5f} "
                f"({self.pattern_scale_x * 100.0:.2f}%)\n"
                f"Scale Y: {self.pattern_scale_y:.5f} "
                f"({self.pattern_scale_y * 100.0:.2f}%)"
            ),
            QMessageBox.StandardButton.Ok,
        )

    def rotate_flipped_pattern_point(
        self, point: tuple[float, float]
    ) -> tuple[float, float]:
        x, y = point
        return (
            self.pattern_transform_a11 * x + self.pattern_transform_a12 * y,
            self.pattern_transform_a21 * x + self.pattern_transform_a22 * y,
        )

    def reset_pattern_print_progress(self) -> None:
        self.pattern_print_dot_states = {}
        self.pattern_print_command_events = []
        self.pattern_print_total_dots = 0
        if hasattr(self, "stage_view"):
            self.stage_view.set_pattern_print_states({})

    def clear_current_pattern_print_dot(self) -> None:
        changed = False
        for index, state in list(self.pattern_print_dot_states.items()):
            if state == "printing":
                del self.pattern_print_dot_states[index]
                changed = True
        if changed and hasattr(self, "stage_view"):
            self.stage_view.set_pattern_print_states(self.pattern_print_dot_states)

    def consume_pattern_print_event(self) -> None:
        if not self.pattern_print_command_events:
            return

        event = self.pattern_print_command_events.pop(0)
        if event is None:
            return

        action, point_index = event
        if action == "start":
            self.pattern_print_dot_states[point_index] = "printing"
        elif action == "finish":
            self.pattern_print_dot_states[point_index] = "printed"
        else:
            return

        if hasattr(self, "stage_view"):
            self.stage_view.set_pattern_print_states(self.pattern_print_dot_states)
        if hasattr(self, "pattern_status_label") and self.pattern_print_active:
            printed_count = sum(
                1 for state in self.pattern_print_dot_states.values()
                if state == "printed"
            )
            total_count = self.pattern_print_total_dots or len(self.pattern_points)
            self.pattern_status_label.setText(
                f"Printing pattern: {printed_count}/{total_count} dots"
            )

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
        has_height_map = self.height_map_completed and bool(self.height_map_points)
        has_pattern = bool(self.pattern_points)

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
        if self.height_map_active:
            if self.height_map_abort_requested:
                self.start_height_map_button.setText("Aborting...")
            elif self.height_map_finishing:
                self.start_height_map_button.setText("Finishing...")
            else:
                self.start_height_map_button.setText("Abort")
        else:
            self.start_height_map_button.setText("Start Probing")
        self.start_height_map_button.setEnabled(
            (
                self.height_map_active
                and not self.height_map_finishing
                and not self.height_map_abort_requested
            )
            or (homed_motion_allowed and not self.height_map_active)
        )
        if hasattr(self, "printing_widgets"):
            for widget in self.printing_widgets:
                widget.setEnabled(can_issue)
        if hasattr(self, "print_circle_button"):
            self.print_circle_button.setEnabled(
                can_issue and has_height_map and not self.height_map_active
            )
        if hasattr(self, "pattern_load_button"):
            pattern_idle = (
                not self.height_map_active
                and not self.printing_active
                and not self.print_circle_editing
                and self.pattern_alignment_state is None
            )
            self.pattern_load_button.setEnabled(pattern_idle)
            self.pattern_reset_button.setEnabled(has_pattern and pattern_idle)
            self.pattern_save_alignment_button.setEnabled(
                pattern_idle
            )
            self.pattern_load_alignment_button.setEnabled(
                pattern_idle
            )
            self.pattern_align_button.setEnabled(
                can_issue
                and has_pattern
                and not self.print_circle_editing
                and self.pattern_alignment_state is None
            )
            self.pattern_anchor_align_button.setEnabled(
                homed_motion_allowed and pattern_idle
            )
            if self.pattern_print_active:
                self.pattern_print_button.setText("Abort")
                self.pattern_print_button.setEnabled(True)
                self.pattern_print_button.setToolTip(
                    "Abort the active dot print and clear pending print commands."
                )
            else:
                pattern_print_enabled = (
                    can_issue
                    and has_pattern
                    and has_height_map
                    and not self.print_circle_editing
                    and self.pattern_alignment_state is None
                )
                self.pattern_print_button.setText("Print Dots")
                self.pattern_print_button.setEnabled(pattern_print_enabled)
                self.pattern_print_button.setToolTip(
                    self.pattern_print_tooltip(
                        connected=connected,
                        has_pattern=has_pattern,
                        has_height_map=has_height_map,
                    )
                )
            for widget in (
                self.pattern_print_height_spin,
                self.pattern_kick_spin,
                self.pattern_retract_spin,
                self.pattern_travel_height_spin,
                self.pattern_lifting_speed_spin,
            ):
                widget.setEnabled(pattern_idle)
        if hasattr(self, "stage_view"):
            self.stage_view.set_motion_enabled(
                homed_motion_allowed,
                disabled_by_not_homed=connected and not self.all_axes_homed,
            )
            if not self.print_circle_editing:
                self.stage_view.set_work_area_edit_enabled(
                    not self.height_map_active and not self.printing_active
                )
        self.update_workflow_statuses()

    def update_workflow_statuses(self) -> None:
        if not hasattr(self, "workflow_status_labels"):
            return
        tool_type = (self.current_tool_type or "").strip().lower()
        states = {
            "connected": self.serial_connected,
            "homed": self.all_axes_homed,
            "height_map": self.height_map_completed and bool(self.height_map_points),
            "aligned": self.pattern_alignment_completed,
            "pattern": bool(self.pattern_points),
            "dispenser": bool(tool_type and tool_type not in {"none", "probe"}),
        }
        for key, label in self.workflow_status_labels.items():
            label.setStyleSheet(
                WORKFLOW_COMPLETE_STYLE
                if states.get(key, False)
                else WORKFLOW_PENDING_STYLE
            )

    def pattern_print_tooltip(
        self,
        *,
        connected: bool,
        has_pattern: bool,
        has_height_map: bool,
    ) -> str:
        reasons: list[str] = []
        if not connected:
            reasons.append("Connect to the controller.")
        if self.motion_busy:
            reasons.append("Wait for the current motion/M400 synchronization.")
        if self.height_map_active:
            reasons.append("Wait for height mapping to finish or abort it.")
        if self.printing_active:
            reasons.append("Wait for the active print to finish or abort it.")
        if not has_pattern:
            reasons.append("Load a pattern file.")
        if not has_height_map:
            reasons.append("Create or load a completed height map.")
        if self.print_circle_editing:
            reasons.append("Finish or cancel circle editing.")
        if self.pattern_alignment_state is not None:
            reasons.append("Finish or cancel the active alignment procedure.")

        if not reasons:
            return "Print pattern dots using the current alignment and height map."
        return "Print Dots is disabled:\n- " + "\n- ".join(reasons)

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
            if self.height_map_finishing:
                if is_error_line(line):
                    self.finish_height_map()
                elif (
                    (line == "ok" or line == "empty" or line.startswith("positionUpdate"))
                    and (
                        self.serial_thread is None
                        or self.serial_thread.is_idle()
                    )
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
                    if self.pending_pattern_print_points is not None:
                        self.queue_prepared_pattern_print()
                    else:
                        self.queue_prepared_circle_print()
                return
            if (
                (line == "ok" or line == "empty")
                and self.serial_thread is not None
                and self.serial_thread.is_idle()
            ):
                self.finish_printing()
            return
        if is_error_line(line):
            self.motion_busy = False
            self.update_control_states()
            return
        if (
            (line == "ok" or line == "empty" or line.startswith("positionUpdate"))
            and (
                self.serial_thread is None
                or self.serial_thread.is_idle()
            )
        ):
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

    def handle_pattern_align_button(self) -> None:
        state = self.pattern_alignment_state
        if state is None:
            self.begin_pattern_alignment()
        else:
            self.cancel_pattern_alignment("Alignment cancelled")

    def handle_pattern_anchor_align_button(self) -> None:
        state = self.pattern_alignment_state
        if state is None:
            self.begin_anchor_pattern_alignment()
        else:
            self.cancel_pattern_alignment("Alignment cancelled")

    def configured_alignment_point_count(self) -> int:
        return 4 if self.use_four_point_alignment else 2

    def alignment_required_point_count(self) -> int:
        if (
            self.pattern_alignment_state is not None
            or self.pattern_alignment_mode is not None
        ):
            return self.pattern_alignment_point_count
        return self.configured_alignment_point_count()

    def begin_pattern_alignment(self) -> None:
        if not self.ensure_pattern_alignment_can_start():
            return

        self.pattern_alignment_mode = "click"
        self.pattern_alignment_point_count = self.configured_alignment_point_count()
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_measurements = []
        self.pattern_alignment_completed = False
        self.clear_pattern_alignment_reference_points()
        self.open_alignment_procedure_dialog("click")
        self.pattern_alignment_first = None
        self.pattern_alignment_pending_index = None
        self.set_pattern_alignment_state("select_point")
        self.stage_view.set_pattern_selection_enabled(True)
        self.stage_view.set_selected_pattern_index(None)
        self.pattern_status_label.setText("Click pattern point 1")

    def begin_anchor_pattern_alignment(self) -> None:
        if not self.ensure_anchor_alignment_can_start():
            return

        self.pattern_alignment_mode = "anchors"
        self.pattern_alignment_point_count = self.configured_alignment_point_count()
        self.pattern_alignment_first = None
        self.pattern_alignment_pending_index = None
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_measurements = []
        self.pattern_alignment_completed = False
        self.clear_pattern_alignment_reference_points()
        self.open_alignment_procedure_dialog("anchors")
        self.set_pattern_alignment_state("anchor_setup")
        self.stage_view.set_pattern_selection_enabled(False)
        self.stage_view.set_selected_pattern_index(None)
        self.pattern_status_label.setText("Enter anchor positions")

    def ensure_pattern_alignment_can_start(self) -> bool:
        if not self.pattern_points:
            QMessageBox.warning(
                self,
                "No Pattern",
                "Load a pattern before alignment.",
                QMessageBox.StandardButton.Ok,
            )
            return False
        if len(self.pattern_points) < self.alignment_required_point_count():
            QMessageBox.warning(
                self,
                "Not Enough Pattern Points",
                (
                    "The current alignment mode needs "
                    f"{self.alignment_required_point_count()} pattern points."
                ),
                QMessageBox.StandardButton.Ok,
            )
            return False
        if self.serial_thread is None:
            self.append_log("! not connected")
            return False
        if self.motion_busy or self.height_map_active or self.printing_active:
            self.append_log("! pattern alignment blocked: waiting for active motion")
            return False
        return True

    def ensure_anchor_alignment_can_start(self) -> bool:
        if self.serial_thread is None:
            self.append_log("! not connected")
            return False
        if not self.all_axes_homed:
            self.warn_not_homed()
            return False
        if (
            self.motion_busy
            or self.height_map_active
            or self.printing_active
            or self.print_circle_editing
        ):
            self.append_log("! anchor alignment blocked: waiting for active motion")
            return False
        return True

    def open_alignment_procedure_dialog(self, mode: str) -> None:
        if self.alignment_procedure_dialog is not None:
            old_dialog = self.alignment_procedure_dialog
            self.alignment_procedure_dialog = None
            old_dialog.close()

        dialog = AlignmentProcedureDialog(
            self,
            mode,
            self.default_anchor_points(),
            self.alignment_required_point_count(),
            self.pattern_alignment_height,
            self.alignment_coarse_xy_step,
            self.alignment_fine_xy_step,
            self,
        )
        self.alignment_procedure_dialog = dialog
        dialog.finished.connect(self.on_alignment_dialog_finished)
        dialog.alignment_height_spin.valueChanged.connect(
            lambda _value: self.save_anchor_config_from_dialog()
        )
        dialog.coarse_xy_step_spin.valueChanged.connect(
            lambda _value: self.save_anchor_config_from_dialog()
        )
        dialog.fine_xy_step_spin.valueChanged.connect(
            lambda _value: self.save_anchor_config_from_dialog()
        )
        if mode == "anchors":
            for spin in dialog.anchor_spins:
                spin.valueChanged.connect(
                    lambda _value: self.on_alignment_anchor_inputs_changed()
                )
            self.on_alignment_anchor_inputs_changed()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def default_anchor_points(self) -> list[tuple[float, float]]:
        required = self.alignment_required_point_count()
        defaults: list[tuple[float, float]] = []
        if self.pattern_anchor_defaults is not None:
            defaults = list(self.pattern_anchor_defaults[:required])
        if not self.pattern_points:
            fallback = [
                (0.0, 0.0),
                (10.0, 0.0),
                (10.0, 10.0),
                (0.0, 10.0),
            ]
            return (defaults + fallback[len(defaults):])[:required]
        xs = [x for x, _y in self.pattern_points]
        ys = [y for _x, y in self.pattern_points]
        x_min = min(xs)
        x_max = max(xs)
        y_min = min(ys)
        y_max = max(ys)
        generated = [
            (x_min, y_min),
            (x_max, y_max),
            (x_max, y_min),
            (x_min, y_max),
        ]
        if math.hypot(x_max - x_min, y_max - y_min) <= 1e-9:
            generated = [
                (x_min, y_min),
                (x_min + 10.0, y_min),
                (x_min + 10.0, y_min + 10.0),
                (x_min, y_min + 10.0),
            ]
        return (defaults + generated[len(defaults):])[:required]

    def validate_nominal_alignment_points(
        self,
        points: list[tuple[float, float]],
    ) -> bool:
        if len(points) < 2:
            return len(points) == 1
        first = points[0]
        if all(
            math.hypot(point[0] - first[0], point[1] - first[1]) <= 1e-9
            for point in points[1:]
        ):
            return False
        if len(points) < 3:
            return True
        try:
            self.fit_affine_transform(
                [(x, y, x, y) for x, y in points]
            )
        except ValueError:
            return False
        return True

    def on_alignment_dialog_finished(self, _result: int = 0) -> None:
        if self.alignment_procedure_dialog is None:
            return
        self.alignment_procedure_dialog = None
        if self.pattern_alignment_state is not None:
            self.cancel_pattern_alignment("Alignment cancelled")

    def handle_alignment_dialog_primary(self) -> None:
        state = self.pattern_alignment_state
        if state == "anchor_setup":
            self.start_anchor_alignment_from_dialog()
        elif state in {"move_point", "anchor_move"}:
            self.lower_to_pattern_alignment_height()
        elif state in {"fine_point", "anchor_fine"}:
            self.confirm_pattern_alignment_point()
        elif state is None and self.alignment_procedure_dialog is not None:
            self.alignment_procedure_dialog.close()

    def start_anchor_alignment_from_dialog(self) -> None:
        if self.alignment_procedure_dialog is None:
            return
        anchor_points = self.alignment_procedure_dialog.anchor_points()
        self.save_anchor_config(
            anchor_points,
            self.alignment_procedure_dialog.alignment_height(),
            self.alignment_procedure_dialog.coarse_xy_step(),
            self.alignment_procedure_dialog.fine_xy_step(),
        )
        required = self.alignment_required_point_count()
        if len(anchor_points) < required:
            QMessageBox.warning(
                self,
                "Invalid Anchors",
                f"Enter {required} anchor positions.",
                QMessageBox.StandardButton.Ok,
            )
            return
        anchor_points = anchor_points[:required]
        if not self.validate_nominal_alignment_points(anchor_points):
            QMessageBox.warning(
                self,
                "Invalid Anchors",
                "Choose distinct, non-collinear anchor positions.",
                QMessageBox.StandardButton.Ok,
            )
            return

        self.pattern_alignment_anchor_points = anchor_points
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_measurements = []
        self.set_pattern_alignment_reference_points(anchor_points)
        self.alignment_procedure_dialog.set_anchor_inputs_enabled(False)
        if not self.send_synchronized_motion(
            *self.alignment_safe_z_payloads(),
            require_homed=False,
        ):
            return
        self.set_pattern_alignment_state("anchor_move")
        self.pattern_status_label.setText("Go to anchor 1, then Lower Z")

    def update_alignment_dialog_for_state(self) -> None:
        dialog = self.alignment_procedure_dialog
        if dialog is None:
            return

        state = self.pattern_alignment_state
        label = ALIGNMENT_POINT_LABELS[
            min(self.pattern_alignment_current_index, len(ALIGNMENT_POINT_LABELS) - 1)
        ]
        step_index = None
        action_text = "Close"
        action_enabled = True
        status = "Alignment complete."
        if state == "select_point":
            step_index = self.pattern_alignment_current_index * 3
            action_text = "Select on Stage"
            action_enabled = False
            status = f"Click pattern dot {label} on the stage view."
        elif state == "move_point":
            step_index = self.pattern_alignment_current_index * 3 + 1
            action_text = "Lower Z"
            status = (
                f"Move to the physical counterpart of point {label}, "
                "then lower Z."
            )
        elif state == "fine_point":
            step_index = self.pattern_alignment_current_index * 3 + 2
            action_text = f"Confirm {label}"
            status = (
                "Use the cameras and XY jog buttons, "
                f"then confirm point {label}."
            )
        elif state == "anchor_setup":
            step_index = 0
            action_text = "Start"
            status = (
                f"Enter {self.alignment_required_point_count()} anchor positions "
                "in pattern coordinates, then start."
            )
        elif state == "anchor_move":
            step_index = self.pattern_alignment_current_index * 2
            action_text = "Lower Z"
            status = f"Move to physical anchor {label}, then lower Z."
        elif state == "anchor_fine":
            step_index = self.pattern_alignment_current_index * 2 + 1
            action_text = f"Confirm {label}"
            status = (
                "Use the cameras and XY jog buttons, "
                f"then confirm anchor {label}."
            )

        dialog.set_current_step(step_index)
        dialog.set_jog_step_mode(self.alignment_state_uses_fine_step(state))
        dialog.set_primary_action(action_text, action_enabled)
        dialog.set_status(status)
        if state != "anchor_setup":
            dialog.set_anchor_inputs_enabled(False)

    def alignment_state_uses_fine_step(self, state: str | None) -> bool:
        return state in {"fine_point", "anchor_fine"}

    def cancel_pattern_alignment(self, status: str) -> None:
        self.raise_to_max_z_after_alignment()
        self.pattern_alignment_state = None
        self.pattern_alignment_mode = None
        self.pattern_alignment_pending_index = None
        self.pattern_alignment_first = None
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_current_index = 0
        self.pattern_alignment_measurements = []
        self.pattern_alignment_completed = False
        self.clear_pattern_alignment_reference_points()
        self.stage_view.set_pattern_selection_enabled(False)
        self.stage_view.set_selected_pattern_index(None)
        if self.alignment_procedure_dialog is not None:
            dialog = self.alignment_procedure_dialog
            self.alignment_procedure_dialog = None
            dialog.close()
        self.pattern_align_button.setText("Click && Align")
        self.pattern_status_label.setText(status)
        self.update_control_states()

    def set_pattern_alignment_state(self, state: str | None) -> None:
        self.pattern_alignment_state = state
        button_text = {
            None: "Click && Align",
            "select_point": "Cancel Align",
            "move_point": "Cancel Align",
            "fine_point": "Cancel Align",
            "anchor_setup": "Cancel Align",
            "anchor_move": "Cancel Align",
            "anchor_fine": "Cancel Align",
        }.get(state, "Click && Align")
        self.pattern_align_button.setText(button_text)
        self.update_alignment_dialog_for_state()
        self.update_control_states()

    def on_pattern_point_selected(self, point_index: int) -> None:
        if self.pattern_alignment_mode != "click":
            return
        if self.pattern_alignment_state != "select_point":
            return

        point = self.pattern_points[point_index]
        reference_points = [
            (nominal_x, nominal_y)
            for nominal_x, nominal_y, _stage_x, _stage_y
            in self.pattern_alignment_measurements
        ]
        reference_points.append(point)
        if not self.validate_nominal_alignment_points(reference_points):
            QMessageBox.warning(
                self,
                "Invalid Alignment Point",
                "Choose distinct, non-collinear alignment points.",
                QMessageBox.StandardButton.Ok,
            )
            return
        self.set_pattern_alignment_reference_points(reference_points)
        self.pattern_alignment_pending_index = point_index
        self.stage_view.set_pattern_selection_enabled(False)
        if self.pattern_alignment_current_index == 0:
            self.raise_for_pattern_alignment_point(point_index)
        else:
            self.move_to_pattern_alignment_point(point_index)

    def raise_for_pattern_alignment_point(self, point_index: int) -> None:
        if not self.send_synchronized_motion(
            *self.alignment_safe_z_payloads(),
            require_homed=False,
        ):
            return
        self.set_pattern_alignment_state("move_point")
        self.pattern_status_label.setText(
            f"Move to physical point {point_index + 1}, then Lower Z"
        )

    def move_to_pattern_alignment_point(self, point_index: int) -> None:
        rough_x, rough_y = self.pattern_point_to_stage(self.pattern_points[point_index])
        if not self.send_synchronized_motion(
            *self.alignment_safe_z_payloads(),
            f"V1 X{rough_x:.6f} Y{rough_y:.6f}",
            require_homed=False,
        ):
            return
        self.set_pattern_alignment_state("move_point")
        self.pattern_status_label.setText(
            f"Rough moving to point {point_index + 1}; then Lower Z"
        )

    def alignment_safe_z_payloads(self) -> list[str]:
        if self.maximum_z_position is None:
            return ["V3 Z"]
        return [f"V1 Z{self.maximum_z_position - MAXIMUM_Z_RETURN_MARGIN_MM:.6f}"]

    def raise_to_max_z_after_alignment(self) -> bool:
        if self.serial_thread is None:
            return False
        payloads = self.alignment_safe_z_payloads()
        if not payloads:
            return False

        for payload in payloads:
            self.send_payload(payload, force=True)
        self.send_payload("M400", force=True)
        self.motion_busy = True
        self.append_log("# alignment ended: raising to max Z")
        self.update_control_states()
        return True

    def lower_to_pattern_alignment_height(self) -> None:
        if self.alignment_procedure_dialog is not None:
            target_z = self.alignment_procedure_dialog.alignment_height()
            self.save_anchor_config_from_dialog()
        else:
            target_z = self.pattern_alignment_height
        if not self.send_synchronized_motion(
            f"V1 Z{target_z:.6f}",
            require_homed=False,
        ):
            return

        label = ALIGNMENT_POINT_LABELS[self.pattern_alignment_current_index]
        if self.pattern_alignment_state == "move_point":
            self.set_pattern_alignment_state("fine_point")
            self.pattern_status_label.setText(
                f"Fine-align point {label}, then Confirm {label}"
            )
        elif self.pattern_alignment_state == "anchor_move":
            self.set_pattern_alignment_state("anchor_fine")
            self.pattern_status_label.setText(
                f"Fine-align anchor {label}, then Confirm {label}"
            )

    def confirm_pattern_alignment_point(self) -> None:
        if self.current_x is None or self.current_y is None:
            QMessageBox.warning(
                self,
                "No Position",
                "Wait for a positionUpdate before confirming alignment.",
                QMessageBox.StandardButton.Ok,
            )
            return
        if self.pattern_alignment_state == "anchor_fine":
            self.confirm_anchor_alignment_point((self.current_x, self.current_y))
            return
        if self.pattern_alignment_pending_index is None:
            self.cancel_pattern_alignment("Alignment failed")
            return

        point_index = self.pattern_alignment_pending_index
        stage_position = (self.current_x, self.current_y)
        if self.pattern_alignment_state != "fine_point":
            self.cancel_pattern_alignment("Alignment failed")
            return

        point = self.pattern_points[point_index]
        if not self.add_pattern_alignment_measurement(point, stage_position):
            return
        self.pattern_alignment_pending_index = None
        if self.pattern_alignment_is_complete():
            self.finish_pattern_alignment()
            self.stage_view.set_pattern_selection_enabled(False)
            self.pattern_status_label.setText("Aligned")
            return

        self.pattern_alignment_current_index = len(self.pattern_alignment_measurements)
        self.set_pattern_alignment_state("select_point")
        self.stage_view.set_pattern_selection_enabled(True)
        label = ALIGNMENT_POINT_LABELS[self.pattern_alignment_current_index]
        self.pattern_status_label.setText(f"Click pattern point {label}")

    def confirm_anchor_alignment_point(
        self, stage_position: tuple[float, float]
    ) -> None:
        if self.pattern_alignment_anchor_points is None:
            self.cancel_pattern_alignment("Alignment failed")
            return

        if self.pattern_alignment_state != "anchor_fine":
            self.cancel_pattern_alignment("Alignment failed")
            return

        anchor_index = self.pattern_alignment_current_index
        if anchor_index >= len(self.pattern_alignment_anchor_points):
            self.cancel_pattern_alignment("Alignment failed")
            return
        anchor = self.pattern_alignment_anchor_points[anchor_index]
        if not self.add_pattern_alignment_measurement(anchor, stage_position):
            return

        if self.pattern_alignment_is_complete():
            self.pattern_alignment_anchor_points = None
            self.pattern_alignment_anchor_first = None
            self.finish_pattern_alignment()
            self.pattern_status_label.setText("Aligned to anchors")
            return

        self.pattern_alignment_current_index = len(self.pattern_alignment_measurements)
        next_anchor = self.pattern_alignment_anchor_points[
            self.pattern_alignment_current_index
        ]
        rough_x, rough_y = self.pattern_point_to_stage(next_anchor)
        if not self.send_synchronized_motion(
            *self.alignment_safe_z_payloads(),
            f"V1 X{rough_x:.6f} Y{rough_y:.6f}",
            require_homed=False,
        ):
            return
        label = ALIGNMENT_POINT_LABELS[self.pattern_alignment_current_index]
        self.set_pattern_alignment_state("anchor_move")
        self.pattern_status_label.setText(
            f"Rough moving to anchor {label}; then Lower Z"
        )

    def add_pattern_alignment_measurement(
        self,
        nominal_point: tuple[float, float],
        stage_position: tuple[float, float],
    ) -> bool:
        previous_measurements = list(self.pattern_alignment_measurements)
        self.pattern_alignment_measurements.append(
            (
                nominal_point[0],
                nominal_point[1],
                stage_position[0],
                stage_position[1],
            )
        )
        if not self.apply_pattern_transform_from_measurements(
            self.pattern_alignment_measurements,
            warn_on_scale=False,
        ):
            self.pattern_alignment_measurements = previous_measurements
            self.set_pattern_alignment_reference_points(
                [
                    (nominal_x, nominal_y)
                    for nominal_x, nominal_y, _stage_x, _stage_y
                    in previous_measurements
                ]
            )
            return False
        self.set_pattern_alignment_reference_points(
            [
                (nominal_x, nominal_y)
                for nominal_x, nominal_y, _stage_x, _stage_y
                in self.pattern_alignment_measurements
            ]
        )
        return True

    def pattern_alignment_is_complete(self) -> bool:
        return (
            len(self.pattern_alignment_measurements)
            >= self.alignment_required_point_count()
        )

    def finish_pattern_alignment(self) -> None:
        self.pattern_alignment_state = None
        self.pattern_alignment_mode = None
        self.pattern_alignment_pending_index = None
        self.pattern_alignment_first = None
        self.pattern_alignment_anchor_points = None
        self.pattern_alignment_anchor_first = None
        self.pattern_alignment_completed = True
        self.warn_if_pattern_scale_deviation()
        self.set_leveling_work_area_to_aligned_pattern_bounds()
        self.raise_to_max_z_after_alignment()
        self.stage_view.set_pattern_selection_enabled(False)
        self.stage_view.set_selected_pattern_index(None)
        self.update_alignment_dialog_for_state()
        if self.alignment_procedure_dialog is not None:
            dialog = self.alignment_procedure_dialog
            self.alignment_procedure_dialog = None
            dialog.close()
        self.pattern_align_button.setText("Click && Align")
        self.update_control_states()

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

    def start_pattern_print(self) -> None:
        if self.serial_thread is None:
            self.append_log("! not connected")
            return
        if self.motion_busy or self.height_map_active or self.printing_active:
            self.append_log("! pattern print blocked: waiting for active motion")
            return
        if not self.pattern_points:
            QMessageBox.warning(
                self,
                "No Pattern",
                "Load a pattern before printing.",
                QMessageBox.StandardButton.Ok,
            )
            return
        if not self.height_map_points:
            QMessageBox.warning(
                self,
                "Height Map Required",
                "Create or load a height map before printing.",
                QMessageBox.StandardButton.Ok,
            )
            return

        points = self.transformed_pattern_points()
        if not points:
            self.append_log("! pattern has no print points")
            return

        self.reset_pattern_print_progress()
        self.pending_pattern_print_points = points
        self.pattern_print_active = True
        self.printing_active = True
        self.print_preparing = True
        self.motion_busy = True
        self.pattern_status_label.setText("Preparing pattern print")
        self.append_log(f"# pattern print preparing: {len(points)} points")
        self.send_payload("V3 Z", force=True)
        self.send_payload("M400", force=True)
        self.update_control_states()

    def on_pattern_print_button_clicked(self) -> None:
        if self.pattern_print_active:
            self.abort_pattern_print()
            return
        self.start_pattern_print()

    def abort_pattern_print(self) -> None:
        if not self.pattern_print_active:
            return
        if self.serial_thread is not None:
            self.serial_thread.clear_pending()
        self.motion_busy = False
        self.cancel_printing("Pattern print aborted")
        self.append_log("# pattern print aborted; pending print commands cleared")
        if self.serial_thread is None:
            self.update_control_states()
            return
        self.append_log("# homing after pattern print abort")
        self.motion_busy = True
        self.send_payload("V5", force=True)
        self.send_payload("M400", force=True)
        self.update_control_states()

    def queue_prepared_pattern_print(self) -> None:
        points = self.pending_pattern_print_points
        if points is None:
            self.cancel_printing("Print failed")
            self.motion_busy = False
            self.append_log("! no pending pattern print after preparation")
            self.update_control_states()
            return

        commands, events = self.build_pattern_dot_print_commands(points)
        if not commands:
            self.cancel_printing("Print failed")
            self.motion_busy = False
            self.append_log("! no pattern print commands were generated")
            self.update_control_states()
            return

        self.print_preparing = False
        self.pending_pattern_print_points = None
        self.pattern_print_command_events = events
        self.pattern_print_total_dots = sum(
            1 for event in events
            if event is not None and event[0] == "finish"
        )
        self.pattern_status_label.setText("Printing pattern")
        self.append_log(f"# pattern print queued: {len(commands)} commands")
        for payload in commands:
            self.send_payload(payload, force=True)
        self.update_control_states()

    def build_pattern_dot_print_commands(
        self, points: list[tuple[int, float, float]]
    ) -> tuple[list[str], list[tuple[str, int] | None]]:
        print_height = self.pattern_print_height_spin.value()
        travel_height = self.pattern_travel_height_spin.value()
        lifting_feedrate = self.pattern_lifting_speed_spin.value() * 60.0
        kick = self.pattern_kick_spin.value() / 1000.0
        retract = self.pattern_retract_spin.value() / 1000.0

        commands: list[str] = ["V102"]
        events: list[tuple[str, int] | None] = [None]
        dot_count = 0
        for point_index, x, y in points:
            if not (0.0 <= x <= STAGE_X_MAX_MM and 0.0 <= y <= STAGE_Y_MAX_MM):
                self.append_log(f"! skipped off-stage pattern point X{x:.3f} Y{y:.3f}")
                continue
            dot_count += 1
            z = self.interpolate_height(x, y)
            commands.append(f"V1 X{x:.6f} Y{y:.6f}")
            events.append(None)
            commands.append(f"V102 Z{travel_height:g}")
            events.append(None)
            commands.append(f"V1 Z{z:.6f} D")
            events.append(None)
            commands.append(f"V102 Z{print_height:g}")
            events.append(None)
            commands.append(f"V1 X{x:.6f} Y{y:.6f} Z{z:.6f} D")
            events.append(("start", point_index))
            if kick:
                commands.append(f"V1 E{kick:g}")
                events.append(None)
            if retract:
                commands.append(f"V1 E{-retract:g}")
                events.append(None)
            commands.append("V102")
            events.append(("finish", point_index))
            commands.append(f"V102 Z{travel_height:g}")
            events.append(None)
            commands.append(f"V1 Z{z:.6f} D F{lifting_feedrate:g}")
            events.append(None)
            commands.append("V102")
            events.append(None)

        if dot_count == 0:
            return [], []

        commands.append("V102")
        events.append(None)
        return_commands = self.return_to_z_switch_commands()
        commands.extend(return_commands)
        events.extend([None] * len(return_commands))
        commands.append("M400")
        events.append(None)
        return commands, events

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
        self.pending_pattern_print_points = None
        self.pattern_print_command_events = []
        self.motion_busy = False
        self.stage_view.clear_print_circle()
        self.print_status_label.setText("Print complete")
        if self.pattern_print_active:
            self.pattern_status_label.setText("Pattern print complete")
            self.pattern_print_active = False
        self.update_control_states()

    def cancel_printing(self, status: str, clear_queue: bool = False) -> None:
        if not self.printing_active and not self.print_circle_editing:
            return
        if clear_queue and self.serial_thread is not None:
            self.serial_thread.clear_pending()
        self.printing_active = False
        self.print_preparing = False
        self.pending_print_circle = None
        self.pending_pattern_print_points = None
        self.pattern_print_command_events = []
        self.print_circle_editing = False
        self.clear_current_pattern_print_dot()
        self.stage_view.clear_print_circle()
        self.print_circle_button.setText("Print Circle")
        self.pattern_print_button.setText("Print Dots")
        self.print_status_label.setText(status)
        if self.pattern_print_active:
            self.pattern_status_label.setText(status)
            self.pattern_print_active = False

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
        self.height_map_abort_requested = False
        self.height_map_probe_phase = None
        self.height_map_completed = True
        if work_area is not None:
            self.stage_view.set_work_area(*work_area)
        self.stage_view.set_height_map_points(self.height_map_points)
        self.height_map_status_label.setText(self.height_map_summary())
        self.append_log(f"# loaded height map: {path}")
        self.maybe_warn_height_map_deviation()
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

    def update_height_map_preview(self) -> None:
        if not all(
            hasattr(self, name)
            for name in (
                "stage_view",
                "height_map_x_points_spin",
                "height_map_y_points_spin",
            )
        ):
            return
        self.stage_view.set_height_map_preview_points(self.build_height_map_plan())

    def start_height_map_probing(self) -> None:
        if self.height_map_active:
            self.request_height_map_abort()
            return
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
        self.height_map_abort_requested = False
        self.height_map_probe_phase = None
        self.height_map_completed = False
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

        if self.height_map_abort_requested:
            self.finish_height_map_sequence()
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
            if self.height_map_abort_requested:
                self.finish_height_map_sequence()
                return
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
        if self.height_map_finishing:
            return
        self.height_map_waiting_for_probe = False
        self.height_map_probe_phase = None
        self.height_map_finishing = True
        status = "Aborting" if self.height_map_abort_requested else "Finishing"
        self.height_map_status_label.setText(
            f"{status} {len(self.height_map_points)}/{len(self.height_map_plan)}"
        )
        self.send_payload("V201 E0", force=True)
        for payload in self.return_to_z_switch_commands():
            self.send_payload(payload, force=True)
        self.send_payload("M400", force=True)
        self.update_control_states()

    def finish_height_map(self) -> None:
        aborted = self.height_map_abort_requested
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_abort_requested = False
        self.height_map_probe_phase = None
        self.motion_busy = False
        if aborted:
            self.height_map_completed = False
            status = self.aborted_height_map_summary()
            self.height_map_status_label.setText(status)
            self.append_log(f"# height map aborted: {status}")
        else:
            self.height_map_completed = (
                bool(self.height_map_points)
                and len(self.height_map_points) == len(self.height_map_plan)
            )
            self.height_map_status_label.setText(self.height_map_summary())
            self.append_log(f"# height map completed: {self.height_map_summary()}")
            if self.height_map_completed:
                self.maybe_warn_height_map_deviation()
        self.update_control_states()

    def maybe_warn_height_map_deviation(self) -> None:
        if not self.height_map_points:
            return
        heights = [height for _x, _y, height in self.height_map_points]
        height_range = max(heights) - min(heights)
        threshold = self.height_map_max_deviation_warning_mm
        if height_range <= threshold:
            return

        self.append_log(
            "! height map deviation warning: "
            f"{height_range:.6f} mm > {threshold:.6f} mm"
        )
        QMessageBox.warning(
            self,
            "Height Map Deviation",
            "Height map Z range is "
            f"{height_range:.4f} mm, exceeding the configured "
            f"{threshold:.4f} mm warning threshold.",
            QMessageBox.StandardButton.Ok,
        )

    def height_map_summary(self) -> str:
        if not self.height_map_points:
            return "No height map"
        heights = [height for _x, _y, height in self.height_map_points]
        return (
            f"{len(self.height_map_points)} points, "
            f"Z {min(heights):.4f}..{max(heights):.4f}"
        )

    def aborted_height_map_summary(self) -> str:
        if not self.height_map_points:
            return "Height map aborted: no points"
        return (
            "Height map aborted: "
            f"{len(self.height_map_points)}/{len(self.height_map_plan)} points"
        )

    def request_height_map_abort(self) -> None:
        if not self.height_map_active or self.height_map_finishing:
            return
        self.height_map_abort_requested = True
        self.height_map_status_label.setText(
            f"Aborting after current probe "
            f"{self.height_map_index}/{len(self.height_map_plan)}"
        )
        self.append_log("# height map abort requested")
        if not self.height_map_waiting_for_probe:
            self.finish_height_map_sequence()
        self.update_control_states()

    def cancel_height_map(self, status: str) -> None:
        if not self.height_map_active and not self.height_map_finishing:
            return
        self.height_map_active = False
        self.height_map_waiting_for_probe = False
        self.height_map_finishing = False
        self.height_map_abort_requested = False
        self.height_map_probe_phase = None
        self.height_map_completed = False
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

    def alignment_dialog_jog_xy(
        self,
        x_direction: float,
        y_direction: float,
    ) -> None:
        if self.current_x is None or self.current_y is None:
            self.append_log("! cannot jog alignment XY before receiving a positionUpdate")
            return
        if self.alignment_procedure_dialog is None:
            self.append_log("! alignment XY jog requires an active alignment dialog")
            return

        step = self.alignment_procedure_dialog.active_xy_step()
        if not math.isfinite(step) or step <= 0.0:
            self.append_log("! invalid alignment XY step")
            return

        target_x = clamp(
            self.current_x + x_direction * step,
            0.0,
            STAGE_X_MAX_MM,
        )
        target_y = clamp(
            self.current_y + y_direction * step,
            0.0,
            STAGE_Y_MAX_MM,
        )
        if (
            abs(target_x - self.current_x) <= 1e-9
            and abs(target_y - self.current_y) <= 1e-9
        ):
            self.append_log("! alignment XY jog would exceed stage bounds")
            return

        self.send_synchronized_motion(f"V1 X{target_x:.6f} Y{target_y:.6f}")

    def jog_z(self, dz: float) -> None:
        if self.current_z is None:
            self.append_log("! cannot jog Z before receiving a positionUpdate")
            return
        target = self.current_z + dz
        self.send_synchronized_motion(f"V1 Z{target:.6f}")

    def jog_e(self, de: float) -> None:
        self.send_synchronized_motion(f"V1 E{de:g}")

    def on_sent_line(self, line: str) -> None:
        self.append_log(f"> {line}")

    def on_received_line(self, line: str) -> None:
        self.append_log(f"< {line}")
        maximum_z = parse_maximum_z_position(line)
        if maximum_z is not None:
            self.maximum_z_position = maximum_z
            self.append_log(f"# maximum Z position: {maximum_z:.6f} mm")

        if line.strip() == "ok":
            self.consume_pattern_print_event()

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
            self.update_control_states()

    def on_error_line(self, line: str) -> None:
        self.motion_busy = False
        self.cancel_height_map("Height map failed")
        self.cancel_printing("Print failed", clear_queue=True)
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
