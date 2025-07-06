import sys
import os
import json
import shutil
import hashlib
import platform
import pandas as pd
import subprocess
import openpyxl
import gc
import win32con
import win32file
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtWidgets import (QApplication,QMainWindow,QFileDialog,QVBoxLayout,QWidget,QPushButton,QLabel,QTableWidget,QTableWidgetItem,QHBoxLayout,QLineEdit,QSplitter,QMessageBox,QMenu,QHeaderView,QInputDialog,QAbstractItemView,QSizePolicy,QCompleter,QTextEdit,QDialog,QDialogButtonBox,QProgressDialog)
from PySide6.QtCore import (Qt,QPoint,QSize,QThread,Signal,QStringListModel,QObject,QBuffer,QByteArray,QIODevice,QTimer)
from PySide6.QtGui import (QPixmap,QMouseEvent,QImageReader,QDragEnterEvent,QDropEvent,QColor,QTextCursor,QMovie)
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 统一管理扩展名，便于维护
EXTS = {
    "supported": ['.ckpt', '.safetensors', '.pth', '.pt', '.bin', '.th', '.gguf'],
    "static_img": ['.png', '.jpg', '.jpeg', '.webp'],
    "dynamic_img": ['.gif'],
}

# 分类目录
CATEGORY_DIR = {
    'Checkpoint': 'Checkpoint',
    'LoRA': 'LoRA',
    'TextualInversion': 'TextualInversion',
    'VAE': 'VAE',
    'GGUF': 'GGUF',
    'Unknown': 'Unknown'
}

# 预览图扩展名
STATIC_PREVIEW_IMAGE_EXTS = [f'.preview{ext}' for ext in EXTS["static_img"]]
DYNAMIC_PREVIEW_IMAGE_EXTS = [f'.preview{ext}' for ext in EXTS["dynamic_img"]]
PREVIEW_IMAGE_EXTS = STATIC_PREVIEW_IMAGE_EXTS + DYNAMIC_PREVIEW_IMAGE_EXTS

# 所有模型文件扩展名
ALL_MODEL_EXTS = (
    EXTS["supported"] +
    STATIC_PREVIEW_IMAGE_EXTS +
    DYNAMIC_PREVIEW_IMAGE_EXTS +
    ['.json', '.metadata.json', '.civitai.info', '.html', '.txt', '.sha256']
)

SUPPORTED_EXTS = EXTS["supported"]
STATIC_IMAGE_EXTS = EXTS["static_img"]
DYNAMIC_IMAGE_EXTS = EXTS["dynamic_img"]

IMAGE_LABEL_STYLE = "background: transparent; border: 2px solid black;"

def win_path(path):
    """返回绝对路径并统一为反斜杠"""
    return os.path.normpath(os.path.abspath(path)).replace("/", "\\")

class PreviewImageWatcher(FileSystemEventHandler):
    def __init__(self, gui):
        super().__init__()
        self.gui = gui

    def on_any_event(self, event):
        row = self.gui.table.currentRow()
        if row < 0:
            return
        filename = self.gui.table.item(row, 1).text()
        orig_path = self.gui.table.item(row, 3).text()
        base_path = os.path.splitext(os.path.join(orig_path, filename))[0]
        for ext in PREVIEW_IMAGE_EXTS:
            preview_path = base_path + ext
            if os.path.abspath(event.src_path) == os.path.abspath(preview_path):
                self.gui.refresh_preview_signal.emit()
                break

class ImageLabel(QLabel):
    def __init__(self, parent=None, preview_type="static"):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 240)
        self.model_base_path = ""
        self.parent_gui = parent
        self.preview_type = preview_type  # 新增

        # 用于多图切换
        self.preview_paths = []
        self.current_index = 0

        # 左右箭头按钮
        self.left_btn = QPushButton("◀", self)
        self.right_btn = QPushButton("▶", self)
        self.left_btn.setFixedSize(32, 64)
        self.right_btn.setFixedSize(32, 64)
        self.left_btn.setStyleSheet("background:rgba(0,0,0,0.3);color:white;font-size:24px;border:none;")
        self.right_btn.setStyleSheet("background:rgba(0,0,0,0.3);color:white;font-size:24px;border:none;")
        self.left_btn.raise_()
        self.right_btn.raise_()
        self.left_btn.hide()
        self.right_btn.hide()
        self.left_btn.clicked.connect(self.show_prev_image)
        self.right_btn.clicked.connect(self.show_next_image)

    def mouseDoubleClickEvent(self, event):
        path = self.current_preview_path()
        if not path or not os.path.exists(path):
            return
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            if self.parent_gui and hasattr(self.parent_gui, "log"):
                self.parent_gui.log(f"打开图片失败: {path}, 错误: {e}")
            QMessageBox.warning(self, "打开失败", f"无法用系统看图工具打开图片：\n{path}\n{e}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 按钮始终在图片框左右两侧居中
        h = self.height()
        self.left_btn.move(0, (h - self.left_btn.height()) // 2)
        self.right_btn.move(self.width() - self.right_btn.width(), (h - self.right_btn.height()) // 2)

    def set_preview_images(self, paths, index=0):
        """设置可切换的图片路径列表，并显示第index张"""
        self.preview_paths = paths
        self.current_index = index if 0 <= index < len(paths) else 0
        self.update_image()
        # 箭头按钮显示逻辑
        if len(paths) > 1:
            self.left_btn.show()
            self.right_btn.show()
        else:
            self.left_btn.hide()
            self.right_btn.hide()

    def update_image(self):
        """显示当前索引的图片"""
        if not self.preview_paths:
            self.setText("无静态预览图\n拖放图片到此处")
            self.setPixmap(QPixmap())
            self.left_btn.hide()
            self.right_btn.hide()
            return
        path = self.preview_paths[self.current_index]
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setPixmap(pixmap)
            self.setText("")
        else:
            self.setText("图片加载失败")
        # 箭头按钮显示逻辑
        if len(self.preview_paths) > 1:
            self.left_btn.show()
            self.right_btn.show()
        else:
            self.left_btn.hide()
            self.right_btn.hide()

    def show_prev_image(self):
        if self.preview_paths:
            self.current_index = (self.current_index - 1) % len(self.preview_paths)
            self.update_image()
            if self.parent_gui and hasattr(self.parent_gui, "refresh_static_info_label"):
                self.parent_gui.refresh_static_info_label()

    def show_next_image(self):
        if self.preview_paths:
            self.current_index = (self.current_index + 1) % len(self.preview_paths)
            self.update_image()
            if self.parent_gui and hasattr(self.parent_gui, "refresh_static_info_label"):
                self.parent_gui.refresh_static_info_label()

    def current_preview_path(self):
        """返回当前显示的图片路径"""
        if self.preview_paths and 0 <= self.current_index < len(self.preview_paths):
            return self.preview_paths[self.current_index]
        return None

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction() 

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if QImageReader.imageFormat(file_path) or file_path.lower().endswith(DYNAMIC_IMAGE_EXTS):
                self.handle_preview_drop(file_path)
                break  
            
    # def mouseDoubleClickEvent(self, event):
    #     if not self.model_base_path:
    #         return
    #     # 只查找对应类型的扩展名
    #     if self.preview_type == "static":
    #         exts = STATIC_PREVIEW_IMAGE_EXTS
    #     elif self.preview_type == "dynamic":
    #         exts = DYNAMIC_PREVIEW_IMAGE_EXTS
    #     else:
    #         exts = PREVIEW_IMAGE_EXTS
    #     for ext in exts:
    #         img_path = self.model_base_path + ext
    #         if os.path.exists(img_path):
    #             try:
    #                 if platform.system() == "Windows":
    #                     os.startfile(img_path)
    #                 elif platform.system() == "Darwin":
    #                     subprocess.Popen(['open', img_path])
    #                 else:
    #                     subprocess.Popen(['xdg-open', img_path])
    #             except Exception as e:
    #                 if self.parent_gui:
    #                     self.parent_gui.log(f"打开图片失败: {img_path}, 错误: {e}")
    #                 QMessageBox.warning(self, "打开失败", f"无法用系统看图工具打开图片：\n{img_path}\n{e}")
    #             break

    def handle_preview_drop(self, src_path):

        def wrap_text(text, max_len=30):    
            return "<br>".join([text[i:i+max_len] for i in range(0, len(text), max_len)])
        if not self.model_base_path:
            return
        ext = os.path.splitext(src_path)[1].lower()
        preview_path = self.model_base_path + ".preview" + ext
        if os.path.abspath(src_path) == os.path.abspath(preview_path):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("操作无效")
            msg.setText("拖入的图片和当前模型的预览图是同一个文件，无法覆盖。")
            
            if self.parent_gui:
                self.parent_gui.log(f"拖入的图片和当前模型的预览图是同一个文件，无法覆盖: {src_path}")
            return
        if os.path.exists(preview_path):
            dlg = QDialog(self)
            dlg.setWindowTitle("选择保留的预览图")
            dlg.setMinimumSize(500, 350)
            dlg.setStyleSheet("background: white;")
            layout = QVBoxLayout()
            img_layout = QHBoxLayout()
            old_vbox = QVBoxLayout()
            old_label = QLabel("原有图片")
            old_label.setAlignment(Qt.AlignCenter)
            # 判断是否为GIF
            old_img = None
            old_gif_player = None
            if preview_path.lower().endswith(".gif"):
                old_gif_player = GifPlayer(preview_path)
                old_gif_player.setFixedSize(200, 200)
                old_img = old_gif_player
            else:
                old_img = QLabel()
                old_img.setFixedSize(200, 200)
                old_img.setAlignment(Qt.AlignCenter)
                old_img.setObjectName("previewImg")
                old_img.setStyleSheet("#previewImg { background: transparent; border: 2px solid black; }")
                old_pix = QPixmap(preview_path)
                if not old_pix.isNull():
                    old_img.setPixmap(old_pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    old_img.setText("图片加载失败")
            # 支持双击打开原有图片
            def open_old_img(event):
                if os.path.exists(preview_path):
                    try:
                        if platform.system() == "Windows":
                            os.startfile(preview_path)
                        elif platform.system() == "Darwin":
                            subprocess.Popen(['open', preview_path])
                        else:
                            subprocess.Popen(['xdg-open', preview_path])
                    except Exception as e:
                        QMessageBox.warning(self, "打开失败", f"无法用系统看图工具打开图片：\n{preview_path}\n{e}")
            old_img.mouseDoubleClickEvent = open_old_img

            old_name = QLabel()
            old_name.setText(f"<div align='left'>{wrap_text(os.path.basename(preview_path))}</div>")
            old_name.setTextFormat(Qt.TextFormat.RichText)
            old_name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            old_name.setMaximumWidth(200)
            old_name.setMinimumHeight(50)
            old_name.setStyleSheet("background: transparent; border: none;")
            old_vbox.addWidget(old_label)
            old_vbox.addWidget(old_img)
            old_vbox.addWidget(old_name)

            new_vbox = QVBoxLayout()
            new_label = QLabel("新图片")
            new_label.setAlignment(Qt.AlignCenter)
            # 判断新图片是否为GIF
            new_img = None
            new_gif_player = None
            if src_path.lower().endswith(".gif"):
                new_gif_player = GifPlayer(src_path)
                new_gif_player.setFixedSize(200, 200)
                new_img = new_gif_player
            else:
                new_img = QLabel()
                new_img.setFixedSize(200, 200)
                new_img.setAlignment(Qt.AlignCenter)
                new_img.setObjectName("previewImg")
                new_img.setStyleSheet("#previewImg { background: transparent; border: 2px solid black; }")
                new_pix = QPixmap(src_path)
                if not new_pix.isNull():
                    new_img.setPixmap(new_pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    new_img.setText("图片加载失败")
            def open_new_img(event):
                if os.path.exists(src_path):
                    try:
                        if platform.system() == "Windows":
                            os.startfile(src_path)
                        elif platform.system() == "Darwin":
                            subprocess.Popen(['open', src_path])
                        else:
                            subprocess.Popen(['xdg-open', src_path])
                    except Exception as e:
                        QMessageBox.warning(self, "打开失败", f"无法用系统看图工具打开图片：\n{src_path}\n{e}")
            new_img.mouseDoubleClickEvent = open_new_img

            new_name = QLabel()
            new_name.setText(f"<div align='left'>{wrap_text(os.path.basename(src_path))}</div>")
            new_name.setTextFormat(Qt.TextFormat.RichText)
            new_name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            new_name.setMaximumWidth(200)
            new_name.setMinimumHeight(50)
            new_name.setStyleSheet("background: transparent; border: none;")
            new_vbox.addWidget(new_label)
            new_vbox.addWidget(new_img)
            new_vbox.addWidget(new_name)

            old_label.setStyleSheet("background: transparent; border: none;")
            new_label.setStyleSheet("background: transparent; border: none;")
            old_img.setStyleSheet("background: transparent; border: 2px solid black;")
            new_img.setStyleSheet("background: transparent; border: 2px solid black;")
            img_layout.addLayout(old_vbox)
            img_layout.addLayout(new_vbox)
            layout.addLayout(img_layout)
            btn_layout = QHBoxLayout()
            btn_keep_old = QPushButton("保留原有图片")
            btn_keep_new = QPushButton("保留新图片")
            btn_keep_old.setStyleSheet("border: 2px solid#888; background:#f8f8f8;")
            btn_keep_new.setStyleSheet("border: 2px solid#888; background:#f8f8f8;")
            btn_layout.addWidget(btn_keep_old)
            btn_layout.addWidget(btn_keep_new)
            layout.addLayout(btn_layout)
            dlg.setLayout(layout)

            def keep_old():
                dlg.done(1)

            def keep_new():
                dlg.done(2)

            btn_keep_old.clicked.connect(keep_old)
            btn_keep_new.clicked.connect(keep_new)
            ret = dlg.exec()
            # 释放GifPlayer资源，防止文件占用
            def safe_release_gifplayer(player):
                try:
                    if player:
                        player.setMovie(None)
                        if player.movie:
                            player.movie.stop()
                            player.movie.deleteLater()
                            player.movie = None
                        if player.buffer:
                            player.buffer.close()
                            player.buffer.deleteLater()
                            player.buffer = None
                        # 不要手动清理 byte_array
                except Exception as e:
                    print(f"释放GifPlayer资源异常: {e}")

            safe_release_gifplayer(old_gif_player)
            safe_release_gifplayer(new_gif_player)
            
            if ret == 2:
                shutil.copy2(src_path, preview_path)
                if self.parent_gui:
                    self.parent_gui.log(f"覆盖预览图: {preview_path}")
        else:
            shutil.copy2(src_path, preview_path)
            if self.parent_gui:
                self.parent_gui.log(f"保存预览图: {preview_path}")
# 刷新预览和表格
        if self.parent_gui:
            self.parent_gui.refresh_preview_and_table()

class ModelTableWidget(QTableWidget):  
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(9)
        self.setHorizontalHeaderLabels(["图片", "文件名", "大小", "原路径", "类型", "版本", "已移动路径", "SHA256(前十位)", "SHA256"])
        self.setSortingEnabled(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setColumnWidth(0, 64)  
        self.verticalHeader().setDefaultSectionSize(30)
        self.setColumnWidth(1, 180) 
        self.setColumnWidth(2, 55)  
        self.setColumnWidth(3, 250) 
        self.setColumnWidth(4, 90)  
        self.setColumnWidth(5, 40)  
        self.setColumnWidth(6, 250) 
        self.setColumnWidth(7, 100) 
        self.setColumnWidth(8, 200) 

class Sha256BatchWorker(QThread):  
    progress_changed = Signal(int, int, str, str, str)
    finished = Signal(int, int)

    def __init__(self, file_list, parent=None):  
        super().__init__(parent)  
        self.file_list = file_list
        self._is_cancelled = False

    def run(self):  
        new_count = 0
        skip_count = 0
        total = len(self.file_list)
        for idx, (row, full_path) in enumerate(self.file_list):
            if self._is_cancelled:
                break
            sha_path = os.path.splitext(full_path)[0] + ".sha256"
            hashv = ""
            if os.path.exists(sha_path):
                try:
                    with open(sha_path, 'r') as f:
                        exist_hash = f.read().strip()

                    if len(exist_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in exist_hash):
                        hashv = exist_hash

                        self.progress_changed.emit(idx+1, total, hashv[:10], hashv, os.path.basename(full_path))
                        skip_count += 1
                        continue
                except Exception:
                    self.log(f"读取现有哈希值失败: {sha_path}, 错误: {e}")
                    pass
            hashv = self.calc_sha256(full_path)
            try:
                with open(sha_path, 'w') as f:
                    f.write(hashv)
            except Exception:
                hashv = ""
            self.progress_changed.emit(idx+1, total, hashv[:10], hashv, os.path.basename(full_path))
            new_count += 1
        self.finished.emit(new_count, skip_count)

    def calc_sha256(self, filepath):  
        h = hashlib.sha256()
        try:
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
            return h.hexdigest()
        except PermissionError:
            self.log(f"权限不足，无法读取文件: {filepath}")
            return ""
        except Exception as e:
            self.log(f"读取文件出错: {filepath}，原因: {e}")
            return ""

    def cancel(self):
        self._is_cancelled = True

class SingleSha256Worker(QThread):  
    finished = Signal(str, str)

    def __init__(self, full_path, filename, parent=None):
        super().__init__(parent)
        self.full_path = full_path
        self.filename = filename

    def run(self):  
        import hashlib  
        h = hashlib.sha256()
        try:
            with open(self.full_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    h.update(chunk)
            hashv = h.hexdigest()
        except Exception:
            hashv = ""
        self.finished.emit(hashv, self.filename)

class ScanWorker(QThread):  
    progress = Signal(int, int, str)
    finished = Signal(list, list)

    def __init__(self, model_dir):
        super().__init__()
        self.model_dir = model_dir
        self._is_cancelled = False  

    def run(self):  
        files_to_scan = []
        for root, _, files in os.walk(self.model_dir):
            for f in files:
                if self._is_cancelled:
                    self.finished.emit([], [])
                    return
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTS:
                    files_to_scan.append((root, f))
        scan_results = []
        filenames = []
        total = len(files_to_scan)
        for idx, (root, f) in enumerate(files_to_scan):
            if self._is_cancelled:
                self.finished.emit([], [])
                return
            full_path = os.path.join(root, f) 
            m_type = ModelClassifierGUI.detect_model_type_static(f)
            m_ver = ModelClassifierGUI.detect_model_version_static(f) 
            try:
                size_bytes = os.path.getsize(full_path)
                size_str = ModelClassifierGUI.format_file_size_static(size_bytes)
            except Exception as e:
                self.log(f"获取文件大小失败: {full_path}, 错误: {e}")
                size_str = "N/A"
            scan_results.append((full_path, f, m_type, m_ver, size_str, idx))
            filenames.append(f)
            self.progress.emit(idx+1, total, f)
        self.finished.emit(scan_results, filenames)

    def cancel(self):
        self._is_cancelled = True

class GifPlayer(QLabel):
    def __init__(self, gif_path: str, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.byte_array = None
        self.buffer = None
        self.movie = None
        self.set_gif(gif_path)

    def set_gif(self, gif_path):
        # 先安全释放旧资源
        self._release_movie()
        from PySide6.QtGui import QImageReader
        reader = QImageReader(gif_path)
        size = reader.size()
        width, height = size.width(), size.height()
        max_width, max_height = 240, 240
        w, h = width, height
        if h > max_height:
            w = int(w * max_height / h)
            h = max_height
        if w > max_width:
            h = int(h * max_width / w)
            w = max_width

        try:
            with open(gif_path, "rb") as f:
                gif_data = f.read()
            self.byte_array = QByteArray(gif_data)
            self.buffer = QBuffer(self.byte_array)
            self.buffer.open(QIODevice.ReadOnly)
            self.movie = QMovie(self.buffer)
            self.movie.setScaledSize(QSize(w, h))
            if not self.movie.isValid():
                self.setText("GIF加载失败")
                self.movie = None
            else:
                self.setMovie(self.movie)
                self.movie.start()
                self.repaint()  # 关键：强制刷新
        except Exception as e:
            self.setText("GIF加载失败")
            self.movie = None

    def _release_movie(self):
        # 断开 QLabel 和 QMovie 的绑定，安全释放
        try:
            self.setMovie(None)
            if self.movie:
                self.movie.stop()
                self.movie.deleteLater()
                self.movie = None
            if self.buffer:
                self.buffer.close()
                self.buffer.deleteLater()
                self.buffer = None
            self.byte_array = None
        except Exception:
            pass

    def closeEvent(self, event):
        self._release_movie()
        super().closeEvent(event)

class ModelClassifierGUI(QMainWindow):
    refresh_preview_signal = Signal()
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Stable Diffusion/ComfyUI模型分类管家")
        self.resize(1400, 800)
        self.model_dir = ""
        self.current_json_path = ""
        self.scan_results = []
        self.rename_history = {}  # 记录 {row: (old_base, new_base, ext, dir_path)}
        self.filter_text = "" 
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)
        top_bar = QHBoxLayout()
        self.select_dir_btn = QPushButton("选择模型目录")
        self.select_dir_btn.clicked.connect(self.select_model_directory)
        self.scan_btn = QPushButton("扫描模型")
        self.scan_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self.scan_models)
        self.export_btn = QPushButton("导出 Excel/JSON")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_results)
        self.path_label = QLabel("[ 未选择目录 ]")
        self.batch_sha256_btn = QPushButton("批量生成SHA256哈希值")
        self.batch_sha256_btn.setEnabled(False)
        self.batch_sha256_btn.clicked.connect(self.generate_sha256_batch)
        self.dup_btn = QPushButton("查找重复模型")
        self.dup_btn.setEnabled(False)
        self.dup_btn.clicked.connect(self.check_duplicates_with_sha256_check)
        self.del_empty_json_btn = QPushButton("删除空白json")
        self.del_empty_json_btn.setEnabled(False)
        self.del_empty_json_btn.clicked.connect(self.delete_empty_json_files)
        self.search_label = QLabel("搜索:")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("输入模型名称or哈希值...")
        self.search_box.textChanged.connect(self.filter_table)
        for btn in [self.select_dir_btn, self.scan_btn, self.export_btn, self.batch_sha256_btn, self.dup_btn, self.del_empty_json_btn]:
            top_bar.addWidget(btn)
        top_bar.addWidget(self.path_label)
        top_bar.addStretch()
        top_bar.addWidget(self.search_label)
        top_bar.addWidget(self.search_box)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = ModelTableWidget()
        self.table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        self.table.customContextMenuRequested.connect(self.show_context_menu) 
        self.preview_area = QWidget() 
        preview_layout = QVBoxLayout()
        self.preview_area.setLayout(preview_layout)   
        self.static_image_label = ImageLabel(self, preview_type="static")
        self.static_image_label.setStyleSheet(IMAGE_LABEL_STYLE)
        self.static_image_label.setMinimumSize(240, 240)
        self.dynamic_image_label = ImageLabel(self, preview_type="dynamic")
        self.dynamic_image_label.setStyleSheet(IMAGE_LABEL_STYLE)
        self.dynamic_image_label.setMinimumSize(240, 240)
        self.preview_info_label = QLabel("")
        self.description_input = QTextEdit()
        self.notes_input = QTextEdit()
        self.vae_input = QTextEdit()
        title_btn_layout = QHBoxLayout()
        self.static_info_label = QLabel("")
        self.static_info_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.dynamic_info_label = QLabel("")
        self.dynamic_info_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        # 设置默认内容，保证无论有没有模型都显示完整信息区
        self.static_image_label.setText("无静态预览图")
        self.dynamic_image_label.setText("无动态预览图")
        self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
        self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
        self.static_image_label.setFixedSize(240, 240)
        self.dynamic_image_label.setFixedSize(240, 240)
        self.static_info_label.setFixedWidth(240)
        self.dynamic_info_label.setFixedWidth(240)

        # 静态预览区
        static_vbox = QVBoxLayout()
        static_vbox.setAlignment(Qt.AlignTop)
        static_title_layout = QHBoxLayout()
        static_title = QLabel("静态预览区")
        static_title.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.delete_static_btn = QPushButton("删除静态预览图")
        self.delete_static_btn.setFixedWidth(120)
        self.delete_static_btn.clicked.connect(self.delete_static_preview)
        static_title.setStyleSheet("font-size: 20px; font-weight: bold;")
        static_title_layout.addWidget(static_title)
        static_title_layout.addStretch()
        static_title_layout.addWidget(self.delete_static_btn)
        static_vbox.addLayout(static_title_layout)
        static_vbox.addWidget(self.static_image_label)
        static_vbox.addWidget(self.static_info_label)

        # 动态预览区
        dynamic_vbox = QVBoxLayout()
        dynamic_vbox.setAlignment(Qt.AlignTop)
        # 标题+按钮横向布局
        dynamic_title_layout = QHBoxLayout()
        dynamic_title = QLabel("动态预览区")
        dynamic_title.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.delete_dynamic_btn = QPushButton("删除动态预览图")
        self.delete_dynamic_btn.setFixedWidth(120)
        self.delete_dynamic_btn.clicked.connect(self.delete_dynamic_preview)
        dynamic_title.setStyleSheet("font-size: 20px; font-weight: bold;")
        dynamic_title_layout.addWidget(dynamic_title)
        dynamic_title_layout.addStretch()
        dynamic_title_layout.addWidget(self.delete_dynamic_btn)
        dynamic_vbox.addLayout(dynamic_title_layout)
        dynamic_vbox.addWidget(self.dynamic_image_label)
        dynamic_vbox.addWidget(self.dynamic_info_label)

        img_info_layout = QHBoxLayout()
        img_info_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        img_info_layout.addLayout(static_vbox, 0)
        img_info_layout.addLayout(dynamic_vbox, 0)
        img_info_layout.addStretch()
        preview_layout.addLayout(img_info_layout)
        title_btn_layout.addStretch()
        preview_layout.addLayout(title_btn_layout)

        preview_layout.addWidget(QLabel("SHA256(前10):"))
        self.sha256_short_box = QLineEdit()
        self.sha256_short_box.setReadOnly(True)
        preview_layout.addWidget(self.sha256_short_box)
        preview_layout.addWidget(QLabel("SHA256(完整):"))
        self.sha256_full_box = QLineEdit()
        self.sha256_full_box.setReadOnly(True)
        preview_layout.addWidget(self.sha256_full_box)
        preview_layout.addWidget(QLabel("Description:"))
        preview_layout.addWidget(self.description_input)
        preview_layout.addWidget(QLabel("Notes:"))
        preview_layout.addWidget(self.notes_input)
        preview_layout.addWidget(QLabel("VAE:"))
        preview_layout.addWidget(self.vae_input)
        splitter.addWidget(self.table)
        splitter.addWidget(self.preview_area) 
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        main_layout.addLayout(top_bar)
        main_layout.addWidget(splitter)
        self.completer = QCompleter() 
        self.search_box.setCompleter(self.completer)  
        self.stats_label = QLabel("日志：") 
        main_layout.addWidget(self.stats_label)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFixedHeight(120)
        main_layout.addWidget(self.log_output)
        self._observer = None
        self._watch_path = None
        self._start_preview_watcher()
        # 连接自定义信号，文件变化时刷新预览区和表格缩略图
        self.refresh_preview_signal.connect(self.refresh_preview_and_table)
        # 连接表格点击信号，点击行时加载对应模型信息到右侧预览区
        self.table.cellClicked.connect(self.load_model_info)
        # 备注、说明、VAE输入框内容变化时自动保存到JSON
        self.description_input.textChanged.connect(self.auto_save_json)
        self.notes_input.textChanged.connect(self.auto_save_json)
        self.vae_input.textChanged.connect(self.auto_save_json)
        self._first_show = True

    @staticmethod
    def detect_model_type_static(fname):
        fname = fname.lower()
        if 'vae' in fname and ('ckpt' in fname or 'safetensors' in fname):
            return 'Checkpoint+VAE'
        if 'lora' in fname:
            return 'LoRA'
        if 'embedding' in fname:
            return 'TextualInversion'
        if 'vae' in fname:
            return 'VAE'
        if 'gguf' in fname:
            return 'GGUF'
        return 'Checkpoint'

    @staticmethod
    def detect_model_version_static(fname):
        fname = fname.lower()
        if '1.5' in fname or 'v1-5' in fname:
            return 'SD1.5'
        if '2.0' in fname or 'v2-0' in fname:
            return 'SD2.0'
        if 'sdxl' in fname or 'xl' in fname:
            return 'SDXL'
        if 'flux' in fname:
            return 'FLUX'
        return ''

    @staticmethod
    def format_file_size_static(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
        
    def showEvent(self, event):
        super().showEvent(event)
        if self._first_show and not self.model_dir:
            self._first_show = False
            QTimer.singleShot(100, self.select_model_directory)  # 延迟弹出，保证主窗口已显示
        
    def _on_table_cell_double_clicked(self, row, col):
    # 只允许双击“文件名”列（索引1）重命名
        if col == 1:
            self.rename_model(row)
        
    def update_row_by_path(self, old_path, new_name):
        """根据原始完整路径，刷新表格中对应行的文件名和相关信息"""
        for row in range(self.table.rowCount()):
            orig_path = self.table.item(row, 3).text()
            filename = self.table.item(row, 1).text()
            full_path = os.path.join(orig_path, filename)
            if os.path.normcase(os.path.normpath(full_path)) == os.path.normcase(os.path.normpath(old_path)):
                # 更新文件名
                self.table.setItem(row, 1, QTableWidgetItem(new_name))
                # 如有其它需要同步的列，也可在这里更新
                break

    def delete_static_preview(self):
        """删除当前选中模型的静态预览图（当前显示的那张）"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选中一个模型")
            return
        # 只删除当前显示的那张
        path = self.static_image_label.current_preview_path()
        if path and os.path.exists(path):
            reply = QMessageBox.question(self, "确认删除", f"确定要删除当前静态预览图？\n{path}", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                try:
                    os.remove(path)
                    self.log(f"已删除静态预览图: {path}")
                    self.refresh_preview_and_table()
                    # 刷新表格图片缩略图
                    filename = self.table.item(row, 1).text()
                    orig_path = self.table.item(row, 3).text()
                    base_path = os.path.splitext(os.path.join(orig_path, filename))[0]
                    preview_path, _ = self.find_preview_image(base_path)
                    image_item = QTableWidgetItem()
                    if preview_path:
                        pixmap = QPixmap(preview_path)
                        if not pixmap.isNull():
                            pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                            image_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
                    self.table.setItem(row, 0, image_item)
                except Exception as e:
                    self.log(f"删除静态预览图失败: {e}")
                    QMessageBox.warning(self, "删除失败", f"无法删除静态预览图：\n{e}")
            return
        QMessageBox.information(self, "提示", "未找到静态预览图")

    def delete_dynamic_preview(self):
        """删除当前选中模型的动态预览图（GIF）"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选中一个模型")
            return
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        use_path = moved_path or orig_path
        base = os.path.splitext(os.path.join(use_path, filename))[0]
        for ext in DYNAMIC_PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                reply = QMessageBox.question(self, "确认删除", f"确定要删除动态预览图？\n{path}", QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    try:
                        os.remove(path)
                        self.log(f"已删除动态预览图: {path}")
                        self.refresh_preview_and_table()
                    except Exception as e:
                        self.log(f"删除动态预览图失败: {e}")
                        QMessageBox.warning(self, "删除失败", f"无法删除动态预览图：\n{e}")
                return
        QMessageBox.information(self, "提示", "未找到动态预览图")
        
    def _start_preview_watcher(self):
# 监控模型目录下所有文件变化
        if self._observer:
            self._observer.stop()
            self._observer.join()
        if not self.model_dir:
            return
        event_handler = PreviewImageWatcher(self)
        observer = Observer()
        observer.schedule(event_handler, self.model_dir, recursive=True)
        observer.start()
        self._observer = observer
        self._watch_path = self.model_dir
    
    def closeEvent(self, event):
        if self._observer:
            self._observer.stop()
            self._observer.join()
        super().closeEvent(event)

        # 在释放资源后强制刷新
    def release_gif_resource(self):
        try:
            if hasattr(self, "_gif_player") and self._gif_player:
                if self._gif_player.movie:
                    self.dynamic_image_label.setMovie(None)
                    self._gif_player.movie.stop()
                    self._gif_player.movie.deleteLater()
                    self._gif_player.movie = None
                if self._gif_player.buffer:
                    self._gif_player.buffer.close()
                    self._gif_player.buffer.deleteLater()
                    self._gif_player.buffer = None
                self._gif_player.byte_array = None
                del self._gif_player
                self._gif_player = None
            if hasattr(self, "_movie") and self._movie:
                self.dynamic_image_label.setMovie(None)
                self._movie.stop()
                self._movie.deleteLater()
                self._movie = None
            self.dynamic_image_label.setMovie(None)
            transparent = QPixmap(240, 240)
            transparent.fill(Qt.transparent)
            self.dynamic_image_label.setPixmap(transparent)
            self.dynamic_image_label.clear()
            self.dynamic_image_label.setText("无动态预览图\n拖放图片到此处")
            # ----------- 1. 强制刷新 label -----------
            self.dynamic_image_label.repaint()
            self.dynamic_image_label.update()
            QApplication.processEvents()
            # ----------- 2. 强制刷新父窗口 -----------
            parent = self.dynamic_image_label.parentWidget()
            if parent:
                parent.update()
                parent.repaint()
            window = self.dynamic_image_label.window()
            if window:
                window.update()
                window.repaint()
            QApplication.processEvents()
            # ----------------------------------------
        except Exception as e:
            self.log(f"释放GIF资源异常: {e}")
        gc.collect()
        
    def select_model_directory(self): 
        dir_path = QFileDialog.getExistingDirectory(self, "选择模型目录")
        if dir_path:
            self.model_dir = dir_path
            self.path_label.setText(dir_path)
            self.scan_btn.setEnabled(True)
            self.export_btn.setEnabled(True)
            self.batch_sha256_btn.setEnabled(True)
            self.dup_btn.setEnabled(True)
            self.del_empty_json_btn.setEnabled(True)
            self.scan_models()
            self._start_preview_watcher()

    def scan_models(self):
        if not self.model_dir:
            QMessageBox.warning(self, "警告", "请先选择模型目录")
            return
        self.table.setRowCount(0)
        self.scan_results.clear()
        self.filter_text = ""
        self.search_box.clear()
        self._fill_canceled = False
        self.progress_dialog = QProgressDialog("正在扫描模型...", "取消", 0, 100, self)
        self.progress_dialog.setWindowTitle("扫描进度")
        self.progress_dialog.setWindowModality(Qt.ApplicationModal)
        self.progress_dialog.setValue(0)
        self.progress_dialog.setMinimumWidth(420)
        self.progress_dialog.setMaximumWidth(420)
        self.progress_dialog.setMinimumHeight(120)
        self.progress_dialog.setMaximumHeight(120)
        self.progress_dialog.setSizeGripEnabled(False)
        self.progress_dialog.setLabelText("正在扫描模型...")
        self.progress_dialog.setCancelButtonText("取消")
        self.progress_dialog.canceled.connect(self._on_fill_cancel)
        self.progress_dialog.show()  # 关键：立即显示
        QApplication.processEvents() # 关键：强制刷新界面
        self.scan_worker = ScanWorker(self.model_dir) 
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.progress_dialog.canceled.connect(self.scan_worker.cancel)
        self.scan_btn.setEnabled(False)
        self.scan_worker.start()
        self.table.setRowCount(0)
        self.static_image_label.setText("无静态预览图")
        self.dynamic_image_label.setText("无动态预览图")
        self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
        self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")

    def _on_fill_cancel(self):
        self._fill_canceled = True
    
    def _on_scan_progress(self, idx, total, filename):
        self.progress_dialog.setMaximum(total)
        self.progress_dialog.setValue(idx)
        def wrap_text(text, max_len=30):
            return "\n".join([text[i:i+max_len] for i in range(0, len(text), max_len)])
        label = f"正在扫描: {wrap_text(filename, 30)}\n({idx}/{total})"
        self.progress_dialog.setLabelText(label)
        QApplication.processEvents()
        
    def _on_scan_finished(self, scan_results, filenames): 
        total = len(scan_results)
        self.progress_dialog.setMaximum(total)
        self.progress_dialog.setWindowTitle("表格填充进度")
        self.progress_dialog.setLabelText("正在填充表格...")
        self.progress_dialog.show()
        QApplication.processEvents()
        self.scan_results.clear()
        self._fill_canceled = False
        batch = 100
        for i in range(0, total, batch):
            for j in range(i, min(i+batch, total)):
                if self._fill_canceled:
                    self.progress_dialog.close()
                    self.scan_btn.setEnabled(True)
                    self.log("用户取消了表格填充")
                    return
                full_path, filename, m_type, m_ver, size_str, row = scan_results[j]
                row = self.table.rowCount()
                self.table.insertRow(row)
                base_path = os.path.splitext(full_path)[0]
                preview_path, preview_type = self.find_preview_image(base_path)
                image_item = QTableWidgetItem()
                if preview_path:
                    pixmap = QPixmap(preview_path)
                    if not pixmap.isNull():                        
                        pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        image_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
                self.table.setItem(row, 0, image_item)
                self.table.setItem(row, 1, QTableWidgetItem(filename))
                self.table.setItem(row, 2, QTableWidgetItem(size_str))
                self.table.setItem(row, 3, QTableWidgetItem(os.path.normpath(os.path.dirname(full_path))))
                self.table.setItem(row, 4, QTableWidgetItem(m_type))
                self.table.setItem(row, 5, QTableWidgetItem(m_ver))
                self.table.setItem(row, 6, QTableWidgetItem(""))
                item_content = filename
                item = QTableWidgetItem(item_content)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                item.setData(Qt.ItemDataRole.TextAlignmentRole, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(item_content)
                sha256_path = os.path.splitext(full_path)[0] + ".sha256"
                sha256_val = ""
                if os.path.exists(sha256_path):
                    try:
                        with open(sha256_path, "r") as fsha:
                            sha256_val = fsha.read().strip()
                    except Exception:
                        sha256_val = ""
                self.table.setItem(row, 7, QTableWidgetItem(sha256_val[:10] if sha256_val else ""))
                self.table.setItem(row, 8, QTableWidgetItem(sha256_val))
                self.scan_results.append((full_path, filename, m_type, m_ver, size_str, row))
                def wrap_text(text, max_len=30):
                    return "\n".join([text[i:i+max_len] for i in range(0, len(text), max_len)])
                self.progress_dialog.setValue(j+1)
                self.progress_dialog.setLabelText(f"正在填充表格({j+1}/{total}):\n{filename}")
            QApplication.processEvents()
        self.progress_dialog.close()
        self.scan_btn.setEnabled(True)
        self.log(f"已扫描 {len(self.scan_results)} 个模型文件")
        self.update_stats()

    def format_file_size(self, size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
        
    def find_preview_image(self, base_path):
        for ext in STATIC_IMAGE_EXTS:
            preview_path = base_path + ".preview" + ext
            if os.path.exists(preview_path):
                return preview_path, "static"
        for ext in DYNAMIC_PREVIEW_IMAGE_EXTS:
            gif_path = base_path + ext
            if os.path.exists(gif_path):
                return gif_path, "dynamic"# 这里返回字符串
        return None, None

    def detect_model_type(self, fname):# 检测模型类型
        fname = fname.lower()
        if 'vae' in fname and ('ckpt' in fname or 'safetensors' in fname):
            return 'Checkpoint+VAE'
        if 'lora' in fname:
            return 'LoRA'
        if 'embedding' in fname:
            return 'TextualInversion'
        if 'vae' in fname:
            return 'VAE'
        if 'gguf' in fname:
            return 'GGUF'
        return 'Checkpoint'

    def detect_model_version(self, fname):
        fname = fname.lower()
        if '1.5' in fname or 'v1-5' in fname:
            return 'SD1.5'
        if '2.0' in fname or 'v2-0' in fname:
            return 'SD2.0'
        if 'sdxl' in fname or 'xl' in fname:
            return 'SDXL'
        if 'flux' in fname:
            return 'FLUX'
        return ''

    def refresh_static_info_label(self):
        """刷新静态预览信息标签，显示当前图片信息"""
        path = self.static_image_label.current_preview_path()
        if path and os.path.exists(path):
            from PySide6.QtGui import QImageReader
            reader = QImageReader(path)
            size = reader.size()
            width, height = size.width(), size.height()
            file_size = os.path.getsize(path)
            ext = os.path.splitext(path)[1]
            info = (
                f"【静态预览】\n"
                f"尺寸：{width}x{height}\n"
                f"大小：{self.format_file_size(file_size)}\n"
                f"后缀名：{ext.lstrip('.')}\n"
            )
        else:
            info = "【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n"
        self.static_info_label.setText(info)

    def load_model_info(self, row, col):
        # 判断是否为分割行或空行
        if row < 0 or row >= self.table.rowCount():
            self.static_image_label.setText("无静态预览图")
            self.dynamic_image_label.setText("无动态预览图")
            self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            return
        # 关键：判断是否为分割行
        if not self.table.item(row, 1) or not self.table.item(row, 1).text():
            self.static_image_label.setText("无静态预览图")
            self.dynamic_image_label.setText("无动态预览图")
            self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            return

        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        use_path = moved_path or orig_path
        base = os.path.splitext(os.path.join(use_path, filename))[0]
        self.static_image_label.model_base_path = base
        self.dynamic_image_label.model_base_path = base
        # ----------- 静态预览多图切换 -----------
        static_preview_paths = []
        for ext in STATIC_PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                static_preview_paths.append(path)
        if static_preview_paths:
            self.static_image_label.set_preview_images(static_preview_paths, 0)
            # 刷新信息标签
            self.refresh_static_info_label()
        else:
            self.static_image_label.set_preview_images([])
            self.static_image_label.setText("无静态预览图\n拖放图片到此处")
            self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")

        # 动态预览
        dynamic_preview_path = None
        for ext in DYNAMIC_PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                dynamic_preview_path = path
                break
        dynamic_info = ""
        if dynamic_preview_path:
            try:
                # 初始化或复用 GifPlayer
                if not hasattr(self, "_gif_player") or self._gif_player is None:
                    gif_player = GifPlayer(dynamic_preview_path, self.dynamic_image_label)
                    if gif_player.movie:  # 只有加载成功才赋值
                        self._gif_player = gif_player
                    else:
                        self._gif_player = None
                        raise Exception("GIF加载失败")
                else:
                    if self._gif_player:
                        self._gif_player.set_gif(dynamic_preview_path)
                    else:
                        raise Exception("GIF加载失败")
                # 设置 Movie 到 label
                if self._gif_player and self._gif_player.movie:
                    self.dynamic_image_label.setMovie(self._gif_player.movie)
                    self.dynamic_image_label.setText("")
                    # 让 dynamic_image_label 记录当前 GIF 路径
                    self.dynamic_image_label.preview_paths = [dynamic_preview_path]
                    self.dynamic_image_label.current_index = 0
                    from PySide6.QtGui import QImageReader
                    reader = QImageReader(dynamic_preview_path)
                    size = reader.size()
                    width, height = size.width(), size.height()
                    file_size = os.path.getsize(dynamic_preview_path)
                    ext = os.path.splitext(dynamic_preview_path)[1]
                    dynamic_info = (
                        f"【动态预览】\n"
                        f"尺寸：{width}x{height}\n"
                        f"大小：{self.format_file_size(file_size)}\n"
                        f"后缀名：{ext.lstrip('.')}\n"
                    )
                    # 缩放逻辑
                    def scale_movie():
                        size = self._gif_player.movie.currentImage().size()
                        if size.width() > 0 and size.height() > 0:
                            max_height = 240
                            max_width = 240
                            w, h = size.width(), size.height()
                            if h > max_height:
                                w = int(w * max_height / h)
                                h = max_height
                            if w > max_width:
                                h = int(h * max_width / w)
                                w = max_width
                            self._gif_player.movie.setScaledSize(QSize(w, h))
                            self._gif_player.movie.frameChanged.disconnect(scale_movie)
                    self._gif_player.movie.frameChanged.connect(scale_movie)
                    self._gif_player.movie.start()
                    self._gif_player.movie.jumpToFrame(0)
                else:
                    raise Exception("GIF加载失败")
            except Exception as e:
                self.dynamic_image_label.setText("GIF加载失败")
                dynamic_info = "【动态预览】\nGIF加载失败\n"
                self.log(f"动态预览图像加载失败：{dynamic_preview_path} {e}")
        else:
            self.dynamic_image_label.setText("无动态预览图\n拖放图片到此处")
            dynamic_info = "【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n"
        # self.static_info_label.setText(static_info)
        self.dynamic_info_label.setText(dynamic_info)
        json_path = base + ".json"
        civitai_info = self.merge_civitai_info(base)
        self.current_json_path = json_path
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data.update({k: v for k, v in civitai_info.items() if v})
            except Exception as e:
                data = civitai_info
                self.log(f"加载JSON文件出错: {e}")
        else:
            data = civitai_info
        self.description_input.blockSignals(True)
        self.notes_input.blockSignals(True)
        self.vae_input.blockSignals(True)
        self.description_input.setText(data.get("description", ""))
        self.notes_input.setText(data.get("notes", ""))
        self.vae_input.setText(data.get("vae", ""))
        self.description_input.blockSignals(False)
        self.notes_input.blockSignals(False)
        self.vae_input.blockSignals(False)
        sha256_path = base + ".sha256"
        sha256_val = ""
        if os.path.exists(sha256_path):
            try:
                with open(sha256_path, "r") as fsha:
                    sha256_val = fsha.read().strip()
            except Exception:
                sha256_val = ""
        self.sha256_short_box.setText(sha256_val[:10] if sha256_val else "")
        self.sha256_full_box.setText(sha256_val)
        self.refresh_preview_buttons()

    def merge_civitai_info(self, base_path):
        path = base_path + ".civitai.info"
        if not os.path.exists(path): 
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                info = json.load(f)
            return {
                "description": info.get("model", {}).get("description", ""),
                "vae": info.get("model", {}).get("vae", "")
            }
        except Exception as e:
            self.log(f"civitai.info 合并出错: {e}")
            return {}
        
    def save_notes(self):
        if not self.current_json_path: return
        try:
            data = {
                "description": self.description_input.toPlainText().strip(),
                "notes": self.notes_input.toPlainText().strip(),
                "vae": self.vae_input.toPlainText().strip()
            }
            with open(self.current_json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.log(f"Saved notes to {self.current_json_path}")
        except Exception as e:
            self.log(f"保存备注JSON失败: {e}")

    def export_results(self):
        if not self.scan_results:
            QMessageBox.warning(self, "提示", "无分析结果")
            return
        export_type, ok = QInputDialog.getItem(
            self, "选择导出类型", "请选择导出格式：", ["Excel (.xlsx)", "JSON (.json)"], 0, False
        )
        if not ok:
            return
        if export_type.startswith("Excel"):     
            file_filter = "Excel 文件 (*.xlsx)"
            default_name = "model_results.xlsx"
        else:
            file_filter = "JSON 文件 (*.json)"
            default_name = "model_results.json"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "选择导出文件路径", default_name, file_filter
        )
        if not save_path:
            return
        data = [{
            "模型名称": filename,
            "大小": size_str,
            "模型的路径": os.path.dirname(p),
            "类型": t,
            "版本": v
        } for p, filename, t, v, size_str, _ in self.scan_results]
        try:
            if export_type.startswith("Excel"):
                pd.DataFrame(data).to_excel(save_path, index=False)
            else:
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "导出成功", f"已导出到：\n{save_path}")
        except Exception as e:
            self.log(f"导出失败: {e}")
            QMessageBox.warning(self, "导出错误", str(e))

    def show_context_menu(self, pos):
        menu = QMenu(self)
        open_action = menu.addAction("打开模型文件")
        move_action = menu.addAction("移动")
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除该文件")
        gen_sha_action = menu.addAction("生成SHA256哈希值")
        undo_rename_action = menu.addAction("撤回重命名")
        undo_move_action = menu.addAction("撤销移动")
        # undo_delete_action = menu.addAction("撤销删除")
        import_html_action = menu.addAction("导入HTML文件")
        refresh_img_action = menu.addAction("刷新图片")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if not action:
            return
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        multi_selected = len(selected_rows) > 1
    
        # 获取选中模型名
        model_names = [self.table.item(r, 1).text() for r in selected_rows if self.table.item(r, 1)]
        model_names_str = "\n".join(model_names)
    
        if action == move_action:
            if multi_selected:
                reply = QMessageBox.question(
                    self, "批量移动",
                    f"确定要批量移动以下模型？\n\n{model_names_str}",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.batch_move_selected_models()
            else:
                self.move_selected_model(row)
            return
    
        if action == rename_action:
            if multi_selected:
                reply = QMessageBox.question(
                    self, "批量重命名",
                    f"确定要批量重命名以下模型？\n\n{model_names_str}",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.batch_rename_selected_models()
            else:
                self.rename_model(row)
            return
    
        if action == delete_action:
            if multi_selected:
                reply = QMessageBox.question(
                    self, "批量删除",
                    f"确定要批量删除以下模型及所有关联文件？\n\n{model_names_str}",
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.batch_delete_selected_models(selected_rows)
            else:
                # 单项删除，无需弹窗
                self.delete_single_model(row)
            return
    
        # if action == undo_delete_action:
        #     self.undo_last_delete()
        #     return
    
        if action == gen_sha_action:
            selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
            if len(selected_rows) > 1:
                # 多选，批量检测并生成
                need_gen = []
                for row in selected_rows:
                    filename = self.table.item(row, 1).text()
                    orig_path = self.table.item(row, 3).text()
                    full_path = os.path.join(orig_path, filename)
                    sha256_path = os.path.splitext(full_path)[0] + ".sha256"
                    hashv = ""
                    if os.path.exists(sha256_path):
                        try:
                            with open(sha256_path, "r") as fsha:
                                exist_hash = fsha.read().strip()
                            if len(exist_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in exist_hash):
                                continue  # 已有合法哈希值
                        except Exception:
                            pass
                    need_gen.append((row, full_path))
                if not need_gen:
                    QMessageBox.information(self, "SHA256", "所选模型的SHA256均已存在且合法，无需再生成。")
                    return
                progress = QProgressDialog("正在批量生成SHA256...", "取消", 0, len(need_gen), self)
                progress.setWindowTitle("进度")
                progress.setWindowModality(Qt.ApplicationModal)
                progress.setValue(0)
                self.sha256_worker = Sha256BatchWorker(need_gen)
                self.sha256_worker.progress_changed.connect(
                    lambda idx, total, short_hash, full_hash, filename: self._on_sha256_progress(idx, total, short_hash, full_hash, filename, need_gen, progress)
                )
                self.sha256_worker.finished.connect(
                    lambda new_count, skip_count: self._on_sha256_finished(progress, new_count, skip_count)
                )
                progress.canceled.connect(self.sha256_worker.cancel)
                self.sha256_worker.start()
                progress.exec()
            else:
                # 单选，走原有逻辑
                self.generate_sha256(row)
            return
    
        if action == undo_rename_action:
            self.undo_rename(row)
            return
    
        if action == undo_move_action:
            self.undo_last_move(row)
            return
    
        if action == import_html_action:
            self.import_html_for_model(row)
            return
    
        if action == refresh_img_action:
            self.refresh_row_image(row)
            return
    
        if action == open_action:
            self.open_model_file(row)
            return
    
    # 单项删除
    def delete_single_model(self, row):
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        use_path = moved_path if moved_path else orig_path
        full_path = os.path.join(use_path, filename)
        # 新增：弹出确认框
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除该模型及所有关联文件？\n\n{full_path}",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
    
        self.release_gif_resource()
        base_path = os.path.splitext(full_path)[0]
        import tempfile, uuid
        error = None
        trash_dir = os.path.join(tempfile.gettempdir(), "sd_model_trash", str(uuid.uuid4()))
        os.makedirs(trash_dir, exist_ok=True)
        moved_files = []
        try:
            for ext in ALL_MODEL_EXTS:
                file_to_delete = base_path + ext
                if os.path.exists(file_to_delete):
                    dst = os.path.join(trash_dir, os.path.basename(file_to_delete))
                    shutil.move(file_to_delete, dst)
                    moved_files.append((dst, file_to_delete))
            for dst, _ in moved_files:
                try:
                    os.remove(dst)
                except Exception as e:
                    self.log(f"彻底删除失败: {dst}, 错误: {e}")
            self.table.removeRow(row)
            self.static_image_label.setText("已删除")
            self.dynamic_image_label.setText("已删除")
            self.modified = True
            try:
                os.rmdir(trash_dir)
            except Exception:
                pass
            # 记录撤销信息
            self.last_deleted = {"files": moved_files, "row": row, "filename": filename}
        except Exception as e:
            for dst, orig in reversed(moved_files):
                if os.path.exists(dst):
                    try:
                        shutil.move(dst, orig)
                    except Exception as e2:
                        self.log(f"回滚删除失败: {e2}")
            self.log(f"删除失败: {e}")
            error = str(e)
        if error:
            QMessageBox.warning(self, "删除失败", f"无法删除文件，已回滚：\n{error}")
    
    # 批量删除
    def batch_delete_selected_models(self, rows):
        # rows为已排序的行号列表
        deleted_info = []
        for row in sorted(rows, reverse=True):
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            moved_path = self.table.item(row, 6).text()
            use_path = moved_path if moved_path else orig_path
            full_path = os.path.join(use_path, filename)
            base_path = os.path.splitext(full_path)[0]
            import tempfile, uuid
            trash_dir = os.path.join(tempfile.gettempdir(), "sd_model_trash", str(uuid.uuid4()))
            os.makedirs(trash_dir, exist_ok=True)
            moved_files = []
            try:
                for ext in ALL_MODEL_EXTS:
                    file_to_delete = base_path + ext
                    if os.path.exists(file_to_delete):
                        dst = os.path.join(trash_dir, os.path.basename(file_to_delete))
                        shutil.move(file_to_delete, dst)
                        moved_files.append((dst, file_to_delete))
                for dst, _ in moved_files:
                    try:
                        os.remove(dst)
                    except Exception as e:
                        self.log(f"彻底删除失败: {dst}, 错误: {e}")
                self.table.removeRow(row)
                deleted_info.append({"files": moved_files, "row": row, "filename": filename})
                try:
                    os.rmdir(trash_dir)
                except Exception:
                    pass
            except Exception as e:
                for dst, orig in reversed(moved_files):
                    if os.path.exists(dst):
                        try:
                            shutil.move(dst, orig)
                        except Exception as e2:
                            self.log(f"回滚删除失败: {e2}")
                self.log(f"批量删除失败: {e}")
        # 记录批量撤销信息
        self.last_deleted = deleted_info
    
    # 撤销删除
    def undo_last_delete(self):
        if not hasattr(self, "last_deleted") or not self.last_deleted:
            QMessageBox.information(self, "提示", "没有可撤销的删除操作。")
            return
        # 支持批量撤销
        if isinstance(self.last_deleted, list):
            for info in self.last_deleted:
                for dst, orig in info["files"]:
                    if os.path.exists(dst):
                        shutil.move(dst, orig)
                # 你可以在这里恢复表格行（如有需要）
            QMessageBox.information(self, "撤销删除", "已撤销批量删除。")
        else:
            for dst, orig in self.last_deleted["files"]:
                if os.path.exists(dst):
                    shutil.move(dst, orig)
            # 你可以在这里恢复表格行（如有需要）
            QMessageBox.information(self, "撤销删除", f"已撤销删除：{self.last_deleted['filename']}")
        self.last_deleted = None

    def refresh_row_image(self, row):
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        use_path = moved_path if moved_path else orig_path
        base_path = os.path.splitext(os.path.join(use_path, filename))[0]
        preview_path, _ = self.find_preview_image(base_path)
        image_item = QTableWidgetItem()
        if preview_path:
            pixmap = QPixmap(preview_path)
            if not pixmap.isNull():
                pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                image_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
        self.table.setItem(row, 0, image_item)
        self.log(f"已刷新图片缩略图: {filename}")

    def import_html_for_model(self, row):
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        use_path = moved_path if moved_path else orig_path
        base_name = os.path.splitext(filename)[0]
        html_path, _ = QFileDialog.getOpenFileName(self, "选择HTML文件", "", "HTML文件 (*.html *.htm)")
        if not html_path:
            return
        dst_path = os.path.join(use_path, base_name + ".html")
        try:
            shutil.copy2(html_path, dst_path)
            self.log(f"已导入HTML文件: {dst_path}")
            QMessageBox.information(self, "导入成功", f"已导入HTML文件为：\n{dst_path}")
        except Exception as e:
            self.log(f"导入HTML失败: {e}")
            QMessageBox.warning(self, "导入失败", f"导入HTML文件失败：\n{e}")

    def batch_move_selected_models(self):
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择要移动的模型")
            return
        # 记录移动前的唯一标识
        move_infos = []
        for row in selected_rows:
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            move_infos.append({"filename": filename, "orig_path": orig_path, "row": row})
        # 收集模型名
        model_names = [info["filename"] for info in move_infos]
        model_names_str = "\n".join(model_names)
        reply = QMessageBox.question(
            self,
            "确认批量移动",
            f"确定要批量移动以下 {len(selected_rows)} 个模型？\n\n模型列表：\n{model_names_str}",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            QMessageBox.information(self, "已取消", f"已取消批量移动操作。\n\n涉及模型：\n{model_names_str}")
            return
        target_dir = QFileDialog.getExistingDirectory(self, "选择目标目录", self.model_dir)
        if not target_dir:
            return
        success_infos = []
        fail_count = 0
        for info in move_infos:
            try:
                self.move_selected_model(info["row"], target_dir, show_message=False)
                success_infos.append({"filename": info["filename"], "target_dir": target_dir})
            except Exception as e:
                fail_count += 1
                self.log(f"批量移动失败: {e}")
        # 移动后刷新所有成功移动的行的图片（用新路径查找行号）
        for info in success_infos:
            # 查找新行号
            for row in range(self.table.rowCount()):
                fname = self.table.item(row, 1).text()
                moved_path = self.table.item(row, 6).text()
                if fname == info["filename"] and moved_path == win_path(info["target_dir"]):
                    self.refresh_row_image(row)
                    break
        if success_infos:
            QMessageBox.information(self, "批量移动完成", f"成功移动 {len(success_infos)} 个模型到:\n{win_path(target_dir)}")
        if fail_count > 0:
            QMessageBox.warning(self, "批量移动部分失败", f"有 {fail_count} 个模型移动失败，详情见日志。")

    def batch_delete_selected_models(self):  # 批量删除所选模型
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择要删除的模型")
            return
        # 收集模型名
        model_names = [self.table.item(row, 1).text() for row in selected_rows]
        model_names_str = "\n".join(model_names)
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除选中的 {len(selected_rows)} 个模型及所有关联文件？\n\n模型列表：\n{model_names_str}",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            QMessageBox.information(self, "已取消", f"已取消删除操作。\n\n涉及模型：\n{model_names_str}")
            return
        fail_names = []
        for row in selected_rows:
            self.release_gif_resource()
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            moved_path = self.table.item(row, 6).text()
            use_path = moved_path if moved_path else orig_path
            full_path = os.path.join(use_path, filename)
            base_path = os.path.splitext(full_path)[0]
            try:
                for ext in ALL_MODEL_EXTS:
                    file_to_delete = base_path + ext
                    if os.path.exists(file_to_delete):
                        try:
                            os.remove(file_to_delete)
                            self.log(f"已删除: {file_to_delete}")
                        except Exception as e:
                            self.log(f"删除失败: {file_to_delete}, 错误: {e}")
                            fail_names.append(filename)
                    else:
                        self.log(f"文件不存在，跳过: {file_to_delete}")
                self.table.removeRow(row)
                self.log(f"已从表格移除: {filename}")
            except Exception as e:
                fail_names.append(filename)
                self.log(f"批量删除失败: {filename}, 错误: {e}")
        self.static_image_label.setText("已删除")
        self.dynamic_image_label.setText("已删除")
        self.modified = True
        if fail_names:
            fail_names_str = "\n".join(fail_names)
            QMessageBox.warning(
                self,
                "批量删除部分失败",
                f"有 {len(fail_names)} 个模型删除失败，详情见日志。\n\n失败模型：\n{fail_names_str}"
            )
        else:
            QMessageBox.information(
                self,
                "批量删除完成",
                f"成功删除 {len(selected_rows)} 个模型：\n{model_names_str}"
            )
        self.log(f"批量删除完成，共处理 {len(selected_rows)} 个模型")

    def batch_rename_selected_models(self): # 批量重命名所选模型
        selected_rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()))
        if not selected_rows:
            QMessageBox.information(self, "提示", "请先选择要重命名的模型")
            return
        # 收集模型名
        model_names = [self.table.item(row, 1).text() for row in selected_rows]
        model_names_str = "\n".join(model_names)
        reply = QMessageBox.question(
            self,
            "确认批量重命名",
            f"确定要批量重命名以下 {len(selected_rows)} 个模型？\n\n模型列表：\n{model_names_str}",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            QMessageBox.information(self, "已取消", f"已取消批量重命名操作。\n\n涉及模型：\n{model_names_str}")
            return
        # 获取原文件名（不含扩展名）
        old_names = []
        file_exts = []
        for row in selected_rows:
            filename = self.table.item(row, 1).text()
            base, ext = os.path.splitext(filename)
            old_names.append(base)
            file_exts.append(ext)
        # 批量输入新前缀
        prefix, ok = QInputDialog.getText(self, "批量重命名", "输入新文件名前缀（自动编号）：", text="model_")
        if not ok or not prefix:
            return
        # 预生成新文件名，防止冲突
        new_names = []
        for i, ext in enumerate(file_exts):
            new_name = f"{prefix}{i+1}{ext}"
            new_names.append(new_name)
        # 检查是否有重名
        dir_paths = []
        for row in selected_rows:
            moved_path = self.table.item(row, 6).text()
            orig_path = self.table.item(row, 3).text()
            dir_paths.append(moved_path if moved_path else orig_path)
        for dir_path, new_name in zip(dir_paths, new_names):
            for ext in ALL_MODEL_EXTS:
                check_file = os.path.join(dir_path, os.path.splitext(new_name)[0] + ext)
                if os.path.exists(check_file):
                    QMessageBox.warning(self, "重命名冲突", f"已存在同名文件：\n{check_file}\n请换个前缀。")
                    return
        # 执行批量重命名
        moved_files = []
        try:
            for idx, row in enumerate(selected_rows):
                filename = self.table.item(row, 1).text()
                moved_path = self.table.item(row, 6).text()
                orig_path = self.table.item(row, 3).text()
                dir_path = moved_path if moved_path else orig_path
                base_old = os.path.splitext(filename)[0]
                new_base = os.path.splitext(new_names[idx])[0]
                file_ext = file_exts[idx]
                for ext in ALL_MODEL_EXTS:
                    old_file = os.path.join(dir_path, base_old + ext)
                    new_file = os.path.join(dir_path, new_base + ext)
                    if os.path.exists(old_file):
                        shutil.move(old_file, new_file)
                        moved_files.append((new_file, old_file, row, new_names[idx]))
                # 更新表格
                self.table.setItem(row, 1, QTableWidgetItem(new_names[idx]))
                # 记录重命名历史，便于撤回
                self.rename_history[row] = (base_old, new_base, file_ext, dir_path)
            self.modified = True
            self.log(f"批量重命名成功: {len(selected_rows)} 个模型")
            QMessageBox.information(self, "批量重命名", f"已成功重命名 {len(selected_rows)} 个模型")
        except Exception as e:
            # 回滚
            for new_file, old_file, row, _ in reversed(moved_files):
                if os.path.exists(new_file):
                    try:
                        shutil.move(new_file, old_file)
                    except Exception as e2:
                        self.log(f"回滚移动失败: {e2}")
                        try:
                            os.remove(new_file)
                            self.log(f"已删除回滚失败残留文件: {new_file}")
                        except Exception as e3:
                            self.log(f"删除残留文件失败: {e3}")
            self.log(f"批量重命名失败: {e}")
            QMessageBox.warning(self, "批量重命名失败", f"批量重命名时发生错误：\n{e}")
            return
        # 刷新预览
        if selected_rows:
            self.load_model_info(selected_rows[0], 0)

    def open_model_location(self, row):
        filename = self.table.item(row, 1).text()
        moved_path = self.table.item(row, 6).text()
        orig_path = self.table.item(row, 3).text()
        if moved_path:
            file_path = os.path.join(moved_path, filename)
        else:
            file_path = os.path.join(orig_path, filename)
        file_path = os.path.normpath(file_path)
        try:
            if os.path.exists(file_path):
                if platform.system() == "Windows":

                    subprocess.Popen(['explorer', '/select,', file_path])
                elif platform.system() == "Darwin":
                    subprocess.Popen(['open', '-R', file_path])
                else:
                    subprocess.Popen(['xdg-open', os.path.dirname(file_path)])
            else:
                folder = moved_path if moved_path else orig_path
                folder = os.path.normpath(folder)
                if platform.system() == "Windows":
                    subprocess.Popen(['explorer', folder])
                elif platform.system() == "Darwin":
                    subprocess.Popen(['open', folder])
                else:
                    subprocess.Popen(['xdg-open', folder])
        except Exception as e:
            self.log(f"无法打开文件: {e}")
            QMessageBox.warning(self, "错误", f"无法打开文件: {str(e)}")

    def filter_table(self, text):
        self.filter_text = text.lower()
        for row in range(self.table.rowCount()):
            filename = self.table.item(row, 1).text().lower()
            sha256_val = ""
            sha256_short = ""
            try:
                sha256_short = self.table.item(row, 8).text().lower() if self.table.columnCount() > 8 and self.table.item(row, 8) else ""
                sha256_val = self.table.item(row, 9).text().lower() if self.table.columnCount() > 9 and self.table.item(row, 9) else ""
            except Exception:
                sha256_short = ""
                sha256_val = ""
            if not sha256_val:
                orig_path = self.table.item(row, 3).text()
                model_name = self.table.item(row, 1).text()
                base = os.path.splitext(os.path.join(orig_path, model_name))[0]
                sha256_path = base + ".sha256"
                if os.path.exists(sha256_path):
                    try:
                        with open(sha256_path, "r") as fsha:
                            sha256_val = fsha.read().strip().lower()
                            sha256_short = sha256_val[:10]
                    except Exception:
                        sha256_val = ""
                        sha256_short = ""
            match = (self.filter_text in filename or (sha256_short and self.filter_text in sha256_short) or (sha256_val and self.filter_text in sha256_val))
            self.table.setRowHidden(row, not match)

    def undo_last_move(self, row):
        self.release_gif_resource()
        filename = self.table.item(row, 1).text()
        moved_path = self.table.item(row, 6).text()
        orig_path = self.table.item(row, 3).text()
        if not moved_path:
            QMessageBox.information(self, "提示", "该模型未移动，无需撤销")
            return
        try:
            base_name = os.path.splitext(filename)[0]
            for ext in ALL_MODEL_EXTS:
                src = os.path.join(moved_path, base_name + ext)
                dst = os.path.join(orig_path, base_name + ext)
                if not os.path.exists(src):
                    self.log(f"撤销移动失败: 源文件不存在: {src}")
                    continue
                # 检查目标目录是否存在
                dst_dir = os.path.dirname(dst)
                if not os.path.exists(dst_dir):
                    self.log(f"撤销移动失败: 目标文件夹不存在: {dst_dir}")
                    continue
                shutil.move(src, dst)
            self.table.setItem(row, 6, QTableWidgetItem(""))
            if 0 <= row < self.table.rowCount():
                self.load_model_info(row, 0)
            QMessageBox.information(self, "撤销完成", "已撤销该模型的上次移动")
            src_abs = os.path.normpath(os.path.abspath(moved_path))
            dst_abs = os.path.normpath(os.path.abspath(orig_path))
            self.log(f"撤销移动: {filename} 已从 {win_path(moved_path)} 撤回到 {win_path(orig_path)}")
        except Exception as e:
            self.log(f"撤销移动失败: {e}\n源: {src}\n目标: {dst}")
            QMessageBox.warning(self, "撤销错误", f"撤销操作失败: {str(e)}\n源: {src}\n目标: {dst}")
            self.load_model_info(row, 0)

    def undo_rename(self, row):  # 撤回重命名
        if row not in self.rename_history:
            QMessageBox.information(self, "提示", "没有可撤回的重命名记录")
            return
        old_base, new_base, file_ext, dir_path = self.rename_history[row]
        # 检查是否有重名
        for ext in ALL_MODEL_EXTS:
            check_file = os.path.join(dir_path, old_base + ext)
            if os.path.exists(check_file):
                QMessageBox.warning(self, "撤回失败", f"已存在同名文件：\n{check_file}\n无法撤回。")
                return
        moved_files = []
        try:
            for ext in ALL_MODEL_EXTS:
                old_file = os.path.join(dir_path, new_base + ext)
                new_file = os.path.join(dir_path, old_base + ext)
                if os.path.exists(old_file):
                    shutil.move(old_file, new_file)
                    moved_files.append((new_file, old_file))
            self.table.setItem(row, 1, QTableWidgetItem(old_base + file_ext))
            self.modified = True
            self.log(f"撤回重命名成功: {new_base + file_ext} → {old_base + file_ext}")
            QMessageBox.information(self, "撤回重命名", f"已撤回为：{old_base + file_ext}")
            # 撤回后删除记录
            del self.rename_history[row]
        except Exception as e:
            # 回滚
            for new_file, old_file in reversed(moved_files):
                if os.path.exists(new_file):
                    try:
                        shutil.move(new_file, old_file)
                    except Exception as e2:
                        self.log(f"回滚移动失败: {e2}")
            self.log(f"撤回重命名失败: {e}")
            QMessageBox.warning(self, "撤回重命名失败", f"撤回重命名时发生错误：\n{e}")

    def update_stats(self):
        total = self.table.rowCount()
        type_count = {}
        hash_count = 0
        for row in range(total):
            t = self.table.item(row, 4).text()
            type_count[t] = type_count.get(t, 0) + 1
            orig_path = self.table.item(row, 3).text()
            filename = self.table.item(row, 1).text()
            base = os.path.splitext(os.path.join(orig_path, filename))[0]
            sha256_path = base + ".sha256"
            if os.path.exists(sha256_path):
                hash_count += 1
        stat_str = f"总数: {total}  哈希值: {hash_count}  " + "  ".join([f"{k}:{v}" for k, v in type_count.items()])
        self.stats_label.setText("日志：" + stat_str)

    def check_duplicates(self):
        info = {}
        for row in range(self.table.rowCount()):
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            full_path = os.path.join(orig_path, filename)
            if not os.path.exists(full_path):
                continue
            size = os.path.getsize(full_path)
            sha256_path = os.path.splitext(full_path)[0] + ".sha256"
            if os.path.exists(sha256_path):
                try:
                    with open(sha256_path, "r") as fsha:
                        hashv = fsha.read().strip()
                except Exception:
                    hashv = self.calc_sha256(full_path)
            else:
                hashv = self.calc_sha256(full_path)
            key = (hashv, size)
            info.setdefault(key, []).append(full_path)
        duplicates = [files for files in info.values() if len(files) > 1]
        if not duplicates:
            QMessageBox.information(self, "查重", "未发现重复模型文件")
            return
        dlg = DuplicateDialog(duplicates, self)
        dlg.exec()

    def calc_sha256(self, filepath):
        h = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
    
    def rename_model(self, row, new_name=None):
        self.release_gif_resource()
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        dir_path = moved_path if moved_path else orig_path
        file_ext = os.path.splitext(filename)[1]
        base_old = os.path.splitext(filename)[0]
        if new_name is None:
            # 只让用户输入主文件名部分
            new_base, ok = QInputDialog.getText(
                self, "重命名", f"输入新文件名（不含扩展名，自动加{file_ext}）:", text=base_old
            )
            if not (ok and new_base and new_base != base_old):
                return
        else:
            new_base = new_name
        new_name_full = new_base + file_ext
        # 检查同目录下是否有同名文件
        for ext in ALL_MODEL_EXTS:
            check_file = os.path.join(dir_path, new_base + ext)
            if os.path.exists(check_file):
                QMessageBox.warning(self, "重命名冲突", f"已存在同名文件：\n{check_file}\n请换个名字。")
                return
        moved_files = []
        try:
            for ext in ALL_MODEL_EXTS:
                old_file = os.path.join(dir_path, base_old + ext)
                new_file = os.path.join(dir_path, new_base + ext)
                if os.path.exists(old_file):
                    shutil.move(old_file, new_file)
                    moved_files.append((new_file, old_file))  # 记录新->旧，便于回滚
            self.table.setItem(row, 1, QTableWidgetItem(new_name_full))
            self.modified = True
            self.log(f"重命名成功: {base_old + file_ext} → {new_name_full}")
            self.rename_history[row] = (base_old, new_base, file_ext, dir_path)
        except Exception as e:
            # 回滚已移动的文件
            for new_file, old_file in reversed(moved_files):
                if os.path.exists(new_file):
                    try:
                        shutil.move(new_file, old_file)
                    except Exception as e2:
                        self.log(f"回滚移动失败: {e2}")
                        try:
                            os.remove(new_file)
                            self.log(f"已删除回滚失败残留文件: {new_file}")
                        except Exception as e3:
                            self.log(f"删除残留文件失败: {e3}")
            self.log(f"重命名失败: {e}")
            QMessageBox.warning(self, "重命名失败", f"重命名文件时发生错误：\n{e}")
            return
        self.load_model_info(row, 0)

    def move_selected_model(self, row, target_dir=None):
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        full_path = os.path.join(orig_path, filename)
        base_path = os.path.splitext(full_path)[0]
        gif_file = base_path + DYNAMIC_PREVIEW_IMAGE_EXTS[0]
        # 检查GIF是否被占用
        if os.path.exists(gif_file) and self.is_file_locked(gif_file):
            QMessageBox.warning(self, "移动失败", f"GIF预览区正在被占用，无法移动：\n{gif_file}\n请关闭所有预览窗口后重试。")
            self.log(f"移动中断，GIF被占用：{gif_file}")
            return
        # 只有真正要移动时才释放GIF资源
        self.release_gif_resource()
        # 检查目标目录同名
        if target_dir is None:
            target_dir = QFileDialog.getExistingDirectory(self, "选择目标目录", self.model_dir)
            if not target_dir:
                self.log("用户取消了目标目录选择，移动中断")
                return

    def move_selected_model(self, row, target_dir=None, show_message=True):
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        full_path = os.path.join(orig_path, filename)
        base_path = os.path.splitext(full_path)[0]
        gif_file = base_path + DYNAMIC_PREVIEW_IMAGE_EXTS[0]
        if os.path.exists(gif_file) and self.is_file_locked(gif_file):
            if show_message:
                QMessageBox.warning(self, "移动失败", f"GIF预览区正在被占用，无法移动：\n{gif_file}\n请关闭所有预览窗口后重试。")
            self.log(f"移动中断，GIF被占用：{gif_file}")
            return
        self.release_gif_resource()
        if target_dir is None:
            target_dir = QFileDialog.getExistingDirectory(self, "选择目标目录", self.model_dir)
            if not target_dir:
                self.log("用户取消了目标目录选择，移动中断")
                return
        for ext in ALL_MODEL_EXTS:
            dst_file = os.path.join(target_dir, os.path.basename(base_path + ext))
            if os.path.exists(dst_file):
                if show_message:
                    QMessageBox.warning(self, "移动冲突", f"目标目录已存在同名文件：\n{dst_file}\n请先手动处理后再移动。")
                self.log(f"移动中断，目标目录已存在同名文件：{dst_file}")
                return
        error = None
        moved_files = []
        try:
            for ext in ALL_MODEL_EXTS:
                src_file = base_path + ext
                dst_file = os.path.join(target_dir, os.path.basename(src_file))
                if os.path.exists(src_file):
                    shutil.move(src_file, dst_file)
                    moved_files.append((dst_file, src_file))
            self.table.setItem(row, 6, QTableWidgetItem(win_path(target_dir)))
            self.log(f"模型 {filename} 及关联文件已移动到: {win_path(target_dir)}")
            if show_message:
                QMessageBox.information(self, "移动成功", f"模型及关联文件已移动到: {win_path(target_dir)}")
            # 关键：移动后立即刷新该行图片
            self.refresh_row_image(row)
            # 如果当前选中行就是本行，右侧预览也刷新
            if row == self.table.currentRow():
                self.load_model_info(row, 0)
        except Exception as e:
            for new_file, old_file in reversed(moved_files):
                if os.path.exists(new_file):
                    try:
                        shutil.move(new_file, old_file)
                    except Exception as e2:
                        self.log(f"回滚移动失败: {e2}")
                        try:
                            if os.path.basename(new_file) == os.path.basename(old_file):
                                os.remove(new_file)
                                self.log(f"已删除回滚失败残留文件: {new_file}")
                        except Exception as e3:
                            self.log(f"删除残留文件失败: {e3}")
            error = str(e)
            self.log(f"移动文件失败: {error}")
            if show_message:
                QMessageBox.warning(self, "移动错误", f"移动文件失败，已回滚：{error}")

    def generate_sha256(self, row):
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        full_path = os.path.join(orig_path, filename)
        progress = QProgressDialog("正在生成SHA256...", None, 0, 0, self)
        progress.setWindowTitle("进度")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()
        def on_finished(hashv, filename):
            self.single_sha256_worker.deleteLater()
            sha_path = os.path.splitext(full_path)[0] + ".sha256"
            if hashv:
                with open(sha_path, 'w') as f:
                    f.write(hashv)
                self.table.setItem(row, 7, QTableWidgetItem(hashv[:10]))
                self.table.setItem(row, 8, QTableWidgetItem(hashv))
                if row == self.table.currentRow():
                    self.load_model_info(row, 0)
                now = datetime.now().strftime("%H:%M:%S")
                self.log_output.append(f"[{now}] 单独生成哈希值：{filename} 已生成哈希值。")
                self.log_output.moveCursor(QTextCursor.End)
            progress.close()
        self.single_sha256_worker = SingleSha256Worker(full_path, filename)
        self.single_sha256_worker.finished.connect(on_finished)
        self.single_sha256_worker.start()

    def generate_sha256_batch(self):
        if not self.model_dir:
            QMessageBox.warning(self, "提示", "请先选择模型目录")
            return
        total = self.table.rowCount()
        file_list = []
        for row in range(total):
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            full_path = os.path.join(orig_path, filename)
            if os.path.exists(full_path):
                file_list.append((row, full_path))
        if not file_list:
            QMessageBox.information(self, "提示", "没有可处理的模型文件")
            return
        progress = QProgressDialog("正在批量生成SHA256...", "取消", 0, len(file_list), self)
        progress.setWindowTitle("进度")
        progress.setWindowModality(Qt.ApplicationModal)
        progress.setValue(0)
        self.sha256_worker = Sha256BatchWorker(file_list)
        self.sha256_worker.progress_changed.connect( lambda idx, total, short_hash, full_hash, filename: self._on_sha256_progress(idx, total, short_hash, full_hash, filename, file_list, progress))
        self.sha256_worker.finished.connect( lambda new_count, skip_count: self._on_sha256_finished(progress, new_count, skip_count))
        progress.canceled.connect(self.sha256_worker.cancel)
        self.sha256_worker.start()
        progress.exec()

    def _on_sha256_progress(self, idx, total, short_hash, full_hash, filename, file_list, progress):
        row, _ = file_list[idx-1]
        self.table.setItem(row, 7, QTableWidgetItem(short_hash))
        self.table.setItem(row, 8, QTableWidgetItem(full_hash))
        progress.setValue(idx)
        progress.setLabelText(f"正在生成 {filename} 的SHA256... ({idx}/{total})")

    def _on_sha256_finished(self, progress, new_count, skip_count):
        progress.close()
        if new_count == 0 and skip_count > 0:
            QMessageBox.information( self, "SHA256", f"所有模型的SHA256文件均已存在，无需再生成。")
            self.log("所有模型的SHA256文件均已存在，无需再生成。")
        else:
            QMessageBox.information(self, "SHA256", f"已新生成 {new_count} 个SHA256文件，已跳过 {skip_count} 个已存在的SHA256文件")
            self.log(f"批量生成SHA256：新生成 {new_count}，跳过 {skip_count}")

    def show_static_preview(self):
        base = self.static_image_label.model_base_path
        for ext in STATIC_PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    max_width, max_height = 240, 240
                    pixmap = pixmap.scaled(max_width, max_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.static_image_label.setPixmap(pixmap)
                    self.static_image_label.setText("")
                return

    def show_dynamic_preview(self):
        base = self.dynamic_image_label.model_base_path
        gif_path = base + DYNAMIC_PREVIEW_IMAGE_EXTS[0]
        if os.path.exists(gif_path):
            try:
                if not hasattr(self, "_gif_player") or self._gif_player is None:
                    gif_player = GifPlayer(gif_path, self.dynamic_image_label)
                    if gif_player.movie:
                        self._gif_player = gif_player
                    else:
                        self._gif_player = None
                        raise Exception("GIF加载失败")
                else:
                    if self._gif_player:
                        self._gif_player.set_gif(gif_path)
                    else:
                        raise Exception("GIF加载失败")
                if self._gif_player and self._gif_player.movie:
                    self.dynamic_image_label.setMovie(self._gif_player.movie)
                    self.dynamic_image_label.setText("")
                    # 缩放逻辑
                    def scale_movie():
                        size = self._gif_player.movie.currentImage().size()
                        if size.width() > 0 and size.height() > 0:
                            max_height = 240
                            max_width = 240
                            w, h = size.width(), size.height()
                            if h > max_height:
                                w = int(w * max_height / h)
                                h = max_height
                            if w > max_width:
                                h = int(h * max_width / w)
                                w = max_width
                            self._gif_player.movie.setScaledSize(QSize(w, h))
                            self._gif_player.movie.frameChanged.disconnect(scale_movie)
                    self._gif_player.movie.frameChanged.connect(scale_movie)
                    self._gif_player.movie.start()
                    self._gif_player.movie.jumpToFrame(0)
                else:
                    raise Exception("GIF加载失败")
            except Exception as e:
                self.dynamic_image_label.setText("GIF加载失败")
                self.log(f"动态预览图像加载失败：{gif_path} {e}")
        else:
            self.static_image_label.setText("无静态预览图\n拖放图片到此处")
            self.dynamic_image_label.setText("无动态预览图\n拖放图片到此处")

    def refresh_preview_and_table(self):
        self.release_gif_resource()
        row = self.table.currentRow()
        if row >= 0:
            self.load_model_info(row, 0)
            # 刷新表格图片缩略图
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            moved_path = self.table.item(row, 6).text()
            use_path = moved_path if moved_path else orig_path
            base_path = os.path.splitext(os.path.join(use_path, filename))[0]
            preview_path, _ = self.find_preview_image(base_path)
            image_item = QTableWidgetItem()
            if preview_path:
                pixmap = QPixmap(preview_path)
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    image_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
            # 先清空再设置，确保刷新
            self.table.setItem(row, 0, QTableWidgetItem())  # 清空
            self.table.setItem(row, 0, image_item)
        else:
            self.static_image_label.setText("无静态预览图")
            self.dynamic_image_label.setText("无动态预览图")
            self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
        # row = self.table.currentRow()
        if row >= 0:
            # self.load_model_info(row, 0)
# 刷新表格图片缩略图
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            base_path = os.path.splitext(os.path.join(orig_path, filename))[0]
            preview_path, _ = self.find_preview_image(base_path)
            image_item = QTableWidgetItem()
            if preview_path:
                pixmap = QPixmap(preview_path)
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    image_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
            self.table.setItem(row, 0, image_item)

    def refresh_preview_buttons(self):
        row = self.table.currentRow()
        if row < 0:
            return
        filename = self.table.item(row, 1).text()
        orig_path = self.table.item(row, 3).text()
        moved_path = self.table.item(row, 6).text()
        use_path = moved_path or orig_path
        base = os.path.splitext(os.path.join(use_path, filename))[0]
        has_static = any(os.path.exists(base + ext) for ext in STATIC_PREVIEW_IMAGE_EXTS)
        has_gif = any(os.path.exists(base + ext) for ext in DYNAMIC_PREVIEW_IMAGE_EXTS)

    def log(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_output.append(f"[{now}] {msg}")
        self.log_output.moveCursor(QTextCursor.End)

    @staticmethod
    def is_file_locked(filepath):
        """跨平台检测文件是否被占用（Windows下最可靠）"""
        import sys
        import os
        if not os.path.exists(filepath):
            return False
        if sys.platform == "win32":
            import win32con
            import win32file
            try:
                handle = win32file.CreateFile(
                    filepath,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0,  # 不允许共享
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None
                )
                win32file.CloseHandle(handle)
                return False
            except Exception:
                return True
        else:
            try:
                with open(filepath, "rb+"):
                    pass
                return False
            except Exception:
                return True

    def check_duplicates_with_sha256_check(self):
        model_count = 0
        sha256_count = 0
        model_files = []
        for row in range(self.table.rowCount()):
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            full_path = os.path.join(orig_path, filename)
            if not os.path.exists(full_path):
                continue
            model_count += 1
            model_files.append(full_path)
            sha256_path = os.path.splitext(full_path)[0] + ".sha256"
            if os.path.exists(sha256_path):
                sha256_count += 1
        if model_count != sha256_count:
            ret = QMessageBox.question(
                self, "SHA256数量不一致",
                f"模型文件数：{model_count}，SHA256文件数：{sha256_count}\n是否补全缺失的SHA256？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if ret == QMessageBox.StandardButton.Yes:
                missing_files = [f for f in model_files if not os.path.exists(os.path.splitext(f)[0] + ".sha256")]
                if not missing_files:
                    self.log("没有缺失的SHA256文件")
                    self.check_duplicates()
                    return
                progress = QProgressDialog("正在补全缺失的SHA256...", "取消", 0, len(missing_files), self)
                progress.setWindowTitle("进度")
                progress.setWindowModality(Qt.ApplicationModal)
                progress.setValue(0)

                class Sha256FillWorker(QThread):
                    progress_signal = Signal(int, int, str)
                    finished_signal = Signal()

                    def __init__(self, files, parent=None):
                        super().__init__(parent)
                        self.files = files
                        self._is_cancelled = False
                        self._removed_once = False

                    def run(self):
                        for idx, full_path in enumerate(self.files, 1):
                            if self._is_cancelled:
                                break
                            sha256_path = os.path.splitext(full_path)[0] + ".sha256"
                            hashv = self.parent().calc_sha256(full_path)
                            with open(sha256_path, 'w') as f:
                                f.write(hashv)
                            self.progress_signal.emit(idx, len(self.files), os.path.basename(full_path))
                        self.finished_signal.emit()

                    def cancel(self):
                        self._is_cancelled = True

                self.sha256_fill_worker = Sha256FillWorker(missing_files, self)
                self.sha256_fill_worker.progress_signal.connect(
                    lambda idx, total, filename: (
                        progress.setValue(idx),
                        progress.setLabelText(f"正在生成: {filename} ({idx}/{total})"),
                        QApplication.processEvents()
                    )
                )
                self.sha256_fill_worker.finished_signal.connect(
                    lambda: (
                        progress.close(),
                        self.log("已补全缺失的SHA256文件"),
                        self.check_duplicates()
                    )
                )
                progress.canceled.connect(self.sha256_fill_worker.cancel)
                self.sha256_fill_worker.start()
                progress.exec()
                return
            elif ret == QMessageBox.StandardButton.Cancel:
                return
        self.check_duplicates()

    def auto_save_json(self):
        description = self.description_input.toPlainText().strip()
        notes = self.notes_input.toPlainText().strip()
        vae = self.vae_input.toPlainText().strip()
        json_path = getattr(self, "current_json_path", None)
        if not json_path:
            row = self.table.currentRow()
            if row < 0:
                self.log("auto_save_json: 无有效行")
                return
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            base = os.path.splitext(os.path.join(orig_path, filename))[0]
            json_path = base + ".json"
        json_path = os.path.normpath(json_path)
        data = {
            "description": description,
            "notes": notes,
            "vae": vae
        }
        empty_struct = {
            "description": "",
            "notes": "",
            "vae": ""
        }
        if data == empty_struct:
            json_path = os.path.normpath(json_path)
            if os.path.exists(json_path):
                try:
                    os.remove(json_path)
                    self.log(f"已删除备注JSON: {os.path.basename(json_path)}")
                except Exception as e:
                    self.log(f"删除JSON失败: {e}")
            else:
                self.log(f"文件不存在，无需删除: {json_path}")
            return
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f"自动保存备注JSON: {os.path.basename(json_path)}")
        except Exception as e:
            self.log(f"保存JSON失败: {e}")

    def delete_empty_json_files(self):
        if not self.model_dir:
            QMessageBox.warning(self, "提示", "请先选择模型目录")
            return
        deleted_files = 0
        for root, _, files in os.walk(self.model_dir):
            for f in files:
                if f.endswith(".json"):
                    full_path = os.path.join(root, f)
                    try:
                        with open(full_path, 'r', encoding='utf-8') as jf:
                            content = json.load(jf)
                        if not (content.get("description") or content.get("notes") or content.get("vae")):
                            os.remove(full_path)
                            deleted_files += 1
                            self.log(f"删除空白JSON文件: {full_path}")
                    except Exception as e:
                        self.log(f"检查JSON文件时出错 {full_path}: {e}")
        if deleted_files == 0:
            QMessageBox.information(self, "完成", "没有可删除的空白JSON文件")
            self.log("没有可删除的空白JSON文件")
        else:
            QMessageBox.information(self, "完成", f"已删除 {deleted_files} 个空白JSON文件")
            self.log(f"已删除 {deleted_files} 个空白JSON文件")

    def remove_deleted_models(self, deleted_files):
# 操作前释放GIF资源
        self.release_gif_resource()
        deleted_set = set(os.path.normpath(f) for f in deleted_files)
        rows_to_remove = []
        for row in range(self.table.rowCount()):
            filename = self.table.item(row, 1).text()
            orig_path = self.table.item(row, 3).text()
            full_path = os.path.normpath(os.path.join(orig_path, filename))
            if full_path in deleted_set:
                rows_to_remove.append(row)
        for row in reversed(rows_to_remove):
            self.table.removeRow(row)
        self.update_stats()
        self.log(f"已从列表移除 {len(rows_to_remove)} 个被删除的模型文件")

class DuplicateDialog(QDialog):
    def __init__(self, duplicates, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重复模型文件")
        self.resize(700, 700)
        main_layout = QVBoxLayout(self)
        top_row = QHBoxLayout()
        self._gif_player = None
        self.static_image_label = ImageLabel(self, preview_type="static")
        self.static_image_label.setFixedSize(240, 240)
        self.static_image_label.setStyleSheet(IMAGE_LABEL_STYLE)
        self.dynamic_image_label = ImageLabel(self, preview_type="dynamic")
        self.dynamic_image_label.setFixedSize(240, 240)
        self.dynamic_image_label.setStyleSheet(IMAGE_LABEL_STYLE)
        self.static_info_label = QLabel("")
        self.static_info_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.dynamic_info_label = QLabel("")
        self.dynamic_info_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        
        # 静态预览区
        static_vbox = QVBoxLayout()
        static_vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        static_title = QLabel("静态预览区")
        static_title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        static_vbox.addWidget(static_title)
        static_vbox.addWidget(self.static_image_label)
        static_vbox.addWidget(self.static_info_label)
        
        # 动态预览区
        dynamic_vbox = QVBoxLayout()
        dynamic_vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        dynamic_title = QLabel("动态预览区")
        dynamic_title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        dynamic_vbox.addWidget(dynamic_title)
        dynamic_vbox.addWidget(self.dynamic_image_label)
        dynamic_vbox.addWidget(self.dynamic_info_label)
        
        top_row = QHBoxLayout()
        top_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        top_row.addLayout(static_vbox, 0)
        top_row.addLayout(dynamic_vbox, 0)
        top_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        top_row.addSpacing(10)
        info_vbox = QVBoxLayout()
        self.preview_info_label = QLabel("")
        self.preview_info_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.preview_info_label.setMinimumWidth(120)  # 可选，设置最小宽度
        self.preview_info_label.setMinimumHeight(10)
        info_vbox.addWidget(self.preview_info_label)
        info_vbox.addStretch()
        info_widget = QWidget()
        info_widget.setLayout(info_vbox)
        info_widget.setMinimumWidth(240)  # 可选，设置最小宽度        
        top_row.addWidget(info_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        top_row.addSpacing(10)
        notes_vbox = QVBoxLayout()
        self.desc_label = QLabel("Description:")
        self.desc_edit = QTextEdit()
        self.desc_edit.setFixedHeight(80)
        self.notes_label = QLabel("Notes:")
        self.notes_edit = QTextEdit()
        self.notes_edit.setFixedHeight(80)
        self.vae_label = QLabel("VAE:")
        self.vae_edit = QTextEdit()
        self.vae_edit.setFixedHeight(80) 
        notes_vbox.addWidget(self.desc_label)
        notes_vbox.addWidget(self.desc_edit)
        notes_vbox.addWidget(self.notes_label)
        notes_vbox.addWidget(self.notes_edit)
        notes_vbox.addWidget(self.vae_label)
        notes_vbox.addWidget(self.vae_edit)
        notes_vbox.addStretch()
        notes_widget = QWidget()
        notes_widget.setLayout(notes_vbox)
        notes_widget.setFixedWidth(520)  
        notes_widget.setMinimumHeight(220)# 备注区最小高度
        top_row.addWidget(notes_widget, 0, Qt.AlignmentFlag.AlignTop)
        main_layout.addLayout(top_row)
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["图片", "文件名", "大小", "路径", "SHA256(前十位)", "SHA256"])
        self.table.setColumnWidth(0, 64)  
        self.table.setColumnWidth(1, 300) 
        self.table.setColumnWidth(2, 80)  
        self.table.setColumnWidth(3, 300) 
        self.table.setColumnWidth(4, 100) 
        self.table.setColumnWidth(5, 250) 
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)
        main_layout.addWidget(self.table, 1)
        self.duplicates = duplicates
        self.parent_gui = parent
        self.fill_table()
        self.modified = False
        self.deleted_files = []
        self._removed_once = False
        self.table.cellClicked.connect(self.update_preview)
        self.desc_edit.textChanged.connect(self.auto_save_json)
        self.notes_edit.textChanged.connect(self.auto_save_json)
        self.vae_edit.textChanged.connect(self.auto_save_json)
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFixedHeight(100)
        self.static_image_label.setText("无静态预览图")
        self.dynamic_image_label.setText("无动态预览图")
        self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
        self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
        main_layout.addWidget(self.log_output)
        # self.log("DuplicateDialog 已创建")

    def log(self, msg):
        now = datetime.now().strftime("%H:%M:%S")
        # 输出到自身日志控件
        self.log_output.append(f"[{now}] {msg}")
        self.log_output.moveCursor(QTextCursor.End)
        # 同步输出到主界面日志区
        if self.parent_gui and hasattr(self.parent_gui, "log"):
            self.parent_gui.log(f"[重复窗口] {msg}")

    def _on_image_label_double_click(self, event):
        # 获取当前预览图片路径
        row = self.table.currentRow()
        if row < 0:
            return
        filename = self.table.item(row, 1).text()
        dir_path = self.table.item(row, 3).text()
        base = os.path.splitext(os.path.join(dir_path, filename))[0]
        preview_path = None
        for ext in PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                preview_path = path
                break
        if not preview_path:
            return
        # 打开图片
        try:
            if platform.system() == "Windows":
                os.startfile(preview_path)
            elif platform.system() == "Darwin":
                subprocess.Popen(['open', preview_path])
            else:
                subprocess.Popen(['xdg-open', preview_path])
        except Exception as e:
            self.log(f"打开图片失败: {preview_path}, 错误: {e}")
            QMessageBox.warning(self, "打开失败", f"无法用系统看图工具打开图片：\n{preview_path}\n{e}")

    def _on_table_cell_double_clicked(self, row, col):
        if col == 1:
            # 直接执行重命名逻辑
            filename = self.table.item(row, 1).text()
            dir_path = self.table.item(row, 3).text()
            file_ext = os.path.splitext(filename)[1]
            base_old = os.path.splitext(filename)[0]
            new_base, ok = QInputDialog.getText(self, "重命名", f"输入新文件名（不含扩展名，自动加{file_ext}）:", text=base_old)
            if ok and new_base and new_base != base_old:
                new_name = new_base + file_ext
                error = None
                try:
                    for ext in ALL_MODEL_EXTS:
                        old_file = os.path.join(dir_path, base_old + ext)
                        new_file = os.path.join(dir_path, new_base + ext)
                        if os.path.exists(old_file):
                            shutil.move(old_file, new_file)
                    self.table.setItem(row, 1, QTableWidgetItem(new_name))
                    self.modified = True
                    self.log(f"重命名成功: {base_old + file_ext} → {new_name}")
                    # 只刷新主界面对应行
                    old_full_path = os.path.join(dir_path, base_old + file_ext)
                    if self.parent_gui and hasattr(self.parent_gui, "update_row_by_path"):
                        self.parent_gui.update_row_by_path(old_full_path, new_name)
                except Exception as e:
                    self.log(f"重命名失败: {e}")
                    error = str(e)
                if 0 <= row < self.table.rowCount():
                    self.release_gif_resource()
                    self.update_preview(row, 0)
                if error:
                    QMessageBox.warning(self, "重命名失败", f"重命名文件时发生错误：\n{error}")
                base_path = os.path.join(dir_path, new_base + file_ext)
                self.release_gif_resource()
                self.update_preview(row, 0)

        # 在释放资源后强制刷新
    def release_gif_resource(self):
        try:
            if hasattr(self, "_gif_player") and self._gif_player:
                if self._gif_player.movie:
                    self.dynamic_image_label.setMovie(None)
                    self._gif_player.movie.stop()
                    self._gif_player.movie.deleteLater()
                    self._gif_player.movie = None
                if self._gif_player.buffer:
                    self._gif_player.buffer.close()
                    self._gif_player.buffer.deleteLater()
                    self._gif_player.buffer = None
                self._gif_player.byte_array = None
                del self._gif_player
                self._gif_player = None
            if hasattr(self, "_movie") and self._movie:
                self.dynamic_image_label.setMovie(None)
                self._movie.stop()
                self._movie.deleteLater()
                self._movie = None
            self.dynamic_image_label.setMovie(None)
            transparent = QPixmap(240, 240)
            transparent.fill(Qt.transparent)
            self.dynamic_image_label.setPixmap(transparent)
            self.dynamic_image_label.clear()
            self.dynamic_image_label.setText("无动态预览图")
            # ----------- 1. 强制刷新 label -----------
            self.dynamic_image_label.repaint()
            self.dynamic_image_label.update()
            QApplication.processEvents()
            # ----------- 2. 强制刷新父窗口 -----------
            parent = self.dynamic_image_label.parentWidget()
            if parent:
                parent.update()
                parent.repaint()
            window = self.dynamic_image_label.window()
            if window:
                window.update()
                window.repaint()
            QApplication.processEvents()
            # ----------------------------------------
        except Exception as e:
            self.log(f"释放GIF资源异常: {e}")
        gc.collect()

    def fill_table(self):
        self.release_gif_resource()
        self.table.setRowCount(0)
        group_idx = 1
        row_idx = 0
        for files in self.duplicates:
            # 插入分割行
            self.table.insertRow(row_idx)
            group_item = QTableWidgetItem(f"—— 重复组 {group_idx} ——")
            group_item.setFlags(Qt.ItemIsEnabled)  # 不可选中
            group_item.setBackground(QColor("#e0e0e0"))
            group_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row_idx, 0, group_item)
            self.table.setSpan(row_idx, 0, 1, self.table.columnCount())  # 合并所有列
            row_idx += 1
            # 插入本组所有文件
            for file_path in files:
                if not os.path.exists(file_path):
                    self.log(f"文件不存在，跳过: {file_path}")
                    continue
                filename = os.path.basename(file_path)
                try:
                    size = os.path.getsize(file_path)
                    size_str = self.parent_gui.format_file_size(size) if self.parent_gui else f"{size} B"
                except Exception as e:
                    self.log(f"获取文件大小失败: {file_path}, 错误: {e}")
                    size_str = "N/A"
                base = os.path.splitext(file_path)[0]
                sha256_path = base + ".sha256"
                sha256_val = ""
                if os.path.exists(sha256_path):
                    try:
                        with open(sha256_path, "r") as fsha:
                            sha256_val = fsha.read().strip()
                    except Exception:
                        sha256_val = ""
                else:
                    sha256_val = self.parent_gui.calc_sha256(file_path) if self.parent_gui else ""
                self.table.insertRow(row_idx)
                preview_path, _ = self.parent_gui.find_preview_image(base) if self.parent_gui else (None, None)
                image_item = QTableWidgetItem()
                if preview_path:
                    pixmap = QPixmap(preview_path)
                    if not pixmap.isNull():
                        pixmap = pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                        image_item.setData(Qt.ItemDataRole.DecorationRole, pixmap)
                self.table.setItem(row_idx, 0, image_item)
                self.table.setItem(row_idx, 1, QTableWidgetItem(filename))
                self.table.setItem(row_idx, 2, QTableWidgetItem(size_str))
                self.table.setItem(row_idx, 3, QTableWidgetItem(os.path.dirname(file_path)))
                self.table.setItem(row_idx, 4, QTableWidgetItem(sha256_val[:10]))
                self.table.setItem(row_idx, 5, QTableWidgetItem(sha256_val))
                row_idx += 1
            group_idx += 1

    def show_context_menu(self, pos):
        menu = QMenu(self)
        delete_action = menu.addAction("删除该文件")
        rename_action = menu.addAction("重命名")
        open_action = menu.addAction("打开模型文件")        
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if not action:
            return
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = index.row()
        filename = self.table.item(row, 1).text()
        dir_path = self.table.item(row, 3).text()
        full_path = os.path.join(dir_path, filename)        
        if action == delete_action:
            # 操作前释放GIF资源
            self.release_gif_resource()
            reply = QMessageBox.question(self, "确认删除", f"确定要删除该模型及所有关联文件？\n{full_path}", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                import tempfile
                import uuid
                base_path = os.path.splitext(full_path)[0]
                error = None
                # 1. 创建临时回收站目录
                trash_dir = os.path.join(tempfile.gettempdir(), "sd_model_trash", str(uuid.uuid4()))
                os.makedirs(trash_dir, exist_ok=True)
                moved_files = []
                try:
                    # 2. 先全部移动到临时目录
                    for ext in ALL_MODEL_EXTS:
                        file_to_delete = base_path + ext
                        if os.path.exists(file_to_delete):
                            dst = os.path.join(trash_dir, os.path.basename(file_to_delete))
                            shutil.move(file_to_delete, dst)
                            moved_files.append((dst, file_to_delete))
                    # 3. 全部移动成功后，再彻底删除
                    for dst, _ in moved_files:
                        try:
                            os.remove(dst)
                        except Exception as e:
                            self.log(f"彻底删除失败: {dst}, 错误: {e}")
                    # 4. 删除表格行
                    self.table.removeRow(row)
                    self.static_image_label.setText("已删除")
                    self.dynamic_image_label.setText("已删除")
                    self.modified = True
                    self.log(f"已删除模型文件: {full_path}")
                    self.deleted_files.append(full_path)
                    # 刷新duplicates和表格
                    for group in self.duplicates[:]:
                        if full_path in group:
                            group.remove(full_path)
                            if len(group) <= 1:
                                self.duplicates.remove(group)
                            break
                    self.fill_table()
                    if not self.duplicates:
                        QMessageBox.information(self, "无重复项", "所有重复项已处理完毕，窗口将自动关闭。")
                        self.close()
                        return
                    if self.table.rowCount() == 1:
                        item = self.table.item(0, 1)
                        if item:
                            font = item.font()
                            font.setStrikeOut(True)
                            item.setFont(font)
                            item.setForeground(QColor("gray"))
                        QMessageBox.information(self, "无重复项", "只剩下一个模型，窗口将自动关闭。")
                        self.close()
                        return
                    # 5. 删除临时目录
                    try:
                        os.rmdir(trash_dir)
                    except Exception:
                        pass
                except Exception as e:
                    # 回滚：把已移动的文件移回原位置
                    for dst, orig in reversed(moved_files):
                        if os.path.exists(dst):
                            try:
                                shutil.move(dst, orig)
                            except Exception as e2:
                                self.log(f"回滚删除失败: {e2}")
                    self.log(f"删除失败: {e}")
                    error = str(e)
                # 只在行还存在时刷新
                if row < self.table.rowCount():
                    self.release_gif_resource()
                    self.update_preview(row, 0)
                if error:
                    QMessageBox.warning(self, "删除失败", f"无法删除文件，已回滚：\n{error}")
                base_path = os.path.splitext(full_path)[0]
                self.release_gif_resource()
                self.update_preview(row, 0)
        elif action == rename_action:
# 操作前释放GIF资源
            self.release_gif_resource()
            file_ext = os.path.splitext(filename)[1]
            base_old = os.path.splitext(filename)[0]
            new_base, ok = QInputDialog.getText(self, "重命名", f"输入新文件名（不含扩展名，自动加{file_ext}）:", text=base_old)
            if ok and new_base and new_base != base_old:
                new_name = new_base + file_ext
                error = None
                try:
                    for ext in ALL_MODEL_EXTS:
                        old_file = os.path.join(dir_path, base_old + ext)
                        new_file = os.path.join(dir_path, new_base + ext)
                        if os.path.exists(old_file):
                            shutil.move(old_file, new_file)
                    self.table.setItem(row, 1, QTableWidgetItem(new_name))
                    self.modified = True
                    self.log(f"重命名成功: {base_old + file_ext} → {new_name}")
                    # 只刷新主界面对应行
                    old_full_path = os.path.join(dir_path, base_old + file_ext)
                    if self.parent_gui and hasattr(self.parent_gui, "update_row_by_path"):
                        self.parent_gui.update_row_by_path(old_full_path, new_name)
                except Exception as e:
                    self.log(f"重命名失败: {e}")
                    error = str(e)
                if 0 <= row < self.table.rowCount():
                    self.release_gif_resource()
                    self.update_preview(row, 0)
                if error:
                    QMessageBox.warning(self, "重命名失败", f"重命名文件时发生错误：\n{error}")
                base_path = os.path.join(dir_path, new_base + file_ext)
                self.release_gif_resource()
                self.update_preview(row, 0)
        elif action == open_action:
            abs_path = os.path.abspath(full_path)
            try:
                if os.path.exists(abs_path):
                    if platform.system() == "Windows":
                        subprocess.Popen(['explorer', '/select,', abs_path])
                    elif platform.system() == "Darwin":
                        subprocess.Popen(['open', '-R', abs_path])
                    else:
                        subprocess.Popen(['xdg-open', os.path.dirname(abs_path)])
                else:
                    QMessageBox.warning(self, "错误", "文件不存在")
            except Exception as e:
                self.log(f"打开文件失败: {e}")
                QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

    def _remove_deleted_once(self):
        self.release_gif_resource()
        if self._removed_once:
            return
        if self.modified and self.parent_gui and self.parent_gui.isVisible() and not QApplication.instance().closingDown():
            if self.deleted_files:
                self.parent_gui.remove_deleted_models(self.deleted_files)
        self._removed_once = True
    
    def closeEvent(self, event):
        self._remove_deleted_once()
        super().closeEvent(event)

    def get_static_info(self):
        path = self.static_image_label.current_preview_path()
        if path and os.path.exists(path):
            from PySide6.QtGui import QImageReader
            reader = QImageReader(path)
            size = reader.size()
            width, height = size.width(), size.height()
            file_size = os.path.getsize(path)
            ext = os.path.splitext(path)[1]
            info = (
                f"【静态预览】\n"
                f"尺寸：{width}x{height}\n"
                f"大小：{self.parent_gui.format_file_size(file_size) if self.parent_gui else f'{file_size}B'}\n"
                f"后缀名：{ext.lstrip('.')}\n"
            )
        else:
            info = "【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n"
        return info

    def update_preview(self, row, col):
        self.release_gif_resource()
        if not self.table.item(row, 1) or not self.table.item(row, 1).text():
            self.static_image_label.setText("无静态预览图")
            self.dynamic_image_label.setText("无动态预览图")
            self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            self.dynamic_info_label.setText("【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
            return
        filename = self.table.item(row, 1).text()
        dir_path = self.table.item(row, 3).text()
        base = os.path.splitext(os.path.join(dir_path, filename))[0]
        self.static_image_label.model_base_path = base
        self.dynamic_image_label.model_base_path = base
    
        # ----------- 静态预览多图切换 -----------
        static_preview_paths = []
        for ext in STATIC_PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                static_preview_paths.append(path)
        if static_preview_paths:
            self.static_image_label.set_preview_images(static_preview_paths, 0)
            # 复用主界面刷新方法
            if self.parent_gui and hasattr(self.parent_gui, "refresh_static_info_label"):
                # 临时切换主界面的 label 指向本窗口的 label
                old_static_image_label = self.parent_gui.static_image_label
                old_static_info_label = self.parent_gui.static_info_label
                self.parent_gui.static_image_label = self.static_image_label
                self.parent_gui.static_info_label = self.static_info_label
                self.parent_gui.refresh_static_info_label()
                # 恢复
                self.parent_gui.static_image_label = old_static_image_label
                self.parent_gui.static_info_label = old_static_info_label
        else:
            self.static_image_label.set_preview_images([])
            self.static_image_label.setText("无静态预览图")
            self.static_info_label.setText("【静态预览】\n尺寸：null\n大小：null\n后缀名：null\n")
    
        # 动态预览（保持原有逻辑）
        dynamic_preview_path = None
        for ext in DYNAMIC_PREVIEW_IMAGE_EXTS:
            path = base + ext
            if os.path.exists(path):
                dynamic_preview_path = path
                break
        dynamic_info = ""
        if dynamic_preview_path:
            try:
                if not hasattr(self, "_gif_player") or self._gif_player is None:
                    gif_player = GifPlayer(dynamic_preview_path, self.dynamic_image_label)
                    if gif_player.movie:
                        self._gif_player = gif_player
                    else:
                        self._gif_player = None
                        raise Exception("GIF加载失败")
                else:
                    if self._gif_player:
                        self._gif_player.set_gif(dynamic_preview_path)
                    else:
                        raise Exception("GIF加载失败")
                if self._gif_player and self._gif_player.movie:
                    self.dynamic_image_label.setMovie(self._gif_player.movie)
                    self.dynamic_image_label.setText("")
                    # 让 dynamic_image_label 记录当前 GIF 路径
                    self.dynamic_image_label.preview_paths = [dynamic_preview_path]
                    self.dynamic_image_label.current_index = 0
                    from PySide6.QtGui import QImageReader
                    reader = QImageReader(dynamic_preview_path)
                    size = reader.size()
                    width, height = size.width(), size.height()
                    file_size = os.path.getsize(dynamic_preview_path)
                    ext = os.path.splitext(dynamic_preview_path)[1]
                    dynamic_info = (
                        f"【动态预览】\n"
                        f"尺寸：{width}x{height}\n"
                        f"大小：{self.parent_gui.format_file_size(file_size) if self.parent_gui else f'{file_size}B'}\n"
                        f"后缀名：{ext.lstrip('.')}\n"
                    )
                    def scale_movie():
                        size = self._gif_player.movie.currentImage().size()
                        if size.width() > 0 and size.height() > 0:
                            max_height = 240
                            max_width = 240
                            w, h = size.width(), size.height()
                            if h > max_height:
                                w = int(w * max_height / h)
                                h = max_height
                            if w > max_width:
                                h = int(h * max_width / w)
                                w = max_width
                            self._gif_player.movie.setScaledSize(QSize(w, h))
                            self._gif_player.movie.frameChanged.disconnect(scale_movie)
                    self._gif_player.movie.frameChanged.connect(scale_movie)
                    self._gif_player.movie.start()
                    self._gif_player.movie.jumpToFrame(0)
                else:
                    raise Exception("GIF加载失败")
            except Exception as e:
                self.dynamic_image_label.setText("GIF加载失败")
                dynamic_info = "【动态预览】\nGIF加载失败\n"
                self.log(f"动态预览图像加载失败：{dynamic_preview_path} {e}")
        else:
            self.dynamic_image_label.setText("无动态预览图")
            dynamic_info = "【动态预览】\n尺寸：null\n大小：null\n后缀名：null\n"
    
        # 合并信息
        self.static_info_label.setText(self.get_static_info())
        self.dynamic_info_label.setText(dynamic_info)
        # 加载备注信息
        base = os.path.splitext(os.path.join(dir_path, filename))[0]
        json_path = base + ".json"
        data = {"description": "", "notes": "", "vae": ""}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data.update(json.load(f))
            except Exception:
                self.log(f"加载JSON失败: {json_path}")
                pass
        self.desc_edit.blockSignals(True)
        self.notes_edit.blockSignals(True)
        self.vae_edit.blockSignals(True)
        self.desc_edit.setText(data.get("description", ""))
        self.notes_edit.setText(data.get("notes", ""))
        self.vae_edit.setText(data.get("vae", ""))
        self.desc_edit.blockSignals(False)
        self.notes_edit.blockSignals(False)
        self.vae_edit.blockSignals(False)
        self._current_json_path = json_path

    def auto_save_json(self):
        description = self.desc_edit.toPlainText().strip()
        notes = self.notes_edit.toPlainText().strip()
        vae = self.vae_edit.toPlainText().strip()
        json_path = getattr(self, "_current_json_path", None)
        if not json_path:
            return
        data = {
            "description": description,
            "notes": notes,
            "vae": vae
        }
        empty_struct = {"description": "", "notes": "", "vae": ""}
        if data == empty_struct:
            if os.path.exists(json_path):
                try:
                    os.remove(json_path)
                    self.log(f"已删除空白备注JSON: {os.path.basename(json_path)}")
                except Exception:
                    self.log(f"删除空白JSON失败: {json_path}")
            else:
                self.log(f"空白JSON不存在，无需删除: {json_path}")
            return
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log(f"自动保存备注JSON: {os.path.basename(json_path)}")
        except Exception:
            self.log(f"保存JSON失败: {json_path}")
            pass

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = ModelClassifierGUI()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        QMessageBox.critical(None, "启动失败", str(e))