"""GstarCAD for Mac 后端：通过脚本桥驱动正在运行的 GstarCAD（macOS 无 COM 接口）

原理：经 macOS 辅助功能(System Events)把 _SCRIPT 与脚本路径经剪贴板粘贴(⌘V)到命令行并回车，
不键入任何字母（键入的字母随当前输入法变化，如泰文输入法），剪贴板随后恢复；
脚本(.scr，内含 AutoLISP)执行完毕后写出结果文件，Python 读取并解析(LISP S 表达式)。
每次工具调用 = 一个脚本往返（约 2~4 秒），期间 GstarCAD 会被切到前台，
此时用户不要在 GstarCAD 中打字（按键会进入其命令行）。

Mac 版限制（已实测）：没有 vla-* 对象模型；.scr 中空格/空行等同回车；未知命令会中止脚本。
因此：实体用 entmake/entmod 创建修改，含空格的参数一律放在 LISP 的 (command ...) 中。
需要给运行 MCP 服务器的终端/应用授予"辅助功能"权限。
"""
from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .cad_controller import CADController
except ImportError:
    from cad_controller import CADController

logger = logging.getLogger("gcad_mac")

WORK = Path("/tmp/cad_mcp_gcad")   # 短的纯ASCII路径，便于键入
GCAD_APP = os.environ.get("GCAD_MAC_APP", "/Applications/gstarcad.app")

_SUBMIT = r'''
on run argv
  set saved to missing value
  try
    set saved to the clipboard
  end try
  tell application "System Events"
    set p to first process whose unix id is (item 1 of argv as integer)
    set frontmost of p to true
    delay 0.4
    repeat 4 times
      key code 53
      delay 0.1
    end repeat
    set cmd to text field 1 of window 1 of p
    -- no typed letters (they follow the active input source, e.g. Thai); paste + key codes only
    set the clipboard to "_SCRIPT"
    try
      set focused of cmd to true
    end try
    delay 0.15
    key code 9 using command down
    delay 0.15
    key code 36
    delay 0.5
    set the clipboard to (item 2 of argv)
    try
      set focused of cmd to true
    end try
    delay 0.15
    key code 9 using command down
    delay 0.15
    key code 36
  end tell
  delay 0.3
  if saved is not missing value then
    try
      set the clipboard to saved
    end try
  end if
end run
'''

# DXF 实体类型 -> 友好类型名（与 COM 后端 _friendly_type 一致）
_FRIENDLY = {"LINE": "line", "CIRCLE": "circle", "ARC": "arc", "ELLIPSE": "ellipse", "LWPOLYLINE": "polyline",
             "POLYLINE": "polyline", "TEXT": "text", "MTEXT": "mtext", "HATCH": "hatch",
             "INSERT": "block_reference", "DIMENSION": "dimension"}

# 每个脚本开头加载的 LISP 辅助函数（纯 ASCII）
_HELPERS = r'''
(defun mcp:h (e) (if e (cdr (assoc 5 (entget e)))))
(defun mcp:ent (h / e) (if (and h (setq e (handent h)) (entget e)) e))
(defun mcp:last () (mcp:h (entlast)))
(defun mcp:props (e / d) (setq d (entget e)) (list (cdr (assoc 5 d)) (cdr (assoc 0 d)) (cdr (assoc 8 d)) (cdr (assoc 62 d))))
(defun mcp:quiet () (setq mcp:os (getvar "OSMODE")) (setvar "OSMODE" 0) (setvar "CMDECHO" 0))
(defun mcp:restore () (if mcp:os (setvar "OSMODE" mcp:os)))
(defun mcp:begin () (mcp:quiet) (command "_.UNDO" "_BE"))
(defun mcp:end () (command "_.UNDO" "_E") (mcp:restore))
(defun mcp:style (e lay col lw / d)
  (setq d (entget e))
  (if lay (setq d (if (assoc 8 d) (subst (cons 8 lay) (assoc 8 d) d) (append d (list (cons 8 lay))))))
  (if col (setq d (if (assoc 62 d) (subst (cons 62 col) (assoc 62 d) d) (append d (list (cons 62 col))))))
  (if lw (setq d (if (assoc 370 d) (subst (cons 370 lw) (assoc 370 d) d) (append d (list (cons 370 lw))))))
  (entmod d) (entupd e) e)
(defun mcp:layer-nth (i / r) (setq r (tblnext "LAYER" T)) (repeat i (setq r (tblnext "LAYER"))) (cdr (assoc 2 r)))
(defun mcp:ensure-layer (n)
  (if (not (tblsearch "LAYER" n))
    (entmake (list '(0 . "LAYER") '(100 . "AcDbSymbolTableRecord") '(100 . "AcDbLayerTableRecord")
                   (cons 2 n) '(70 . 0) '(62 . 7) '(6 . "Continuous"))))
  n)
'''


class GcadMacError(RuntimeError):
    pass


class _Ent:
    """轻量实体引用：服务器层只读取 .Handle"""

    def __init__(self, handle: str):
        self.Handle = handle

    def __repr__(self):
        return f"<GcadEntity {self.Handle}>"


# ---------------------------------------------------------------- LISP 文本编解码

def lisp_str(s: str) -> str:
    """Python 字符串 -> LISP 字符串字面量（非 ASCII 字符写成 \\U+XXXX，GstarCAD 会还原为真实字符）"""
    out = []
    for ch in str(s):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\\\U+{ord(ch):04X}")
    return '"' + "".join(out) + '"'


def lisp_val(v) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "T" if v else "nil"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return lisp_str(v)
    if isinstance(v, (list, tuple)):
        return "(list " + " ".join(lisp_val(x) for x in v) + ")"
    raise TypeError(type(v))


def lisp_pt(p) -> str:
    x, y = float(p[0]), float(p[1])
    z = float(p[2]) if len(p) > 2 else 0.0
    return f"(list {x!r} {y!r} {z!r})"


_UNI = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def parse_sexpr(text: str):
    """解析 vl-prin1-to-string 的输出（列表/字符串/数字/符号）"""
    pos = 0

    def skip():
        nonlocal pos
        while pos < len(text) and text[pos].isspace():
            pos += 1

    def read():
        nonlocal pos
        skip()
        if pos >= len(text):
            return None
        ch = text[pos]
        if ch == "(":
            pos += 1
            items = []
            while True:
                skip()
                if pos >= len(text):
                    return items
                if text[pos] == ")":
                    pos += 1
                    return items
                if text[pos] == "." and pos + 1 < len(text) and text[pos + 1].isspace():
                    pos += 1                       # 点对 (a . b) -> [a, b]
                    continue
                items.append(read())
        if ch == '"':
            pos += 1
            buf = []
            while pos < len(text) and text[pos] != '"':
                if text[pos] == "\\" and pos + 1 < len(text):
                    nxt = text[pos + 1]
                    if nxt == "U" and _UNI.match(text, pos):
                        buf.append(chr(int(text[pos + 3:pos + 7], 16)))
                        pos += 7
                        continue
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(nxt, "\\" + nxt))
                    pos += 2
                    continue
                buf.append(text[pos])
                pos += 1
            pos += 1
            return _UNI.sub(lambda m: chr(int(m.group(1), 16)), "".join(buf))
        start = pos
        while pos < len(text) and not text[pos].isspace() and text[pos] not in "()":
            pos += 1
        tok = text[start:pos]
        if tok.lower() == "nil":
            return None
        if tok == "T":
            return True
        try:
            return int(tok)
        except ValueError:
            try:
                return float(tok)
            except ValueError:
                return tok

    return read()


# ---------------------------------------------------------------- 控制器

class GcadMacController(CADController):
    """与 CADController 接口一致；每个方法 = 一次 GstarCAD 脚本往返"""

    def __init__(self):
        super().__init__()
        WORK.mkdir(parents=True, exist_ok=True)
        self._helpers = WORK / "mcp_helpers.lsp"
        self._helpers.write_text(_HELPERS, encoding="ascii")

    # --- 脚本桥 ---
    def _pid(self) -> Optional[int]:
        r = subprocess.run(["pgrep", "-n", "gcad"], capture_output=True, text=True)
        return int(r.stdout.split()[0]) if r.returncode == 0 and r.stdout.strip() else None

    def _run(self, body: List[str], result: str = "nil", post: List[str] = (), timeout: float = 60.0,
             script_lines: List[str] = ()):
        """执行一段 LISP（body）并返回 result 表达式的值（已解析）

        script_lines: 需要以原生脚本行形式发送的命令（如 _.OPEN 路径），放在 LISP 之前执行。
        post: 写出结果之后再执行的行（如关闭当前文档，之后脚本可能无法继续）。
        """
        pid = self._pid()
        if pid is None:
            self.app = self.doc = None
            raise GcadMacError("GstarCAD 未运行")
        tag = uuid.uuid4().hex[:8]
        done, scr, started = WORK / f"r{tag}.out", WORK / f"r{tag}.scr", WORK / f"r{tag}.go"
        # 第一行先写"已开始"标记：提交丢失（焦点不在命令行等）时可安全重发
        mark = f'(progn (close (open {lisp_str(str(started))} "w")) (princ))'
        # LISP 变量/函数属于单个文档：OPEN/NEW 之后必须重新加载辅助函数
        lines = [mark, "FILEDIA 0", *script_lines, f"(load {lisp_str(str(self._helpers))})",
                 "(setq mcp:err nil)", *body,
                 f'(progn (setq mcp:f (open {lisp_str(str(done))} "w")) '
                 f'(write-line (vl-prin1-to-string (list (quote ok) {result})) mcp:f) (close mcp:f) (princ))',
                 *post, ""]
        scr.write_text("\n".join(lines), encoding="ascii")
        for attempt in range(3):
            subprocess.run(["osascript", "-", str(pid), str(scr)], input=_SUBMIT, text=True,
                           capture_output=True, check=True)
            t_sub = time.time()
            while time.time() - t_sub < 6 and not started.exists() and not done.exists():
                time.sleep(0.2)
            if started.exists() or done.exists():
                break
            logger.warning(f"脚本提交未生效，重试（第{attempt + 1}次）")
        else:
            raise GcadMacError("无法向 GstarCAD 提交脚本（命令行未响应，可能有对话框打开）")
        t0 = time.time()
        while time.time() - t0 < timeout:
            if done.exists():
                time.sleep(0.15)
                text = done.read_text(encoding="utf-8", errors="replace")
                for f in (done, scr, started):
                    f.unlink(missing_ok=True)
                value = parse_sexpr(text)
                return value[1] if isinstance(value, list) and len(value) > 1 else None
            time.sleep(0.25)
        raise GcadMacError(f"GstarCAD 未在 {timeout:.0f} 秒内完成（可能有对话框未关闭，或执行期间有键盘输入）")

    def _try(self, fn, default, what: str):
        try:
            return fn()
        except Exception as e:
            logger.error(f"{what}失败: {e}")
            return default

    # --- 连接 ---
    def start_cad(self) -> bool:
        if self._pid() is None:
            logger.info("启动 GstarCAD for Mac ...")
            subprocess.run(["open", "-a", GCAD_APP], check=False)
            for _ in range(int(self.startup_wait_time * 3)):
                time.sleep(1)
                if self._pid() and self._window_count() > 0:
                    break
            time.sleep(3)
        try:
            name = self._run([], result='(getvar "DWGNAME")', timeout=60)
            self.app, self.doc = "gcad-mac", name
            logger.info(f"已连接 GstarCAD for Mac，当前图纸: {name}")
            return True
        except Exception as e:
            logger.error(f"连接 GstarCAD for Mac 失败: {e}")
            self.app = self.doc = None
            return False

    def _window_count(self) -> int:
        pid = self._pid()
        if not pid:
            return 0
        r = subprocess.run(["osascript", "-e", f'tell application "System Events" to count windows of '
                                                f'(first process whose unix id is {pid})'],
                           capture_output=True, text=True)
        try:
            return int(r.stdout.strip())
        except ValueError:
            return 0

    def is_running(self) -> bool:
        """GstarCAD 进程存在即视为可用：首次调用时自动连接（查询类工具不会主动启动CAD）"""
        if self._pid() is None:
            self.app = self.doc = None
            return False
        if self.app is None:
            self.start_cad()
        return self.app is not None

    def close(self) -> None:
        self.app = self.doc = None

    # --- 图层名解析 ---
    def _layer_names(self) -> List[str]:
        rows = self._run(['(setq mcp:r nil mcp:l (tblnext "LAYER" T))',
                          '(while mcp:l (setq mcp:r (cons (cdr (assoc 2 mcp:l)) mcp:r) mcp:l (tblnext "LAYER")))'],
                         result="(reverse mcp:r)")
        return list(rows or [])

    def _layer_expr(self, name: Optional[str]) -> str:
        """图层名 -> LISP 表达式

        非 ASCII 名称（中文/泰文）以 \\U+XXXX 转义写入时，tblsearch 找不到已有图层、entmake 却会
        新建一个同名图层；因此先在图层表中定位，按序号取回真实名称。
        """
        if not name:
            return "nil"
        if name.isascii():
            return f"(mcp:ensure-layer {lisp_str(name)})"
        names = self._layer_names()
        if name in names:
            return f"(mcp:layer-nth {names.index(name)})"
        return f"(mcp:ensure-layer {lisp_str(name)})"

    # --- 绘图（entmake，不经命令行，避免捕捉干扰） ---
    def _make(self, dxf: str, layer, color, lineweight, what: str):
        lw = self.validate_lineweight(lineweight)
        lay = "mcp:lay" if layer else "nil"
        body = ["(mcp:begin)",
                (f"(setq mcp:lay {self._layer_expr(layer)})" if layer else ""),
                f"(setq mcp:r (if (entmake {dxf}) (mcp:style (entlast) {lay} {lisp_val(color)} {lisp_val(lw)})))",
                "(mcp:end)"]
        h = self._try(lambda: self._run([b for b in body if b], result="(if mcp:r (mcp:h mcp:r))"), None, what)
        return _Ent(h) if h else None

    def draw_line(self, start_point, end_point, layer=None, color=None, lineweight=None):
        return self._make(f"(list '(0 . \"LINE\") (cons 10 {lisp_pt(start_point)}) (cons 11 {lisp_pt(end_point)}))",
                          layer, color, lineweight, "绘制直线")

    def draw_circle(self, center, radius, layer=None, color=None, lineweight=None):
        if radius <= 0:
            return None
        return self._make(f"(list '(0 . \"CIRCLE\") (cons 10 {lisp_pt(center)}) (cons 40 {float(radius)!r}))",
                          layer, color, lineweight, "绘制圆")

    def draw_arc(self, center, radius, start_angle, end_angle, layer=None, color=None, lineweight=None):
        if radius <= 0:
            return None
        return self._make(f"(list '(0 . \"ARC\") (cons 10 {lisp_pt(center)}) (cons 40 {float(radius)!r}) "
                          f"(cons 50 {math.radians(start_angle)!r}) (cons 51 {math.radians(end_angle)!r}))",
                          layer, color, lineweight, "绘制圆弧")

    def draw_ellipse(self, center, major_axis, minor_axis, rotation=0, layer=None, color=None, lineweight=None):
        if major_axis <= 0 or minor_axis <= 0 or minor_axis > major_axis:
            return None
        a = math.radians(rotation or 0)
        major = (major_axis * math.cos(a), major_axis * math.sin(a), 0.0)
        return self._make("(list '(0 . \"ELLIPSE\") '(100 . \"AcDbEntity\") '(100 . \"AcDbEllipse\") "
                          f"(cons 10 {lisp_pt(center)}) (cons 11 {lisp_pt(major)}) (cons 40 {minor_axis / major_axis!r}) "
                          f"(cons 41 0.0) (cons 42 {2 * math.pi!r}))", layer, color, lineweight, "绘制椭圆")

    def draw_polyline(self, points, closed=False, layer=None, color=None, lineweight=None):
        if not points or len(points) < 2:
            return None
        verts = " ".join(f"(cons 10 (list {float(p[0])!r} {float(p[1])!r}))" for p in points)
        return self._make("(list '(0 . \"LWPOLYLINE\") '(100 . \"AcDbEntity\") '(100 . \"AcDbPolyline\") "
                          f"(cons 90 {len(points)}) (cons 70 {1 if closed and len(points) > 2 else 0}) {verts})",
                          layer, color, lineweight, "绘制多段线")

    def draw_rectangle(self, corner1, corner2, layer=None, color=None, lineweight=None):
        x1, y1, x2, y2 = corner1[0], corner1[1], corner2[0], corner2[1]
        return self.draw_polyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True, layer, color, lineweight)

    def draw_text(self, position, text, height=2.5, rotation=0, layer=None, color=None):
        return self._make(f"(list '(0 . \"TEXT\") (cons 10 {lisp_pt(position)}) (cons 40 {float(height)!r}) "
                          f"(cons 1 {lisp_str(text)}) (cons 50 {math.radians(rotation or 0)!r}))",
                          layer, color, None, "添加文本")

    def draw_hatch(self, points, pattern_name="SOLID", scale=1.0, layer=None, color=None):
        if not points or len(points) < 3:
            return None
        boundary = self.draw_polyline(points, True, layer)
        if boundary is None:
            return None
        pat = lisp_str(pattern_name)
        opts = "" if pattern_name.upper() == "SOLID" else f" {lisp_str(str(scale))} \"0\""
        body = ["(mcp:begin)", f"(setq mcp:b (mcp:ent {lisp_str(boundary.Handle)}) mcp:before (entlast))",
                f'(command "_.-HATCH" "_P" {pat}{opts} "_S" mcp:b "" "")',
                "(setq mcp:r (if (not (eq (entlast) mcp:before)) (entlast)))",
                f"(if mcp:r (mcp:style mcp:r {self._layer_expr(layer)} {lisp_val(color)} nil))",
                "(mcp:end)"]
        h = self._try(lambda: self._run(body, result="(if mcp:r (mcp:h mcp:r))"), None, "创建填充")
        if not h:
            self.erase_entity(boundary.Handle)
        return _Ent(h) if h else None

    def add_dimension(self, start_point, end_point, text_position=None, textheight=5, layer=None, color=None):
        if text_position is None:
            text_position = ((start_point[0] + end_point[0]) / 2, (start_point[1] + end_point[1]) / 2 + 5, 0)
        body = ["(mcp:begin)", "(setq mcp:before (entlast))",
                (f'(setvar "DIMTXT" {float(textheight)!r})' if textheight else ""),
                f'(command "_.DIMALIGNED" {lisp_pt(start_point)} {lisp_pt(end_point)} {lisp_pt(text_position)})',
                "(setq mcp:r (if (not (eq (entlast) mcp:before)) (entlast)))",
                f"(if mcp:r (mcp:style mcp:r {self._layer_expr(layer)} {lisp_val(color)} nil))",
                "(mcp:end)"]
        h = self._try(lambda: self._run([b for b in body if b], result="(if mcp:r (mcp:h mcp:r))"), None, "添加标注")
        return _Ent(h) if h else None

    def zoom_extents(self) -> bool:
        return self._try(lambda: self._run(['(command "_.ZOOM" "_E")'], result="T"), False, "缩放视图") is True

    def refresh_view(self) -> None:
        self._try(lambda: self._run(['(command "_.REGEN")']), None, "刷新视图")

    # --- 图层 ---
    def create_layer(self, layer_name: str) -> bool:
        return self._try(lambda: self._run([f"(setq mcp:lay {self._layer_expr(layer_name)})", '(setvar "CLAYER" mcp:lay)'],
                                           result='(getvar "CLAYER")') is not None, False, "创建图层")

    def set_current_layer(self, layer_name: str) -> bool:
        def go():
            names = self._layer_names()
            match = next((i for i, n in enumerate(names) if n.casefold() == layer_name.casefold()), None)
            if match is None:
                return False
            return self._run([f'(setvar "CLAYER" (mcp:layer-nth {match}))'], result="T") is True
        return self._try(go, False, "切换图层")

    def list_layers(self) -> List[Dict[str, Any]]:
        body = ['(setq mcp:r nil mcp:l (tblnext "LAYER" T))',
                '(while mcp:l (setq mcp:r (cons (list (cdr (assoc 2 mcp:l)) (cdr (assoc 62 mcp:l)) (cdr (assoc 70 mcp:l))) mcp:r) mcp:l (tblnext "LAYER")))']
        res = self._try(lambda: self._run(body, result='(list (getvar "CLAYER") (reverse mcp:r))'), None, "列出图层")
        if not res:
            return []
        current, rows = res[0], res[1] or []
        out = []
        for name, color, flags in rows:
            out.append({"name": name, "color": abs(color) if color is not None else None,
                        "is_current": name == current, "is_frozen": bool((flags or 0) & 1),
                        "is_locked": bool((flags or 0) & 4), "is_on": (color or 0) >= 0})
        return out

    # --- 查询 ---
    def list_entities(self, entity_type: str = None, limit: int = 50) -> Dict[str, Any]:
        body = ['(setq mcp:ss (ssget "_X" (list (cons 410 "Model"))) mcp:r nil mcp:i 0)',
                '(setq mcp:n (if mcp:ss (sslength mcp:ss) 0))',
                f'(while (and mcp:ss (< mcp:i mcp:n) (< (length mcp:r) {int(limit) * 4})) '
                f'(setq mcp:r (cons (mcp:props (ssname mcp:ss mcp:i)) mcp:r) mcp:i (1+ mcp:i)))']
        res = self._try(lambda: self._run(body, result="(list mcp:n (reverse mcp:r))", timeout=120), None, "列出实体")
        if not res:
            return {"total": 0, "entities": []}
        total, rows = res[0] or 0, res[1] or []
        ents = []
        for h, t, lay, col in rows:
            info = {"handle": h, "entity_type": _FRIENDLY.get(t, t), "layer": lay, "color": col if col is not None else 256}
            if entity_type and info["entity_type"].lower() != entity_type.lower():
                continue
            ents.append(info)
            if len(ents) >= limit:
                break
        return {"total": total, "entities": ents}

    def get_entity_properties(self, handle: str) -> Dict[str, Any]:
        body = [f"(setq mcp:e (mcp:ent {lisp_str(handle)}))",
                "(setq mcp:r (if mcp:e (vl-remove-if (function (lambda (p) (or (= (type (car p)) (quote STR)) "
                "(member (car p) (list -1 -2 330 360 102 5))))) (entget mcp:e))))"]
        d = self._run(body, result="mcp:r")
        if not d:
            raise ValueError(f"未找到句柄为 {handle} 的实体")
        g = {}
        for pair in d:
            if isinstance(pair, list) and pair:
                key, val = pair[0], (pair[1:] if len(pair) > 2 else (pair[1] if len(pair) > 1 else None))
                g.setdefault(key, []).append(val)
        one = lambda k, default=None: (g.get(k) or [default])[0]
        t = one(0, "")
        props = {"handle": str(handle), "entity_type": _FRIENDLY.get(t, t), "layer": one(8),
                 "color": one(62, 256), "linetype": one(6, "ByLayer"), "lineweight": one(370, -1)}
        pt = lambda k: list(one(k) or [])
        detail: Dict[str, Any] = {}
        if t == "LINE":
            a, b = pt(10), pt(11)
            detail = {"start_point": a, "end_point": b, "length": math.dist(a[:2], b[:2]) if a and b else None}
        elif t in ("CIRCLE", "ARC"):
            detail = {"center": pt(10), "radius": one(40)}
            if t == "CIRCLE":
                detail["area"] = math.pi * one(40, 0) ** 2
            else:
                detail["start_angle"] = math.degrees(one(50, 0))
                detail["end_angle"] = math.degrees(one(51, 0))
        elif t == "ELLIPSE":
            detail = {"center": pt(10), "radius_ratio": one(40), "start_param": one(41), "end_param": one(42)}
        elif t in ("TEXT", "MTEXT"):
            detail = {"text": one(1), "insertion_point": pt(10), "height": one(40)}
            if t == "TEXT":
                detail["rotation"] = math.degrees(one(50, 0))
        elif t == "LWPOLYLINE":
            coords = [list(v)[:2] for v in g.get(10, [])]
            closed = bool(one(70, 0) & 1)
            ring = coords + (coords[:1] if closed else [])
            detail = {"coordinates": coords, "closed": closed,
                      "length": sum(math.dist(a, b) for a, b in zip(ring, ring[1:]))}
        elif t == "HATCH":
            detail = {"pattern_name": one(2)}
        elif t == "INSERT":
            detail = {"name": one(2), "insertion_point": pt(10), "x_scale": one(41, 1.0), "y_scale": one(42, 1.0),
                      "rotation": math.degrees(one(50, 0))}
        props["properties"] = detail
        return props

    def get_drawing_stats(self) -> Dict[str, Any]:
        body = ['(setq mcp:ss (ssget "_X" (list (cons 410 "Model"))) mcp:nl 0 mcp:l (tblnext "LAYER" T))',
                '(while mcp:l (setq mcp:nl (1+ mcp:nl) mcp:l (tblnext "LAYER")))']
        res = self._try(lambda: self._run(body, result='(list (if mcp:ss (sslength mcp:ss) 0) mcp:nl (getvar "DWGNAME"))'),
                        None, "统计")
        if not res:
            return {"running": False}
        return {"running": True, "entity_count": res[0], "layer_count": res[1], "document_name": res[2]}

    def screenshot(self) -> bytes:
        """以 PublishToWeb PNG 打印当前模型视图（Mac 无法直接截取窗口）"""
        out = WORK / f"shot-{uuid.uuid4().hex[:8]}.png"
        body = ['(setvar "CTAB" "Model")', '(command "_.ZOOM" "_E")',
                '(command "_.-PLOT" "_Y" "Model" "PublishToWeb PNG.pc3")',
                '(while (> (getvar "CMDACTIVE") 0) (command ""))']
        prefix = self._run(body, result='(list (getvar "DWGPREFIX") (getvar "DWGNAME"))', timeout=120)
        folder, name = Path(prefix[0]), Path(prefix[1]).stem
        candidates = sorted([*folder.glob(f"{name}-Model*.png"), *Path.home().glob(f"Documents/{name}-Model*.png")],
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            raise RuntimeError("截图失败：未找到打印输出的 PNG")
        data = candidates[0].read_bytes()
        candidates[0].replace(out)
        return data

    # --- 编辑 ---
    def _edit(self, handle: str, command: str, what: str, new_entity: bool = False):
        body = ["(mcp:begin)", f"(setq mcp:e (mcp:ent {lisp_str(handle)}) mcp:before (entlast))",
                f"(if mcp:e {command})",
                "(setq mcp:r (if (and mcp:e (not (eq (entlast) mcp:before))) (entlast)))", "(mcp:end)"]
        res = self._try(lambda: self._run(body, result="(list (if mcp:e T) (if mcp:r (mcp:h mcp:r)))"), None, what)
        if not res or not res[0]:
            return None if new_entity else False
        if new_entity:
            return _Ent(res[1]) if res[1] else None
        return True

    def erase_entity(self, handle: str) -> bool:
        return bool(self._edit(handle, "(entdel mcp:e)", "删除实体"))

    def move_entity(self, handle: str, displacement) -> bool:
        return bool(self._edit(handle, f'(command "_.MOVE" mcp:e "" (list 0.0 0.0 0.0) {lisp_pt(displacement)})', "移动实体"))

    def rotate_entity(self, handle: str, base_point, angle_degrees: float) -> bool:
        return bool(self._edit(handle, f'(command "_.ROTATE" mcp:e "" {lisp_pt(base_point)} {float(angle_degrees)!r})', "旋转实体"))

    def scale_entity(self, handle: str, base_point, scale_factor: float) -> bool:
        return bool(self._edit(handle, f'(command "_.SCALE" mcp:e "" {lisp_pt(base_point)} {float(scale_factor)!r})', "缩放实体"))

    def copy_entity(self, handle: str, displacement):
        return self._edit(handle, f'(command "_.COPY" mcp:e "" (list 0.0 0.0 0.0) {lisp_pt(displacement or (0, 0, 0))})',
                          "复制实体", new_entity=True)

    def mirror_entity(self, handle: str, point1, point2):
        return self._edit(handle, f'(command "_.MIRROR" mcp:e "" {lisp_pt(point1)} {lisp_pt(point2)} "_N")',
                          "镜像实体", new_entity=True)

    def offset_entity(self, handle: str, distance: float) -> List[Any]:
        """偏移：正值向外/左侧，负值向内/右侧（侧点由几何计算）"""
        try:
            props = self.get_entity_properties(handle)
        except Exception as e:
            logger.error(f"偏移实体失败: {e}")
            return []
        t, p, d = props["entity_type"], props["properties"], float(distance)
        if t in ("circle", "arc"):
            c, r = p["center"], p["radius"]
            side = (c[0] + (r * 3 + abs(d) if d > 0 else r * 0.25), c[1], 0.0)
        elif t == "line":
            (x1, y1), (x2, y2) = p["start_point"][:2], p["end_point"][:2]
            L = math.hypot(x2 - x1, y2 - y1) or 1.0
            nx, ny = -(y2 - y1) / L, (x2 - x1) / L
            k = 1 if d > 0 else -1
            side = ((x1 + x2) / 2 + k * nx * (abs(d) + 1), (y1 + y2) / 2 + k * ny * (abs(d) + 1), 0.0)
        elif t == "polyline" and p.get("coordinates"):
            xs = [c[0] for c in p["coordinates"]]
            ys = [c[1] for c in p["coordinates"]]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            side = ((max(xs) + abs(d) * 3 + 1, cy, 0.0) if d > 0 else (cx, cy, 0.0))
        else:
            logger.error(f"偏移实体失败: 不支持的类型 {t}")
            return []
        ent = self._edit(handle, f'(command "_.OFFSET" {abs(d)!r} mcp:e {lisp_pt(side)} "")', "偏移实体", new_entity=True)
        return [ent] if ent else []

    def array_linear_entity(self, handle: str, count: int, displacement) -> List[Any]:
        dx, dy = float(displacement[0]), float(displacement[1])
        dz = float(displacement[2]) if len(displacement) > 2 else 0.0
        body = ["(mcp:begin)", f"(setq mcp:e (mcp:ent {lisp_str(handle)}) mcp:r nil mcp:i 1)",
                f"(while (and mcp:e (< mcp:i {int(count)})) "
                f'(command "_.COPY" mcp:e "" (list 0.0 0.0 0.0) (list (* mcp:i {dx!r}) (* mcp:i {dy!r}) (* mcp:i {dz!r}))) '
                "(setq mcp:r (cons (mcp:last) mcp:r) mcp:i (1+ mcp:i)))", "(mcp:end)"]
        hs = self._try(lambda: self._run(body, result="(reverse mcp:r)", timeout=300), None, "线性阵列") or []
        return [_Ent(h) for h in hs if h]

    def array_polar_entity(self, handle: str, count: int, center, total_angle_degrees: float = 360.0) -> List[Any]:
        step = float(total_angle_degrees) / int(count)
        body = ["(mcp:begin)", f"(setq mcp:e (mcp:ent {lisp_str(handle)}) mcp:r nil mcp:i 1)",
                f"(while (and mcp:e (< mcp:i {int(count)})) "
                f'(command "_.COPY" mcp:e "" (list 0.0 0.0 0.0) (list 0.0 0.0 0.0)) '
                f'(command "_.ROTATE" (entlast) "" {lisp_pt(center)} (* mcp:i {step!r})) '
                "(setq mcp:r (cons (mcp:last) mcp:r) mcp:i (1+ mcp:i)))", "(mcp:end)"]
        hs = self._try(lambda: self._run(body, result="(reverse mcp:r)", timeout=300), None, "环形阵列") or []
        return [_Ent(h) for h in hs if h]

    def undo(self) -> bool:
        return self._try(lambda: self._run(['(command "_.UNDO" "1")'], result="T"), False, "撤销") is True

    def redo(self) -> bool:
        return self._try(lambda: self._run(['(command "_.REDO")'], result="T"), False, "重做") is True

    # --- 图纸 ---
    def new_drawing(self) -> bool:
        res = self._try(lambda: self._run([], result='(getvar "DWGNAME")', script_lines=["_.NEW", "."]), None, "新建文档")
        if res:
            self.doc = res
        return bool(res)

    def open_drawing(self, file_path: str) -> bool:
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        if " " in str(path) or not str(path).isascii():
            # 脚本的文件提示不接受空格/非 ASCII：用临时副本打开（另存时请指定新路径）
            safe = WORK / ("open-" + uuid.uuid4().hex[:6] + path.suffix.lower())
            safe.write_bytes(path.read_bytes())
            logger.warning(f"路径含空格或非ASCII字符，改为打开副本: {safe}")
            path = safe
        res = self._try(lambda: self._run([], result='(getvar "DWGNAME")', script_lines=["_.OPEN", str(path)],
                                          timeout=180), None, "打开文档")
        if res:
            self.doc = res
        return bool(res)

    def save_drawing(self, file_path: str) -> bool:
        path = Path(file_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() not in (".dwg", ".dxf"):
            path = path.with_suffix(".dwg")
        fmt = '"_DXF" "16"' if path.suffix.lower() == ".dxf" else '"2018"'
        overwrite = ' "_Y"' if path.exists() else ""
        res = self._try(lambda: self._run([f'(command "_.SAVEAS" {fmt} {lisp_str(str(path))}{overwrite})'],
                                          result='(getvar "DWGNAME")', timeout=180), None, "保存图纸")
        return bool(res) and path.exists()

    def close_drawing(self, save: bool = False) -> bool:
        """关闭当前文档：结果先写出，之后再执行 CLOSE（关闭后脚本无法继续）"""
        post = ['(command "_.QSAVE")'] if save else []
        post += ['(command "_.CLOSE" "_N")' if not save else '(command "_.CLOSE")']
        return self._try(lambda: self._run([], result="T", post=post), False, "关闭文档") is True

    # --- 图块 ---
    def create_block(self, name: str, base_point=None):
        n = lisp_str(name)
        body = [f'(if (not (tblsearch "BLOCK" {n})) (progn (entmake (list (quote (0 . "BLOCK")) (cons 2 {n}) '
                f"(quote (70 . 0)) (cons 10 {lisp_pt(base_point or (0, 0, 0))}))) "
                "(entmake (list (quote (0 . \"ENDBLK\"))))))",
                f'(setq mcp:r (tblobjname "BLOCK" {n}))']
        h = self._try(lambda: self._run(body, result="(if mcp:r (mcp:h mcp:r))"), None, "创建图块")
        return _Ent(h) if h else None

    def insert_block(self, name: str, position, x_scale=1.0, y_scale=1.0, z_scale=1.0, rotation_degrees=0.0):
        n = lisp_str(name)
        dxf = (f"(list '(0 . \"INSERT\") (cons 2 {n}) (cons 10 {lisp_pt(position)}) (cons 41 {float(x_scale)!r}) "
               f"(cons 42 {float(y_scale)!r}) (cons 43 {float(z_scale)!r}) (cons 50 {math.radians(rotation_degrees)!r}))")
        body = ["(mcp:begin)", f'(setq mcp:r (if (and (tblsearch "BLOCK" {n}) (entmake {dxf})) (entlast)))', "(mcp:end)"]
        h = self._try(lambda: self._run(body, result="(if mcp:r (mcp:h mcp:r))"), None, "插入图块")
        return _Ent(h) if h else None

    def list_blocks(self) -> List[Dict[str, Any]]:
        body = ['(setq mcp:r nil mcp:b (tblnext "BLOCK" T))',
                '(while mcp:b (if (/= (substr (cdr (assoc 2 mcp:b)) 1 1) "*") (progn (setq mcp:c 0 mcp:x (cdr (assoc -2 mcp:b))) '
                '(while mcp:x (setq mcp:c (1+ mcp:c) mcp:x (entnext mcp:x))) (setq mcp:r (cons (list (cdr (assoc 2 mcp:b)) mcp:c) mcp:r)))) '
                '(setq mcp:b (tblnext "BLOCK")))']
        rows = self._try(lambda: self._run(body, result="(reverse mcp:r)", timeout=120), None, "列出图块") or []
        return [{"name": n, "entity_count": c} for n, c in rows]

    # --- 命令直通 ---
    def send_command(self, command: str) -> bool:
        """命令按脚本行原样发送：与在命令行键入一致（空格=回车）"""
        lines = [ln for ln in command.replace("\r", "\n").split("\n") if ln.strip()]
        return self._try(lambda: self._run([], result="T", script_lines=lines, timeout=120), False, "执行命令") is True

    # --- 模板相关（Mac 后端实现，供 replace_text / plot_to_pdf 工具使用） ---
    def replace_text(self, find: str, replace: str) -> int:
        """在模型空间、所有布局、图块定义（含标题栏）与属性中替换文字，返回修改的对象数"""
        f, r = lisp_str(find), lisp_str(replace)
        body = [
            "(setq mcp:n 0)",
            "(defun mcp:rep (e / d s new) (setq d (entget e)) "
            '(if (and (member (cdr (assoc 0 d)) (list "TEXT" "MTEXT" "ATTRIB" "ATTDEF")) (setq s (cdr (assoc 1 d)))) '
            f"(progn (setq new (mcp:subst-all s {f} {r})) (if (/= new s) (progn (entmod (subst (cons 1 new) (assoc 1 d) d)) "
            "(entupd e) (setq mcp:n (1+ mcp:n)))))))",
            "(defun mcp:subst-all (s a b / i out) (setq out \"\") "
            "(while (setq i (vl-string-search a s)) (setq out (strcat out (substr s 1 i) b) s (substr s (+ i 1 (strlen a))))) "
            "(strcat out s))",
            # 模型与图纸空间中的实体（以及插入的属性）
            '(setq mcp:ss (ssget "_X") mcp:i 0)',
            "(if mcp:ss (repeat (sslength mcp:ss) (setq mcp:e (ssname mcp:ss mcp:i)) (mcp:rep mcp:e) "
            '(if (= (cdr (assoc 0 (entget mcp:e))) "INSERT") (progn (setq mcp:a (entnext mcp:e)) '
            '(while (and mcp:a (= (cdr (assoc 0 (entget mcp:a))) "ATTRIB")) (mcp:rep mcp:a) (setq mcp:a (entnext mcp:a))))) '
            "(setq mcp:i (1+ mcp:i))))",
            # 图块定义（标题栏等）
            '(setq mcp:b (tblnext "BLOCK" T))',
            "(while mcp:b (setq mcp:x (cdr (assoc -2 mcp:b))) (while mcp:x (mcp:rep mcp:x) (setq mcp:x (entnext mcp:x))) "
            '(setq mcp:b (tblnext "BLOCK")))',
            '(command "_.REGEN")',
        ]
        n = self._run(body, result="mcp:n", timeout=600)
        return int(n or 0)

    def plot_to_pdf(self, output_path: str, paper: str = "ISO full bleed A3 (420.00 x 297.00 MM)") -> Dict[str, Any]:
        """用 DWG To PDF 打印所有布局（GstarCAD 自身打印，字体/线宽与打印样式真实）并合并为一个 PDF"""
        body = ["(setq mcp:lays (layoutlist))",
                f'(foreach l mcp:lays (command "_.-PLOT" "_Y" l "DWG To PDF.pc3" {lisp_str(paper)}) '
                '(while (> (getvar "CMDACTIVE") 0) (command "")))']
        t0 = time.time()
        res = self._run(body, result='(list (getvar "DWGPREFIX") (getvar "DWGNAME") mcp:lays)', timeout=3600)
        folder, stem, layouts = Path(res[0]), Path(res[1]).stem, res[2] or []
        pdfs = []
        for lay in layouts:
            p = folder / f"{stem}-{lay}.pdf"
            if p.exists() and p.stat().st_mtime >= t0 - 5:
                pdfs.append(p)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            from pypdf import PdfWriter
            w = PdfWriter()
            for p in pdfs:
                w.append(str(p))
            with open(out, "wb") as fh:
                w.write(fh)
            for p in pdfs:
                p.unlink(missing_ok=True)
        except ImportError:
            out = folder
            logger.warning("未安装 pypdf：各布局 PDF 保留在图纸目录中")
        return {"pdf": str(out), "layouts": len(layouts), "plotted": len(pdfs)}
