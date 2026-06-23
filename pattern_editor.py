from __future__ import annotations

import csv
import io
import math
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QLineF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


PatternPoint = tuple[float, float, float]

EDITOR_MIN_SPAN_MM = 10.0
EDITOR_ZOOM_MIN = 0.05
EDITOR_ZOOM_MAX = 200.0
EDITOR_ZOOM_STEP = 1.2


def _finite_point(point: PatternPoint) -> bool:
    return all(math.isfinite(value) for value in point)


def _toolbar_icon(kind: str) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#263238"), 2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))

    if kind == "new":
        painter.drawRect(QRectF(6.0, 4.0, 11.0, 16.0))
        painter.drawLine(QLineF(17.0, 4.0, 20.0, 7.0))
        painter.drawLine(QLineF(17.0, 4.0, 17.0, 8.0))
        painter.drawLine(QLineF(17.0, 8.0, 20.0, 8.0))
        painter.drawLine(QLineF(12.0, 10.0, 12.0, 17.0))
        painter.drawLine(QLineF(8.5, 13.5, 15.5, 13.5))
    elif kind == "load":
        painter.drawRect(QRectF(4.0, 8.0, 16.0, 10.0))
        painter.drawLine(QLineF(6.0, 8.0, 9.0, 5.0))
        painter.drawLine(QLineF(9.0, 5.0, 14.0, 5.0))
        painter.drawLine(QLineF(12.0, 10.0, 12.0, 16.0))
        painter.drawLine(QLineF(9.0, 13.0, 12.0, 16.0))
        painter.drawLine(QLineF(15.0, 13.0, 12.0, 16.0))
    elif kind in {"save", "save_as"}:
        painter.drawRect(QRectF(5.0, 4.0, 14.0, 16.0))
        painter.drawLine(QLineF(8.0, 4.0, 8.0, 9.0))
        painter.drawLine(QLineF(8.0, 9.0, 16.0, 9.0))
        painter.drawRect(QRectF(8.0, 14.0, 8.0, 5.0))
        if kind == "save_as":
            painter.drawLine(QLineF(16.0, 4.0, 20.0, 8.0))
            painter.drawLine(QLineF(20.0, 8.0, 17.0, 11.0))
    elif kind == "apply":
        painter.drawLine(QLineF(5.0, 12.0, 10.0, 17.0))
        painter.drawLine(QLineF(10.0, 17.0, 19.0, 6.0))
    elif kind == "undo":
        painter.drawLine(QLineF(8.0, 7.0, 4.0, 11.0))
        painter.drawLine(QLineF(4.0, 11.0, 8.0, 15.0))
        painter.drawLine(QLineF(5.0, 11.0, 17.0, 11.0))
        painter.drawLine(QLineF(17.0, 11.0, 20.0, 15.0))
    elif kind == "redo":
        painter.drawLine(QLineF(16.0, 7.0, 20.0, 11.0))
        painter.drawLine(QLineF(20.0, 11.0, 16.0, 15.0))
        painter.drawLine(QLineF(19.0, 11.0, 7.0, 11.0))
        painter.drawLine(QLineF(7.0, 11.0, 4.0, 15.0))
    elif kind == "add":
        painter.drawLine(QLineF(12.0, 5.0, 12.0, 19.0))
        painter.drawLine(QLineF(5.0, 12.0, 19.0, 12.0))
    elif kind == "delete":
        painter.drawLine(QLineF(7.0, 8.0, 17.0, 8.0))
        painter.drawLine(QLineF(10.0, 5.0, 14.0, 5.0))
        painter.drawRect(QRectF(8.0, 8.0, 8.0, 11.0))
        painter.drawLine(QLineF(11.0, 11.0, 11.0, 17.0))
        painter.drawLine(QLineF(14.0, 11.0, 14.0, 17.0))
    elif kind == "copy":
        painter.drawRect(QRectF(5.0, 7.0, 10.0, 12.0))
        painter.drawRect(QRectF(9.0, 4.0, 10.0, 12.0))
    elif kind == "paste":
        painter.drawRect(QRectF(6.0, 6.0, 12.0, 14.0))
        painter.drawRect(QRectF(9.0, 4.0, 6.0, 4.0))
        painter.drawLine(QLineF(9.0, 12.0, 15.0, 12.0))
        painter.drawLine(QLineF(9.0, 16.0, 15.0, 16.0))
    elif kind == "up":
        painter.drawLine(QLineF(12.0, 5.0, 12.0, 19.0))
        painter.drawLine(QLineF(12.0, 5.0, 7.0, 10.0))
        painter.drawLine(QLineF(12.0, 5.0, 17.0, 10.0))
    elif kind == "down":
        painter.drawLine(QLineF(12.0, 5.0, 12.0, 19.0))
        painter.drawLine(QLineF(12.0, 19.0, 7.0, 14.0))
        painter.drawLine(QLineF(12.0, 19.0, 17.0, 14.0))
    elif kind == "fit":
        painter.drawRect(QRectF(6.0, 6.0, 12.0, 12.0))
        painter.drawLine(QLineF(4.0, 9.0, 4.0, 4.0))
        painter.drawLine(QLineF(4.0, 4.0, 9.0, 4.0))
        painter.drawLine(QLineF(20.0, 15.0, 20.0, 20.0))
        painter.drawLine(QLineF(20.0, 20.0, 15.0, 20.0))
    elif kind == "close":
        painter.drawLine(QLineF(7.0, 7.0, 17.0, 17.0))
        painter.drawLine(QLineF(17.0, 7.0, 7.0, 17.0))

    painter.end()
    return QIcon(pixmap)


class PatternEditorView(QWidget):
    selection_changed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._points: list[PatternPoint] = []
        self._selected: set[int] = set()
        self._hovered_index: int | None = None
        self._zoom = 1.0
        self._base_span = 50.0
        self._center_x = 0.0
        self._center_y = 0.0
        self._panning = False
        self._last_pan_pos = None
        self._selection_start = None
        self._selection_current = None
        self._selection_press_index: int | None = None
        self._selection_base: set[int] = set()
        self._selection_modifiers = Qt.KeyboardModifier.NoModifier
        self._selection_dragging = False
        self.setMinimumSize(520, 420)
        self.setMouseTracking(True)

    def set_points(self, points: list[PatternPoint], fit: bool = False) -> None:
        self._points = list(points)
        self._selected = {
            index for index in self._selected if 0 <= index < len(self._points)
        }
        if fit:
            self.fit_to_points()
        self.update()

    def set_selected(self, indices, notify: bool = False) -> None:
        selected = {
            int(index)
            for index in indices
            if 0 <= int(index) < len(self._points)
        }
        if selected == self._selected:
            return
        self._selected = selected
        if notify:
            self.selection_changed.emit(sorted(self._selected))
        self.update()

    def selected_indices(self) -> set[int]:
        return set(self._selected)

    def fit_to_points(self) -> None:
        if not self._points:
            self._center_x = 0.0
            self._center_y = 0.0
            self._base_span = 50.0
            self._zoom = 1.0
            self.update()
            return

        xs = [point[0] for point in self._points]
        ys = [point[1] for point in self._points]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        self._center_x = (min(xs) + max(xs)) / 2.0
        self._center_y = (min(ys) + max(ys)) / 2.0
        self._base_span = max(EDITOR_MIN_SPAN_MM, width, height) * 1.35
        self._zoom = 1.0
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._selection_start = event.position()
            self._selection_current = event.position()
            self._selection_press_index = self._hit_point(
                event.position().x(),
                event.position().y(),
            )
            self._selection_base = set(self._selected)
            self._selection_modifiers = event.modifiers()
            self._selection_dragging = False
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._panning and self._last_pan_pos is not None:
            scale = self._scale()
            if scale > 0.0:
                delta = event.position() - self._last_pan_pos
                self._center_x -= delta.x() / scale
                self._center_y += delta.y() / scale
                self._last_pan_pos = event.position()
                self.update()
            event.accept()
            return

        if self._selection_start is not None:
            self._selection_current = event.position()
            delta = event.position() - self._selection_start
            if abs(delta.x()) > 3.0 or abs(delta.y()) > 3.0:
                self._selection_dragging = True
            self.update()
            event.accept()
            return

        hovered = self._hit_point(event.position().x(), event.position().y())
        if hovered != self._hovered_index:
            self._hovered_index = hovered
            self.update()
        self.setCursor(Qt.CursorShape.CrossCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._selection_start is not None:
                self._selection_current = event.position()
                if self._selection_dragging:
                    selected = self._selection_from_rect()
                else:
                    selected = self._selection_from_click()
                self.set_selected(selected, notify=True)
            self._clear_selection_drag()
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._last_pan_pos = None
            self.setCursor(Qt.CursorShape.CrossCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered_index = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        anchor_x, anchor_y = self._map_widget_to_pcs(
            event.position().x(),
            event.position().y(),
        )
        old_zoom = self._zoom
        factor = EDITOR_ZOOM_STEP if delta > 0 else 1.0 / EDITOR_ZOOM_STEP
        self._zoom = max(
            EDITOR_ZOOM_MIN,
            min(EDITOR_ZOOM_MAX, self._zoom * factor),
        )
        if abs(self._zoom - old_zoom) <= 1e-12:
            event.accept()
            return

        scale = self._scale()
        rect = self._content_rect()
        self._center_x = anchor_x - (event.position().x() - rect.center().x()) / scale
        self._center_y = anchor_y + (event.position().y() - rect.center().y()) / scale
        self.update()
        event.accept()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#f7f9fb"))

        rect = self._content_rect()
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setClipRect(rect)
        self._draw_grid(painter, rect)
        self._draw_axes(painter, rect)
        self._draw_points(painter)
        self._draw_selection_rect(painter)
        painter.setClipping(False)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.setPen(QPen(QColor("#68707a"), 1))
        painter.drawRect(rect)

    def _clear_selection_drag(self) -> None:
        self._selection_start = None
        self._selection_current = None
        self._selection_press_index = None
        self._selection_base = set()
        self._selection_modifiers = Qt.KeyboardModifier.NoModifier
        self._selection_dragging = False
        self.update()

    def _content_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(14.0, 14.0, -14.0, -14.0)

    def _visible_span(self) -> float:
        return max(EDITOR_MIN_SPAN_MM / EDITOR_ZOOM_MAX, self._base_span / self._zoom)

    def _scale(self) -> float:
        rect = self._content_rect()
        span = self._visible_span()
        return min(rect.width(), rect.height()) / max(span, 1e-9)

    def _map_pcs_to_widget(self, x: float, y: float) -> tuple[float, float]:
        rect = self._content_rect()
        scale = self._scale()
        return (
            rect.center().x() + (x - self._center_x) * scale,
            rect.center().y() - (y - self._center_y) * scale,
        )

    def _map_widget_to_pcs(self, px: float, py: float) -> tuple[float, float]:
        rect = self._content_rect()
        scale = self._scale()
        return (
            self._center_x + (px - rect.center().x()) / scale,
            self._center_y - (py - rect.center().y()) / scale,
        )

    def _visible_limits(self) -> tuple[float, float, float, float]:
        rect = self._content_rect()
        scale = self._scale()
        half_width = rect.width() / (2.0 * scale)
        half_height = rect.height() / (2.0 * scale)
        return (
            self._center_x - half_width,
            self._center_x + half_width,
            self._center_y - half_height,
            self._center_y + half_height,
        )

    def _grid_step(self) -> float:
        x_min, x_max, _y_min, _y_max = self._visible_limits()
        target = max((x_max - x_min) / 9.0, 1e-9)
        magnitude = 10.0 ** math.floor(math.log10(target))
        for multiplier in (1.0, 2.0, 5.0, 10.0):
            step = multiplier * magnitude
            if step >= target:
                return step
        return 10.0 * magnitude

    def _draw_grid(self, painter: QPainter, rect: QRectF) -> None:
        x_min, x_max, y_min, y_max = self._visible_limits()
        step = self._grid_step()
        grid_pen = QPen(QColor("#dfe4ea"), 1)
        grid_pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(grid_pen)

        first_x = math.floor(x_min / step) * step
        x = first_x
        while x <= x_max + step * 0.5:
            px, _py = self._map_pcs_to_widget(x, 0.0)
            painter.drawLine(QLineF(px, rect.top(), px, rect.bottom()))
            x += step

        first_y = math.floor(y_min / step) * step
        y = first_y
        while y <= y_max + step * 0.5:
            _px, py = self._map_pcs_to_widget(0.0, y)
            painter.drawLine(QLineF(rect.left(), py, rect.right(), py))
            y += step

    def _draw_axes(self, painter: QPainter, rect: QRectF) -> None:
        x_min, x_max, y_min, y_max = self._visible_limits()
        axis_pen = QPen(QColor("#9aa0a6"), 1.5)
        painter.setPen(axis_pen)
        if y_min <= 0.0 <= y_max:
            _x0, py = self._map_pcs_to_widget(0.0, 0.0)
            painter.drawLine(QLineF(rect.left(), py, rect.right(), py))
        if x_min <= 0.0 <= x_max:
            px, _y0 = self._map_pcs_to_widget(0.0, 0.0)
            painter.drawLine(QLineF(px, rect.top(), px, rect.bottom()))

        painter.setPen(QPen(QColor("#5f6368"), 1))
        painter.drawText(
            rect.adjusted(8.0, 6.0, -8.0, -6.0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            "PCS",
        )

    def _draw_points(self, painter: QPainter) -> None:
        point_radius = 4.5
        z_min, z_max = self._z_bounds()
        for index, (x, y, z) in enumerate(self._points):
            if not _finite_point((x, y, z)):
                continue
            px, py = self._map_pcs_to_widget(x, y)
            selected = index in self._selected
            hovered = index == self._hovered_index

            fill = self._z_color(z, z_min, z_max)
            outline = QColor("#0b3d66")
            if selected:
                outline = QColor("#b06000")
            elif hovered:
                outline = QColor("#006b6b")

            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(outline, 1.4))
            painter.drawEllipse(QRectF(
                px - point_radius,
                py - point_radius,
                point_radius * 2.0,
                point_radius * 2.0,
            ))

            if selected:
                painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                painter.setPen(QPen(QColor("#d93025"), 1.5))
                painter.drawEllipse(QRectF(px - 8.0, py - 8.0, 16.0, 16.0))

            painter.setPen(QPen(QColor("#202124"), 1))
            label_rect = QRectF(px + 6.0, py - 10.0, 42.0, 18.0)
            painter.drawText(
                label_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                str(index + 1),
            )

    def _z_bounds(self) -> tuple[float, float]:
        z_values = [
            z
            for x, y, z in self._points
            if _finite_point((x, y, z))
        ]
        if not z_values:
            return 0.0, 0.0
        return min(z_values), max(z_values)

    def _z_color(self, z: float, z_min: float, z_max: float) -> QColor:
        if z_max <= z_min:
            return QColor("#1f73b7")
        ratio = max(0.0, min(1.0, (z - z_min) / (z_max - z_min)))
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

    def _draw_selection_rect(self, painter: QPainter) -> None:
        if (
            self._selection_start is None
            or self._selection_current is None
            or not self._selection_dragging
        ):
            return

        rect = QRectF(self._selection_start, self._selection_current).normalized()
        painter.setBrush(QBrush(QColor(31, 115, 183, 35)))
        painter.setPen(QPen(QColor("#1f73b7"), 1.2, Qt.PenStyle.DashLine))
        painter.drawRect(rect)

    def _selection_from_click(self) -> set[int]:
        index = self._selection_press_index
        modifiers = self._selection_modifiers
        if index is None:
            if (
                modifiers & Qt.KeyboardModifier.ControlModifier
                or modifiers & Qt.KeyboardModifier.ShiftModifier
            ):
                return set(self._selection_base)
            return set()

        selected = set(self._selection_base)
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            selected.discard(index)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            if index in selected:
                selected.remove(index)
            else:
                selected.add(index)
        else:
            selected = {index}
        return selected

    def _selection_from_rect(self) -> set[int]:
        if self._selection_start is None or self._selection_current is None:
            return set(self._selection_base)

        rect = QRectF(self._selection_start, self._selection_current).normalized()
        hits = self._points_in_rect(rect)
        modifiers = self._selection_modifiers
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            return set(self._selection_base) - hits
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return set(self._selection_base) | hits
        return hits

    def _points_in_rect(self, rect: QRectF) -> set[int]:
        hits: set[int] = set()
        for index, (x, y, z) in enumerate(self._points):
            if not _finite_point((x, y, z)):
                continue
            px, py = self._map_pcs_to_widget(x, y)
            if rect.contains(px, py):
                hits.add(index)
        return hits

    def _hit_point(self, px: float, py: float) -> int | None:
        tolerance = 9.0
        best_index = None
        best_distance = tolerance
        for index in range(len(self._points) - 1, -1, -1):
            x, y, z = self._points[index]
            if not _finite_point((x, y, z)):
                continue
            point_x, point_y = self._map_pcs_to_widget(x, y)
            distance = math.hypot(px - point_x, py - point_y)
            if distance <= best_distance:
                best_index = index
                best_distance = distance
        return best_index


class PatternEditorDialog(QDialog):
    points_applied = Signal(object, object)

    def __init__(
        self,
        points: list[PatternPoint],
        file_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pattern Editor")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setSizeGripEnabled(True)
        self.resize(1120, 720)
        self.points = list(points)
        self.file_path = file_path
        self._updating_table = False
        self._updating_selection = False
        self._dirty = False
        self._closing_dialog = False
        self._undo_stack: list[
            tuple[list[PatternPoint], list[int], str | None, bool]
        ] = []
        self._redo_stack: list[
            tuple[list[PatternPoint], list[int], str | None, bool]
        ] = []

        self.view = PatternEditorView()
        self.table = QTableWidget()
        self.status_label = QLabel("")

        self.x_spin = self._coordinate_spin()
        self.y_spin = self._coordinate_spin()
        self.z_spin = self._coordinate_spin()
        self.dx_spin = self._coordinate_spin()
        self.dy_spin = self._coordinate_spin()
        self.dz_spin = self._coordinate_spin()
        self.dx_spin.setValue(0.0)
        self.dy_spin.setValue(0.0)
        self.dz_spin.setValue(0.0)

        self.order_spin = QSpinBox()
        self.dimension_combo = QComboBox()
        self.x_count_spin = QSpinBox()
        self.y_count_spin = QSpinBox()
        self.x_spacing_spin = self._spacing_spin()
        self.y_spacing_spin = self._spacing_spin()
        self.matrix_order_combo = QComboBox()
        self.replace_selection_check = QCheckBox("Replace selected dots")

        self._build_ui()
        self.disable_default_buttons()
        self._connect_ui()
        self.refresh_all(fit=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.new_button = QPushButton("New Pattern")
        self.load_button = QPushButton("Load CSV")
        self.save_button = QPushButton("Save")
        self.save_as_button = QPushButton("Save As")
        self.apply_button = QPushButton("Apply to App")
        self.undo_button = QPushButton("Undo")
        self.redo_button = QPushButton("Redo")
        self.add_button = QPushButton("Add")
        self.delete_button = QPushButton("Delete")
        self.copy_button = QPushButton("Copy")
        self.paste_button = QPushButton("Paste")
        self.move_up_button = QPushButton("Up")
        self.move_down_button = QPushButton("Down")
        self.fit_button = QPushButton("Fit")
        self.close_button = QPushButton("Close")
        for button in (
            self.new_button,
            self.load_button,
            self.save_button,
            self.save_as_button,
            self.apply_button,
            self.undo_button,
            self.redo_button,
            self.add_button,
            self.delete_button,
            self.copy_button,
            self.paste_button,
            self.move_up_button,
            self.move_down_button,
            self.fit_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.close_button)
        self.configure_toolbar_buttons()
        root.addLayout(toolbar)

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addWidget(self.view, stretch=1)
        body.addWidget(self._build_side_panel(), stretch=0)
        root.addLayout(body, stretch=1)
        root.addWidget(self.status_label)

    def configure_toolbar_buttons(self) -> None:
        toolbar_buttons = (
            (self.new_button, "New Pattern", "new"),
            (self.load_button, "Load CSV", "load"),
            (self.save_button, "Save", "save"),
            (self.save_as_button, "Save As", "save_as"),
            (self.apply_button, "Apply to App", "apply"),
            (self.undo_button, "Undo", "undo"),
            (self.redo_button, "Redo", "redo"),
            (self.add_button, "Add", "add"),
            (self.delete_button, "Delete", "delete"),
            (self.copy_button, "Copy", "copy"),
            (self.paste_button, "Paste", "paste"),
            (self.move_up_button, "Move Up", "up"),
            (self.move_down_button, "Move Down", "down"),
            (self.fit_button, "Fit View", "fit"),
            (self.close_button, "Close", "close"),
        )
        for button, tooltip, icon_name in toolbar_buttons:
            button.setText("")
            button.setToolTip(tooltip)
            button.setAccessibleName(tooltip)
            button.setIcon(_toolbar_icon(icon_name))
            button.setIconSize(QSize(20, 20))
            button.setFixedSize(32, 30)

    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(340)
        panel.setMaximumWidth(390)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["#", "X", "Y", "Z"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch,
            )
        layout.addWidget(self.table, stretch=1)

        edit_group = QGroupBox("Point Values")
        edit_layout = QGridLayout(edit_group)
        edit_layout.setContentsMargins(8, 8, 8, 8)
        edit_layout.setHorizontalSpacing(4)
        edit_layout.setVerticalSpacing(4)
        self.set_selected_button = QPushButton("Set Selected")
        self.set_x_button = QPushButton("Set X")
        self.set_y_button = QPushButton("Set Y")
        self.set_z_button = QPushButton("Set Z")
        self.add_from_values_button = QPushButton("Add Point")
        edit_layout.addWidget(QLabel("X"), 0, 0)
        edit_layout.addWidget(self.x_spin, 0, 1)
        edit_layout.addWidget(self.set_x_button, 0, 2)
        edit_layout.addWidget(QLabel("Y"), 1, 0)
        edit_layout.addWidget(self.y_spin, 1, 1)
        edit_layout.addWidget(self.set_y_button, 1, 2)
        edit_layout.addWidget(QLabel("Z"), 2, 0)
        edit_layout.addWidget(self.z_spin, 2, 1)
        edit_layout.addWidget(self.set_z_button, 2, 2)
        edit_layout.addWidget(self.add_from_values_button, 3, 0, 1, 2)
        edit_layout.addWidget(self.set_selected_button, 3, 2)
        layout.addWidget(edit_group)

        order_group = QGroupBox("Order")
        order_layout = QHBoxLayout(order_group)
        self.move_to_order_button = QPushButton("Move Selected")
        self.order_spin.setMinimum(1)
        self.order_spin.setMaximum(1)
        order_layout.addWidget(QLabel("To #"))
        order_layout.addWidget(self.order_spin)
        order_layout.addWidget(self.move_to_order_button)
        layout.addWidget(order_group)

        offset_group = QGroupBox("Offset Selected")
        offset_layout = QGridLayout(offset_group)
        offset_layout.setContentsMargins(8, 8, 8, 8)
        offset_layout.setHorizontalSpacing(4)
        offset_layout.setVerticalSpacing(4)
        self.offset_selected_button = QPushButton("Apply Offset")
        offset_layout.addWidget(QLabel("dX"), 0, 0)
        offset_layout.addWidget(self.dx_spin, 0, 1)
        offset_layout.addWidget(QLabel("dY"), 1, 0)
        offset_layout.addWidget(self.dy_spin, 1, 1)
        offset_layout.addWidget(QLabel("dZ"), 2, 0)
        offset_layout.addWidget(self.dz_spin, 2, 1)
        offset_layout.addWidget(self.offset_selected_button, 3, 0, 1, 2)
        layout.addWidget(offset_group)

        matrix_group = QGroupBox("Matrix")
        matrix_layout = QFormLayout(matrix_group)
        self.dimension_combo.addItems(["1D", "2D"])
        self.x_count_spin.setRange(1, 1000)
        self.x_count_spin.setValue(10)
        self.y_count_spin.setRange(1, 1000)
        self.y_count_spin.setValue(10)
        self.matrix_order_combo.addItems(
            [
                "X fast, Y slow",
                "X slow, Y fast",
                "X fast, Y slow zigzag",
                "X slow, Y fast zigzag",
            ]
        )
        self.replace_selection_check.setChecked(True)
        self.matrix_button = QPushButton("Create Matrix")
        self.matrix_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        matrix_layout.addRow("Dimension", self.dimension_combo)
        matrix_layout.addRow("X count", self.x_count_spin)
        matrix_layout.addRow("Y count", self.y_count_spin)
        matrix_layout.addRow("X spacing", self.x_spacing_spin)
        matrix_layout.addRow("Y spacing", self.y_spacing_spin)
        matrix_layout.addRow("Order", self.matrix_order_combo)
        matrix_layout.addRow("", self.replace_selection_check)
        matrix_layout.addRow("", self.matrix_button)
        layout.addWidget(matrix_group)
        return panel

    def _connect_ui(self) -> None:
        self.new_button.clicked.connect(self.new_pattern)
        self.load_button.clicked.connect(self.load_csv)
        self.save_button.clicked.connect(self.save_csv)
        self.save_as_button.clicked.connect(self.save_csv_as)
        self.apply_button.clicked.connect(self.apply_to_app)
        self.undo_button.clicked.connect(self.undo)
        self.redo_button.clicked.connect(self.redo)
        self.add_button.clicked.connect(self.add_point)
        self.add_from_values_button.clicked.connect(self.add_point)
        self.delete_button.clicked.connect(self.delete_selected)
        self.copy_button.clicked.connect(self.copy_selected)
        self.paste_button.clicked.connect(self.paste_points)
        self.move_up_button.clicked.connect(lambda: self.move_selected_by(-1))
        self.move_down_button.clicked.connect(lambda: self.move_selected_by(1))
        self.move_to_order_button.clicked.connect(self.move_selected_to_order)
        self.fit_button.clicked.connect(self.view.fit_to_points)
        self.close_button.clicked.connect(self.close)
        self.set_selected_button.clicked.connect(self.set_selected_values)
        self.set_x_button.clicked.connect(lambda: self.set_selected_coordinate(0))
        self.set_y_button.clicked.connect(lambda: self.set_selected_coordinate(1))
        self.set_z_button.clicked.connect(lambda: self.set_selected_coordinate(2))
        self.offset_selected_button.clicked.connect(self.offset_selected_values)
        self.matrix_button.clicked.connect(self.matrix_points)
        self.dimension_combo.currentIndexChanged.connect(
            lambda _index: self.update_dimension_controls()
        )
        self.table.itemChanged.connect(self.on_table_item_changed)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.view.selection_changed.connect(self.on_view_selection_changed)

    def reject(self) -> None:
        self.set_selected_rows([])

    def closeEvent(self, event) -> None:
        if self._closing_dialog:
            event.accept()
            return

        if not self.confirm_close():
            event.ignore()
            return

        self._closing_dialog = True
        self.done(QDialog.DialogCode.Rejected)
        event.accept()

    def confirm_close(self) -> bool:
        if not self.points and not self._dirty:
            return True

        message = (
            "The pattern editor has unsaved changes. Close anyway?"
            if self._dirty
            else "The pattern editor contains pattern data. Close anyway?"
        )
        response = QMessageBox.question(
            self,
            "Close Pattern Editor",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return response == QMessageBox.StandardButton.Yes

    def disable_default_buttons(self) -> None:
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _coordinate_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-10000.0, 10000.0)
        spin.setDecimals(6)
        spin.setSingleStep(0.1)
        spin.setSuffix(" mm")
        return spin

    def _spacing_spin(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-10000.0, 10000.0)
        spin.setDecimals(6)
        spin.setSingleStep(0.1)
        spin.setValue(1.0)
        spin.setSuffix(" mm")
        return spin

    def refresh_all(self, fit: bool = False) -> None:
        self.refresh_table()
        self.view.set_points(self.points, fit=fit)
        self.update_order_controls()
        self.update_dimension_controls()
        self.update_status()
        self.update_history_buttons()

    def refresh_table(self) -> None:
        self._updating_table = True
        try:
            self.table.setRowCount(len(self.points))
            for row, point in enumerate(self.points):
                number_item = QTableWidgetItem(str(row + 1))
                number_item.setFlags(
                    number_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                self.table.setItem(row, 0, number_item)
                for column, value in enumerate(point, start=1):
                    self.table.setItem(row, column, QTableWidgetItem(f"{value:.6f}"))
        finally:
            self._updating_table = False

    def update_status(self) -> None:
        name = Path(self.file_path).name if self.file_path else "unsaved pattern"
        dirty = " *" if self._dirty else ""
        self.status_label.setText(f"{name}{dirty} - {len(self.points)} dots")

    def update_history_buttons(self) -> None:
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(bool(self._undo_stack))
        if hasattr(self, "redo_button"):
            self.redo_button.setEnabled(bool(self._redo_stack))

    def update_dimension_controls(self) -> None:
        is_2d = self.dimension_combo.currentText() == "2D"
        self.y_count_spin.setEnabled(is_2d)
        self.y_spacing_spin.setEnabled(is_2d)

    def update_order_controls(self) -> None:
        self.order_spin.setMaximum(max(1, len(self.points)))

    def selected_indices(self) -> list[int]:
        model = self.table.selectionModel()
        if model is None:
            return sorted(self.view.selected_indices())
        rows = {index.row() for index in model.selectedRows()}
        if not rows:
            rows = set(self.view.selected_indices())
        return sorted(index for index in rows if 0 <= index < len(self.points))

    def set_selected_rows(self, indices: list[int]) -> None:
        valid_indices = sorted(
            {index for index in indices if 0 <= index < len(self.points)}
        )
        self._updating_selection = True
        try:
            self.table.clearSelection()
            selection_model = self.table.selectionModel()
            if selection_model is not None:
                flags = (
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows
                )
                for row in valid_indices:
                    index = self.table.model().index(row, 0)
                    selection_model.select(index, flags)
            if valid_indices:
                if selection_model is not None:
                    selection_model.setCurrentIndex(
                        self.table.model().index(valid_indices[0], 1),
                        QItemSelectionModel.SelectionFlag.NoUpdate,
                    )
                item = self.table.item(valid_indices[0], 0)
                if item is not None:
                    self.table.scrollToItem(
                        item,
                        QAbstractItemView.ScrollHint.PositionAtCenter,
                    )
        finally:
            self._updating_selection = False
        self.view.set_selected(valid_indices)
        self.update_value_spins_from_selection(valid_indices)

    def update_value_spins_from_selection(self, indices: list[int]) -> None:
        if not indices:
            return
        x, y, z = self.points[indices[0]]
        self.x_spin.setValue(x)
        self.y_spin.setValue(y)
        self.z_spin.setValue(z)
        self.order_spin.setValue(indices[0] + 1)

    def on_table_selection_changed(self) -> None:
        if self._updating_selection:
            return
        indices = self.selected_indices()
        self.view.set_selected(indices)
        self.update_value_spins_from_selection(indices)

    def on_view_selection_changed(self, indices) -> None:
        self.set_selected_rows(list(indices))

    def on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_table:
            return
        row = item.row()
        column = item.column()
        if column == 0 or not (0 <= row < len(self.points)):
            return

        try:
            value = float(item.text())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Number",
                "Point coordinates must be numeric.",
                QMessageBox.StandardButton.Ok,
            )
            self.refresh_table()
            return
        if not math.isfinite(value):
            QMessageBox.warning(
                self,
                "Invalid Number",
                "Point coordinates must be finite.",
                QMessageBox.StandardButton.Ok,
            )
            self.refresh_table()
            return

        x, y, z = self.points[row]
        values = [x, y, z]
        if abs(values[column - 1] - value) <= 1e-12:
            return
        self.push_undo_state()
        values[column - 1] = value
        self.points[row] = (values[0], values[1], values[2])
        self._mark_dirty()
        self.view.set_points(self.points)
        self.update_value_spins_from_selection(self.selected_indices())

    def add_point(self) -> None:
        point = (self.x_spin.value(), self.y_spin.value(), self.z_spin.value())
        insert_at = self.insertion_index()
        self.push_undo_state()
        self.points.insert(insert_at, point)
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows([insert_at])

    def insertion_index(self) -> int:
        selected = self.selected_indices()
        if selected:
            return max(selected) + 1
        return len(self.points)

    def delete_selected(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        self.push_undo_state()
        for index in reversed(selected):
            del self.points[index]
        next_index = min(selected[0], len(self.points) - 1)
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows([next_index] if next_index >= 0 else [])

    def copy_selected(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        text = self.points_to_csv_text([self.points[index] for index in selected])
        QApplication.clipboard().setText(text)

    def paste_points(self) -> None:
        text = QApplication.clipboard().text()
        try:
            points = self.parse_csv_text(text, allow_empty=False)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Paste Failed",
                f"Clipboard does not contain valid x,y[,z] points:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            return
        insert_at = self.insertion_index()
        self.push_undo_state()
        self.points[insert_at:insert_at] = points
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(list(range(insert_at, insert_at + len(points))))

    def set_selected_values(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        point = (self.x_spin.value(), self.y_spin.value(), self.z_spin.value())
        self.push_undo_state()
        for index in selected:
            self.points[index] = point
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(selected)

    def set_selected_coordinate(self, coordinate: int) -> None:
        selected = self.selected_indices()
        if not selected or coordinate not in (0, 1, 2):
            return
        value = (
            self.x_spin.value(),
            self.y_spin.value(),
            self.z_spin.value(),
        )[coordinate]
        if all(
            abs(self.points[index][coordinate] - value) <= 1e-12
            for index in selected
        ):
            return

        self.push_undo_state()
        for index in selected:
            values = list(self.points[index])
            values[coordinate] = value
            self.points[index] = (values[0], values[1], values[2])
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(selected)

    def offset_selected_values(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        dx = self.dx_spin.value()
        dy = self.dy_spin.value()
        dz = self.dz_spin.value()
        self.push_undo_state()
        for index in selected:
            x, y, z = self.points[index]
            self.points[index] = (x + dx, y + dy, z + dz)
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(selected)

    def move_selected_by(self, direction: int) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        selected_set = set(selected)
        if direction < 0:
            if selected[0] == 0:
                return
            self.push_undo_state()
            for index in selected:
                if index - 1 not in selected_set:
                    self.points[index - 1], self.points[index] = (
                        self.points[index],
                        self.points[index - 1],
                    )
            new_selected = [index - 1 for index in selected]
        else:
            if selected[-1] == len(self.points) - 1:
                return
            self.push_undo_state()
            for index in reversed(selected):
                if index + 1 not in selected_set:
                    self.points[index + 1], self.points[index] = (
                        self.points[index],
                        self.points[index + 1],
                    )
            new_selected = [index + 1 for index in selected]
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(new_selected)

    def move_selected_to_order(self) -> None:
        selected = self.selected_indices()
        if not selected:
            return
        target = max(0, min(len(self.points), self.order_spin.value() - 1))
        selected_set = set(selected)
        moving = [self.points[index] for index in selected]
        remaining = [
            point for index, point in enumerate(self.points)
            if index not in selected_set
        ]
        target = min(target, len(remaining))
        self.push_undo_state()
        self.points = remaining[:target] + moving + remaining[target:]
        new_selected = list(range(target, target + len(moving)))
        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(new_selected)

    def matrix_points(self) -> None:
        selected = self.selected_indices()
        if selected:
            seed_points = [self.points[index] for index in selected]
            insert_at = min(selected)
        else:
            seed_points = [
                (self.x_spin.value(), self.y_spin.value(), self.z_spin.value())
            ]
            insert_at = len(self.points)

        nx = self.x_count_spin.value()
        ny = (
            self.y_count_spin.value()
            if self.dimension_combo.currentText() == "2D"
            else 1
        )
        dx = self.x_spacing_spin.value()
        dy = self.y_spacing_spin.value() if ny > 1 else 0.0
        offsets = self.matrix_offsets(
            nx,
            ny,
            dx,
            dy,
            self.matrix_order_combo.currentText(),
        )

        generated: list[PatternPoint] = []
        for offset_x, offset_y in offsets:
            for x, y, z in seed_points:
                generated.append((x + offset_x, y + offset_y, z))

        self.push_undo_state()
        if selected and self.replace_selection_check.isChecked():
            selected_set = set(selected)
            remaining = [
                point for index, point in enumerate(self.points)
                if index not in selected_set
            ]
            self.points = remaining[:insert_at] + generated + remaining[insert_at:]
        else:
            insert_at = self.insertion_index()
            self.points[insert_at:insert_at] = generated

        self._mark_dirty()
        self.refresh_all()
        self.set_selected_rows(list(range(insert_at, insert_at + len(generated))))

    def matrix_offsets(
        self,
        nx: int,
        ny: int,
        dx: float,
        dy: float,
        order: str,
    ) -> list[tuple[float, float]]:
        offsets: list[tuple[float, float]] = []
        zigzag = "zigzag" in order.lower()
        if order.startswith("X fast"):
            for iy in range(ny):
                x_indices = list(range(nx))
                if zigzag and iy % 2 == 1:
                    x_indices.reverse()
                for ix in x_indices:
                    offsets.append((ix * dx, iy * dy))
        else:
            for ix in range(nx):
                y_indices = list(range(ny))
                if zigzag and ix % 2 == 1:
                    y_indices.reverse()
                for iy in y_indices:
                    offsets.append((ix * dx, iy * dy))
        return offsets

    def new_pattern(self) -> None:
        if self.points:
            response = QMessageBox.question(
                self,
                "New Pattern",
                "Create a new empty pattern? The current dots will be cleared.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return

        if not self.points and self.file_path is None and not self._dirty:
            return

        self.push_undo_state()
        self.points = []
        self.file_path = None
        self._dirty = False
        self.refresh_all(fit=True)
        self.set_selected_rows([])

    def load_csv(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Pattern CSV",
            "",
            "Pattern CSV (*.csv *.txt);;All Files (*)",
        )
        if not path:
            return
        try:
            points = self.read_csv_file(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self,
                "Load Failed",
                f"Could not load pattern:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            return
        self.push_undo_state()
        self.points = points
        self.file_path = path
        self._dirty = False
        self.refresh_all(fit=True)
        self.set_selected_rows([])

    def save_csv(self) -> None:
        if not self.file_path:
            self.save_csv_as()
            return
        try:
            self.write_csv_file(Path(self.file_path), self.points)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not save pattern:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            return
        self._dirty = False
        self.update_status()

    def save_csv_as(self) -> None:
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Pattern CSV",
            self.file_path or "pattern.csv",
            "Pattern CSV (*.csv);;All Files (*)",
        )
        if not path:
            return
        if "." not in Path(path).name:
            path += ".csv"
        try:
            self.write_csv_file(Path(path), self.points)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Save Failed",
                f"Could not save pattern:\n{exc}",
                QMessageBox.StandardButton.Ok,
            )
            return
        self.file_path = path
        self._dirty = False
        self.update_status()

    def apply_to_app(self) -> None:
        self.points_applied.emit(list(self.points), self.file_path)
        self.update_status()

    def history_state(self) -> tuple[list[PatternPoint], list[int], str | None, bool]:
        return (
            list(self.points),
            self.selected_indices(),
            self.file_path,
            self._dirty,
        )

    def push_undo_state(self) -> None:
        self._undo_stack.append(self.history_state())
        self._redo_stack.clear()
        self.update_history_buttons()

    def restore_history_state(
        self,
        state: tuple[list[PatternPoint], list[int], str | None, bool],
    ) -> None:
        points, selected, file_path, dirty = state
        self.points = list(points)
        self.file_path = file_path
        self._dirty = dirty
        self.refresh_all()
        self.set_selected_rows(selected)

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(self.history_state())
        self.restore_history_state(self._undo_stack.pop())
        self.update_history_buttons()

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(self.history_state())
        self.restore_history_state(self._redo_stack.pop())
        self.update_history_buttons()

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.New):
            self.new_pattern()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selected()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_points()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Save):
            self.save_csv()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Open):
            self.load_csv()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.set_selected_rows(list(range(len(self.points))))
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.set_selected_rows([])
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self.update_status()

    @staticmethod
    def read_csv_file(path: Path) -> list[PatternPoint]:
        with path.open("r", encoding="utf-8", newline="") as file:
            return PatternEditorDialog.parse_csv_text(file.read(), allow_empty=False)

    @staticmethod
    def write_csv_file(path: Path, points: list[PatternPoint]) -> None:
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["x", "y", "z"])
            for x, y, z in points:
                writer.writerow([f"{x:.6f}", f"{y:.6f}", f"{z:.6f}"])

    @staticmethod
    def points_to_csv_text(points: list[PatternPoint]) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for x, y, z in points:
            writer.writerow([f"{x:.6f}", f"{y:.6f}", f"{z:.6f}"])
        return buffer.getvalue()

    @staticmethod
    def parse_csv_text(text: str, allow_empty: bool = True) -> list[PatternPoint]:
        points: list[PatternPoint] = []
        reader = csv.reader(io.StringIO(text))
        for line_number, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue
            first = row[0].strip()
            if first.startswith("#"):
                continue
            if first.lower() in {"x", "pcs_x"}:
                continue
            if len(row) < 2:
                raise ValueError(f"line {line_number}: expected x,y[,z]")
            try:
                point = (
                    float(row[0].strip()),
                    float(row[1].strip()),
                    float(row[2].strip()) if len(row) >= 3 and row[2].strip() else 0.0,
                )
            except ValueError as exc:
                raise ValueError(f"line {line_number}: invalid number") from exc
            if not _finite_point(point):
                raise ValueError(f"line {line_number}: non-finite value")
            points.append(point)
        if not points and not allow_empty:
            raise ValueError("no points found")
        return points
