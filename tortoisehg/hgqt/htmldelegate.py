# htmldelegate.py - HTML QStyledItemDelegate
#
# Copyright 2010 Steve Borho <steve@borho.org>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

from __future__ import annotations

from .qtcore import (
    QPointF,
    QSize,
)
from .qtgui import (
    QAbstractTextDocumentLayout,
    QPalette,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTextDocument,
)

class HTMLDelegate(QStyledItemDelegate):

    def paint(self, painter, option, index):
        # draw selection
        option = QStyleOptionViewItem(option)
        self.parent().style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter)

        # draw text
        doc = self._builddoc(option, index)
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(QPointF(
            option.rect.left(),
            option.rect.top() + (option.rect.height() - doc.size().height()) / 2))
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette = option.palette
        if option.state & QStyle.StateFlag.State_Selected:
            if option.state & QStyle.StateFlag.State_Active:
                ctx.palette.setCurrentColorGroup(QPalette.ColorGroup.Active)
            else:
                ctx.palette.setCurrentColorGroup(QPalette.ColorGroup.Inactive)
            ctx.palette.setBrush(QPalette.ColorRole.Text, ctx.palette.highlightedText())
        elif not option.state & QStyle.StateFlag.State_Enabled:
            ctx.palette.setCurrentColorGroup(QPalette.ColorGroup.Disabled)

        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):
        doc = self._builddoc(option, index)
        return QSize(int(doc.idealWidth() + 5), int(doc.size().height()))

    def _builddoc(self, option, index):
        doc = QTextDocument(defaultFont=option.font)
        doc.setHtml(index.data())
        return doc
