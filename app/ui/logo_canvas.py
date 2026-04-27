"""A QGraphicsView that lets you drag-drop a PNG, then move + resize it on
top of a 1080x1920 frame outline. Snaps to center lines and frame edges.

The view scales the 1080x1920 logical frame down to fit, but reports
positions in *frame* coords so the renderer can use them directly.
"""
from __future__ import annotations
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPixmap, QPainter, QPen, QColor, QBrush, QImage, QDragEnterEvent, QDropEvent
)
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsItem, QFrame
)
from pathlib import Path

from app.config import OUTPUT_W, OUTPUT_H


SNAP_THRESHOLD = 24      # logical px


class _LogoItem(QGraphicsPixmapItem):
    """Movable logo. Holds a uniform scale; a separate handle item resizes."""
    def __init__(self, pixmap: QPixmap):
        super().__init__(pixmap)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scale = 1.0
        self._opacity = 1.0
        self._on_change = None

    def set_scale(self, s: float):
        self._scale = max(0.05, s)
        self.setTransform(self.transform().fromScale(self._scale, self._scale))
        if self._on_change:
            self._on_change()

    def set_opacity(self, a: float):
        self._opacity = max(0.0, min(1.0, a))
        self.setOpacity(self._opacity)
        if self._on_change:
            self._on_change()

    def itemChange(self, change, value):
        # Snap on move
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            new_pos: QPointF = value
            br = self.boundingRect()
            w = br.width() * self._scale
            h = br.height() * self._scale
            cx = new_pos.x() + w / 2
            cy = new_pos.y() + h / 2
            # snap center to frame center / vertical thirds / horizontal thirds
            snaps_x = [OUTPUT_W / 2, OUTPUT_W * 0.25, OUTPUT_W * 0.75, w / 2, OUTPUT_W - w / 2]
            snaps_y = [OUTPUT_H / 2, OUTPUT_H * 0.25, OUTPUT_H * 0.75, h / 2, OUTPUT_H - h / 2,
                       100 + h / 2, OUTPUT_H - 100 - h / 2]
            for sx in snaps_x:
                if abs(cx - sx) < SNAP_THRESHOLD:
                    new_pos.setX(sx - w / 2)
                    break
            for sy in snaps_y:
                if abs(cy - sy) < SNAP_THRESHOLD:
                    new_pos.setY(sy - h / 2)
                    break
            value = new_pos
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self._on_change:
            self._on_change()
        return result


class LogoCanvas(QGraphicsView):
    """Drag-drop a PNG, position + resize, get back x/y/width/opacity."""
    changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(0, 0, OUTPUT_W, OUTPUT_H)
        self.setScene(self._scene)

        # background "phone frame" rectangle (so the user sees the canvas)
        bg = QGraphicsRectItem(0, 0, OUTPUT_W, OUTPUT_H)
        bg.setBrush(QBrush(QColor("#0e0e0e")))
        bg.setPen(QPen(QColor("#262626"), 4))
        self._scene.addItem(bg)

        # subtle center cross
        cross_pen = QPen(QColor(255, 255, 255, 25), 2, Qt.PenStyle.DashLine)
        cv = self._scene.addLine(OUTPUT_W / 2, 0, OUTPUT_W / 2, OUTPUT_H, cross_pen)
        ch = self._scene.addLine(0, OUTPUT_H / 2, OUTPUT_W, OUTPUT_H / 2, cross_pen)
        cv.setZValue(-1); ch.setZValue(-1)

        self._logo: _LogoItem | None = None
        self._logo_path: str = ""

    # ---- drag and drop ----
    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if p.lower().endswith((".png", ".webp", ".jpg", ".jpeg")):
                    e.acceptProposedAction()
                    return
        e.ignore()

    def dropEvent(self, e: QDropEvent):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith((".png", ".webp", ".jpg", ".jpeg")):
                self.load_logo(p)
                e.acceptProposedAction()
                return

    # ---- public API ----
    def load_logo(self, path: str):
        pm = QPixmap(path)
        if pm.isNull():
            return
        if self._logo is not None:
            self._scene.removeItem(self._logo)
        self._logo = _LogoItem(pm)
        # reasonable default size: 240px wide
        s = 240 / pm.width()
        self._logo.set_scale(s)
        self._logo.setPos(OUTPUT_W / 2 - (pm.width() * s) / 2, 200 - (pm.height() * s) / 2)
        self._scene.addItem(self._logo)
        self._logo._on_change = self._emit
        self._logo_path = path
        self._emit()

    def set_width(self, frame_px_width: int):
        if not self._logo:
            return
        s = max(20, frame_px_width) / self._logo.pixmap().width()
        # keep center
        br = self._logo.boundingRect()
        cx = self._logo.pos().x() + br.width() * self._logo._scale / 2
        cy = self._logo.pos().y() + br.height() * self._logo._scale / 2
        self._logo.set_scale(s)
        nw = br.width() * s
        nh = br.height() * s
        self._logo.setPos(cx - nw / 2, cy - nh / 2)

    def set_opacity_pct(self, pct: int):
        if not self._logo:
            return
        self._logo.set_opacity(pct / 100.0)

    def state(self) -> dict:
        if not self._logo:
            return {"path": "", "x": OUTPUT_W // 2, "y": 200, "width": 240, "opacity": 1.0}
        br = self._logo.boundingRect()
        s = self._logo._scale
        w = int(br.width() * s)
        h = int(br.height() * s)
        cx = int(self._logo.pos().x() + w / 2)
        cy = int(self._logo.pos().y() + h / 2)
        return {"path": self._logo_path, "x": cx, "y": cy, "width": w,
                "opacity": float(self._logo._opacity)}

    def apply_state(self, st: dict):
        if st.get("path") and Path(st["path"]).exists():
            self.load_logo(st["path"])
            if self._logo:
                pm = self._logo.pixmap()
                w = max(20, int(st.get("width", 240)))
                s = w / pm.width()
                self._logo.set_scale(s)
                h = pm.height() * s
                self._logo.setPos(st.get("x", OUTPUT_W // 2) - w / 2,
                                  st.get("y", 200) - h / 2)
                self._logo.set_opacity(float(st.get("opacity", 1.0)))

    # ---- helpers ----
    def _emit(self):
        self.changed.emit(self.state())

    def resizeEvent(self, e):
        # always fit the 1080x1920 frame inside the view with margin
        self.fitInView(QRectF(0, 0, OUTPUT_W, OUTPUT_H), Qt.AspectRatioMode.KeepAspectRatio)
        super().resizeEvent(e)
