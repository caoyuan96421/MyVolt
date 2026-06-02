from __future__ import annotations

import queue
import sys

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
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
    QStatusBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import serial
from serial.tools import list_ports

from myvolt_protocol import (
    FIXED_BAUD_RATE,
    LINE_TERMINATOR,
    frame_command,
    is_error_line,
    parse_homed_status,
    parse_position,
    parse_probe_measurement,
    parse_temperature,
    parse_tool_status,
)


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
                return

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
        for _ in range(20):
            try:
                _, _, payload = self._commands.get_nowait()
            except queue.Empty:
                return

            frame = frame_command(self._sequence, payload)
            self._sequence += 1
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
        self.received_line.emit(line)
        if is_error_line(line):
            self.error_line.emit(line)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MyVolt Controller")
        self.resize(980, 720)

        self.serial_thread: SerialThread | None = None
        self.current_x: float | None = None
        self.current_y: float | None = None
        self.current_z: float | None = None
        self.current_e: float | None = None
        self.current_tool_type: str | None = None
        self.all_axes_homed = False
        self.motion_busy = False
        self.general_command_widgets: list[QWidget] = []
        self.setup_motion_widgets: list[QWidget] = []
        self.homed_motion_widgets: list[QWidget] = []

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
        self.error_label = QLabel("")

        self._build_ui()
        self._connect_ui()
        self.refresh_ports()
        self.update_control_states()

    def _build_ui(self) -> None:
        root = QWidget()
        main_layout = QVBoxLayout(root)

        connection_group = QGroupBox("Connection")
        connection_layout = QHBoxLayout(connection_group)
        self.refresh_button = QPushButton("Refresh")
        connection_layout.addWidget(QLabel("Port"))
        connection_layout.addWidget(self.port_combo, stretch=1)
        connection_layout.addWidget(self.refresh_button)
        connection_layout.addWidget(self.connect_button)
        connection_layout.addWidget(self.disconnect_button)
        self.refresh_button.clicked.connect(self.refresh_ports)
        main_layout.addWidget(connection_group)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self._build_machine_group())
        controls_layout.addWidget(self._build_temperature_group())
        controls_layout.addWidget(self._build_probe_group())
        main_layout.addLayout(controls_layout)

        jog_layout = QHBoxLayout()
        jog_layout.addWidget(self._build_xy_jog_group(), stretch=2)
        jog_layout.addWidget(self._build_z_e_jog_group(), stretch=1)
        main_layout.addLayout(jog_layout)

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

    def _build_machine_group(self) -> QGroupBox:
        group = QGroupBox("Machine")
        layout = QGridLayout(group)
        self.home_button = QPushButton("Home XY")
        self.prepare_probe_button = QPushButton("Prepare Probe")
        self.emergency_button = QPushButton("Emergency Stop")
        self.emergency_button.setObjectName("emergencyButton")
        layout.addWidget(self.home_button, 0, 0)
        layout.addWidget(self.prepare_probe_button, 0, 1)
        layout.addWidget(self.emergency_button, 1, 0, 1, 2)
        self.setup_motion_widgets.extend([self.home_button, self.prepare_probe_button])
        return group

    def _build_temperature_group(self) -> QGroupBox:
        group = QGroupBox("Temperature")
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

    def _build_probe_group(self) -> QGroupBox:
        group = QGroupBox("Probe")
        layout = QGridLayout(group)
        self.probe_fast_button = QPushButton("Probe R1")
        self.probe_slow_button = QPushButton("Probe R0.1")
        layout.addWidget(self.probe_fast_button, 0, 0)
        layout.addWidget(self.probe_slow_button, 0, 1)
        self.homed_motion_widgets.extend(
            [self.probe_fast_button, self.probe_slow_button]
        )
        return group

    def _build_xy_jog_group(self) -> QGroupBox:
        group = QGroupBox("XY Jog")
        layout = QGridLayout(group)
        self.xy_step = QDoubleSpinBox()
        self.xy_step.setRange(0.001, 50.0)
        self.xy_step.setDecimals(3)
        self.xy_step.setValue(1.0)
        self.xy_step.setSuffix(" mm")

        self.x_minus_button = QPushButton("X-")
        self.x_plus_button = QPushButton("X+")
        self.y_minus_button = QPushButton("Y-")
        self.y_plus_button = QPushButton("Y+")

        layout.addWidget(QLabel("Step"), 0, 0)
        layout.addWidget(self.xy_step, 0, 1)
        layout.addWidget(self.y_plus_button, 1, 1)
        layout.addWidget(self.x_minus_button, 2, 0)
        layout.addWidget(self.x_plus_button, 2, 2)
        layout.addWidget(self.y_minus_button, 3, 1)

        self.x_minus_button.clicked.connect(
            lambda: self.jog_xy(-self.xy_step.value(), 0.0)
        )
        self.x_plus_button.clicked.connect(lambda: self.jog_xy(self.xy_step.value(), 0.0))
        self.y_minus_button.clicked.connect(
            lambda: self.jog_xy(0.0, -self.xy_step.value())
        )
        self.y_plus_button.clicked.connect(lambda: self.jog_xy(0.0, self.xy_step.value()))
        self.homed_motion_widgets.extend(
            [
                self.xy_step,
                self.x_minus_button,
                self.x_plus_button,
                self.y_minus_button,
                self.y_plus_button,
            ]
        )
        return group

    def _build_z_e_jog_group(self) -> QGroupBox:
        group = QGroupBox("Z / E Jog")
        layout = QFormLayout(group)
        self.z_step = QDoubleSpinBox()
        self.z_step.setRange(0.001, 10.0)
        self.z_step.setDecimals(3)
        self.z_step.setValue(0.1)
        self.z_step.setSuffix(" mm")
        self.e_step = QDoubleSpinBox()
        self.e_step.setRange(0.0001, 10.0)
        self.e_step.setDecimals(4)
        self.e_step.setValue(0.01)

        z_buttons = QHBoxLayout()
        self.z_minus_button = QPushButton("Z-")
        self.z_plus_button = QPushButton("Z+")
        z_buttons.addWidget(self.z_minus_button)
        z_buttons.addWidget(self.z_plus_button)

        e_buttons = QHBoxLayout()
        self.e_minus_button = QPushButton("E-")
        self.e_plus_button = QPushButton("E+")
        e_buttons.addWidget(self.e_minus_button)
        e_buttons.addWidget(self.e_plus_button)

        layout.addRow("Z step", self.z_step)
        layout.addRow("", z_buttons)
        layout.addRow("E step", self.e_step)
        layout.addRow("", e_buttons)

        self.z_minus_button.clicked.connect(lambda: self.jog_z(-self.z_step.value()))
        self.z_plus_button.clicked.connect(lambda: self.jog_z(self.z_step.value()))
        self.e_minus_button.clicked.connect(lambda: self.jog_e(-self.e_step.value()))
        self.e_plus_button.clicked.connect(lambda: self.jog_e(self.e_step.value()))
        self.homed_motion_widgets.extend(
            [
                self.z_step,
                self.e_step,
                self.z_minus_button,
                self.z_plus_button,
                self.e_minus_button,
                self.e_plus_button,
            ]
        )
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

    def connect_serial(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "No Port", "Select a serial port first.")
            return

        self.all_axes_homed = False
        self.motion_busy = False
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
        self.all_axes_homed = False
        self.motion_busy = False
        self.homed_label.setText("Home: --")
        self.homed_label.setStyleSheet("")
        self.tool_label.setText("Dispenser: --")
        self.update_control_states()

    def send_payload(self, payload: str, urgent: bool = False) -> bool:
        if self.serial_thread is None:
            self.append_log(f"! not connected: {payload}")
            return False
        if self.motion_busy and not urgent:
            self.append_log(f"! command blocked during M400 synchronization: {payload}")
            return False
        self.serial_thread.enqueue(payload, urgent=urgent)
        return True

    def update_control_states(self) -> None:
        connected = self.serial_thread is not None
        can_issue = connected and not self.motion_busy
        homed_motion_allowed = can_issue and self.all_axes_homed

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
        if not self.ensure_probe_ready():
            return
        self.send_synchronized_motion("V3 Z", require_homed=False)

    def manual_probe(self, probe_option: str) -> None:
        if not self.ensure_probe_ready():
            return
        self.send_synchronized_motion(f"V4 {probe_option}")

    def emergency_stop(self) -> None:
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
        self.update_motion_sync(line)

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
            self.update_control_states()
            return

        tool = parse_tool_status(line)
        if tool is not None:
            self.current_tool_type = tool.tool_type
            if tool.version is None:
                self.tool_label.setText(f"Dispenser: {tool.tool_type}")
            else:
                self.tool_label.setText(f"Dispenser: {tool.tool_type} v{tool.version}")

    def on_error_line(self, line: str) -> None:
        self.motion_busy = False
        self.update_control_states()
        self.error_label.setText(f"ERROR: {line}")
        self.append_log(f"! {line}")

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event) -> None:
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
