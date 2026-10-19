"""假CAD后端：在内存中模拟 AutoCAD COM 对象模型

用于在 macOS/Linux（或没有安装CAD的Windows）上启动服务器、调试工具。
提供与 win32com.client / pythoncom 同名的替身对象，cad_controller 的
业务代码无需修改即可在其上运行。启用方式：环境变量 CAD_MCP_FAKE=1
或 server.py 的 --fake 参数。

限制：
- 图纸保存为JSON格式（扩展名仍为调用方指定的.dwg），只有本后端能打开；
- send_command 只识别 UNDO/U/REDO/ZOOM/REGEN，其余命令仅记录日志；
- 多段线不支持偏移(Offset)；
- screenshot 返回按实体几何渲染的示意图，而非真实CAD窗口。
"""

import copy
import io
import json
import logging
import math
import os
import types
from typing import Any, Dict, List, Tuple

logger = logging.getLogger('fake_cad')

_FORMAT_TAG = "cad-mcp-fake/1"


# ==================== win32com / pythoncom 替身 ====================

class com_error(Exception):
    """模拟 pythoncom.com_error（hresult 为空，不会触发重试逻辑）"""
    hresult = None
    excepinfo = None


class VARIANT:
    """模拟 win32com.client.VARIANT：仅保存类型与值"""

    def __init__(self, vt, value):
        self.varianttype = vt
        self.value = value


_APP = None


def _get_app(prog_id=None):
    """返回进程内唯一的假CAD应用（模拟"CAD已在运行"）"""
    global _APP
    if _APP is None:
        _APP = Application()
        logger.warning(f"使用假CAD后端（请求的ProgID: {prog_id}），所有绘图仅存在于内存中")
    return _APP


pythoncom = types.SimpleNamespace(
    CoInitialize=lambda: None,
    CoUninitialize=lambda: None,
    VT_ARRAY=0x2000,
    VT_R8=5,
    VT_DISPATCH=9,
    com_error=com_error,
)

win32com = types.SimpleNamespace(
    client=types.SimpleNamespace(
        VARIANT=VARIANT,
        GetActiveObject=_get_app,
        Dispatch=_get_app,
    )
)


# ==================== 几何工具 ====================

def _vals(v) -> list:
    return list(v.value) if isinstance(v, VARIANT) else list(v)


def _p3(v) -> Tuple[float, float, float]:
    a = [float(x) for x in _vals(v)]
    while len(a) < 3:
        a.append(0.0)
    return (a[0], a[1], a[2])


def _add(p, d):
    return (p[0] + d[0], p[1] + d[1], p[2] + d[2])


def _sub(p, q):
    return (p[0] - q[0], p[1] - q[1], p[2] - q[2])


def _dist(p, q) -> float:
    return math.dist(p[:3], q[:3])


def _rotator(base, ang):
    c, s = math.cos(ang), math.sin(ang)

    def f(p):
        x, y = p[0] - base[0], p[1] - base[1]
        return (base[0] + x * c - y * s, base[1] + x * s + y * c, p[2])
    return f


def _scaler(base, k):
    return lambda p: (base[0] + (p[0] - base[0]) * k,
                      base[1] + (p[1] - base[1]) * k,
                      base[2] + (p[2] - base[2]) * k)


def _mirrorer(p1, p2):
    """沿 p1-p2 直线镜像（XY平面），返回 (变换函数, 镜像轴角度)"""
    theta = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    c, s = math.cos(2 * theta), math.sin(2 * theta)

    def f(p):
        x, y = p[0] - p1[0], p[1] - p1[1]
        return (p1[0] + x * c + y * s, p1[1] + x * s - y * c, p[2])
    return f, theta


def _sample_arc(center, r, a0, a1, n=64) -> List[tuple]:
    if a1 <= a0:
        a1 += 2 * math.pi
    return [(center[0] + r * math.cos(a0 + (a1 - a0) * i / n),
             center[1] + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def _shoelace(pts) -> float:
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                   for i in range(n))) / 2


# ==================== 实体 ====================

class _Entity:
    ObjectName = ""

    def __init__(self, doc):
        self._doc = doc
        self.Handle = doc._new_handle()
        self.Layer = doc.state["active_layer"]
        self.color = 256        # ByLayer
        self.Lineweight = -1    # ByLayer
        self.Linetype = "ByLayer"

    @property
    def Color(self):
        return self.color

    @Color.setter
    def Color(self, value):
        self.color = value

    # --- 编辑 ---
    def Delete(self):
        self._doc.state["entities"].remove(self)

    def Move(self, p1, p2):
        d = _sub(_p3(p2), _p3(p1))
        self._xform(lambda p: _add(p, d), 0.0, 1.0, None)

    def Rotate(self, base, angle):
        self._xform(_rotator(_p3(base), float(angle)), float(angle), 1.0, None)

    def ScaleEntity(self, base, factor):
        factor = float(factor)
        if factor <= 0:
            raise com_error("比例因子必须为正数")
        self._xform(_scaler(_p3(base), factor), 0.0, factor, None)

    def Copy(self):
        new = copy.deepcopy(self, {id(self._doc): self._doc})
        new.Handle = self._doc._new_handle()
        self._doc.state["entities"].append(new)
        return new

    def Mirror(self, p1, p2):
        f, theta = _mirrorer(_p3(p1), _p3(p2))
        new = self.Copy()
        new._xform(f, 0.0, 1.0, theta)
        return new

    def Offset(self, distance):
        raise com_error(f"{self.ObjectName} 不支持偏移（假后端）")

    # --- 子类实现 ---
    def _xform(self, f, angle, scale, mirror_theta):
        """f: 点变换；angle: 旋转角；scale: 比例；mirror_theta: 镜像轴角度（非镜像为None）"""
        raise NotImplementedError

    def _paths(self) -> List[List[tuple]]:
        """渲染用折线（二维点列表）"""
        return []

    def _label(self):
        """渲染用文字 (位置, 文本, 高度)，无文字时返回None"""
        return None


class _Line(_Entity):
    ObjectName = "AcDbLine"

    def __init__(self, doc, start, end):
        super().__init__(doc)
        self.StartPoint, self.EndPoint = start, end

    @property
    def Length(self):
        return _dist(self.StartPoint, self.EndPoint)

    def _xform(self, f, angle, scale, mirror_theta):
        self.StartPoint, self.EndPoint = f(self.StartPoint), f(self.EndPoint)

    def Offset(self, distance):
        length = self.Length
        if length == 0:
            raise com_error("零长度直线无法偏移")
        dx, dy = (self.EndPoint[0] - self.StartPoint[0]) / length, (self.EndPoint[1] - self.StartPoint[1]) / length
        n = (-dy * distance, dx * distance, 0.0)
        new = self.Copy()
        new._xform(lambda p: _add(p, n), 0.0, 1.0, None)
        return (new,)

    def _paths(self):
        return [[self.StartPoint[:2], self.EndPoint[:2]]]


class _Circle(_Entity):
    ObjectName = "AcDbCircle"

    def __init__(self, doc, center, radius):
        super().__init__(doc)
        self.Center, self.Radius = center, radius

    @property
    def Area(self):
        return math.pi * self.Radius ** 2

    def _xform(self, f, angle, scale, mirror_theta):
        self.Center = f(self.Center)
        self.Radius *= scale

    def Offset(self, distance):
        if self.Radius + distance <= 0:
            raise com_error("偏移距离过大，半径将不为正")
        new = self.Copy()
        new.Radius += distance
        return (new,)

    def _paths(self):
        return [_sample_arc(self.Center, self.Radius, 0, 2 * math.pi)]


class _Arc(_Circle):
    ObjectName = "AcDbArc"

    def __init__(self, doc, center, radius, start, end):
        super().__init__(doc, center, radius)
        self.StartAngle, self.EndAngle = start, end

    def _xform(self, f, angle, scale, mirror_theta):
        super()._xform(f, angle, scale, mirror_theta)
        if mirror_theta is not None:
            # 镜像后方向反转：起止角互换
            self.StartAngle, self.EndAngle = 2 * mirror_theta - self.EndAngle, 2 * mirror_theta - self.StartAngle
        else:
            self.StartAngle += angle
            self.EndAngle += angle
        self.StartAngle %= 2 * math.pi
        self.EndAngle %= 2 * math.pi

    def _paths(self):
        return [_sample_arc(self.Center, self.Radius, self.StartAngle, self.EndAngle)]


class _Ellipse(_Entity):
    ObjectName = "AcDbEllipse"

    def __init__(self, doc, center, major, ratio):
        super().__init__(doc)
        self.Center, self.MajorAxis, self.RadiusRatio = center, major, ratio
        self.StartParam, self.EndParam = 0.0, 2 * math.pi

    def _xform(self, f, angle, scale, mirror_theta):
        tip = f(_add(self.Center, self.MajorAxis))
        self.Center = f(self.Center)
        self.MajorAxis = _sub(tip, self.Center)

    def _paths(self):
        cx, cy = self.Center[:2]
        ax, ay = self.MajorAxis[:2]
        bx, by = -ay * self.RadiusRatio, ax * self.RadiusRatio
        return [[(cx + ax * math.cos(t) + bx * math.sin(t), cy + ay * math.cos(t) + by * math.sin(t))
                 for t in (2 * math.pi * i / 72 for i in range(73))]]


class _Polyline(_Entity):
    # AddPolyline 在AutoCAD中创建旧式2D多段线，Coordinates为3分量
    ObjectName = "AcDb2dPolyline"

    def __init__(self, doc, pts):
        super().__init__(doc)
        self._pts = pts
        self.Closed = False

    @property
    def Coordinates(self):
        return tuple(c for p in self._pts for c in p)

    @property
    def Length(self):
        pts = self._pts + (self._pts[:1] if self.Closed else [])
        return sum(_dist(a, b) for a, b in zip(pts, pts[1:]))

    def _xform(self, f, angle, scale, mirror_theta):
        self._pts = [f(p) for p in self._pts]

    def _paths(self):
        pts = [p[:2] for p in self._pts]
        return [pts + (pts[:1] if self.Closed else [])]


class _Text(_Entity):
    ObjectName = "AcDbText"

    def __init__(self, doc, text, pos, height):
        super().__init__(doc)
        self.TextString, self.InsertionPoint, self.Height = text, pos, height
        self.Rotation = 0.0

    def _xform(self, f, angle, scale, mirror_theta):
        # 镜像时保持文字可读（相当于 MIRRTEXT=0）
        self.InsertionPoint = f(self.InsertionPoint)
        self.Height *= scale
        if mirror_theta is None:
            self.Rotation += angle

    def _label(self):
        return self.InsertionPoint, self.TextString, self.Height


class _Hatch(_Entity):
    ObjectName = "AcDbHatch"

    def __init__(self, doc, pattern):
        super().__init__(doc)
        self.PatternName = pattern
        self.PatternScale = 1.0
        self._loop: List[tuple] = []

    def AppendOuterLoop(self, objects):
        for obj in _vals(objects):
            if hasattr(obj, "_pts"):
                self._loop = list(obj._pts)

    def Evaluate(self):
        pass

    @property
    def Area(self):
        return _shoelace(self._loop) if len(self._loop) >= 3 else 0.0

    def _xform(self, f, angle, scale, mirror_theta):
        self._loop = [f(p) for p in self._loop]

    def _paths(self):
        return [[p[:2] for p in self._loop]] if self._loop else []


class _Dimension(_Entity):
    ObjectName = "AcDbAlignedDimension"

    def __init__(self, doc, p1, p2, text_pos):
        super().__init__(doc)
        self.ExtLine1Point, self.ExtLine2Point, self.TextPosition = p1, p2, text_pos
        self.TextHeight = 2.5

    @property
    def Measurement(self):
        return _dist(self.ExtLine1Point, self.ExtLine2Point)

    def _xform(self, f, angle, scale, mirror_theta):
        self.ExtLine1Point = f(self.ExtLine1Point)
        self.ExtLine2Point = f(self.ExtLine2Point)
        self.TextPosition = f(self.TextPosition)

    def _paths(self):
        return [[self.ExtLine1Point[:2], self.ExtLine2Point[:2]]]

    def _label(self):
        return self.TextPosition, f"{self.Measurement:.2f}", self.TextHeight


class _BlockRef(_Entity):
    ObjectName = "AcDbBlockReference"

    def __init__(self, doc, name, pos, xs, ys, zs, rot):
        super().__init__(doc)
        self.Name, self.InsertionPoint = name, pos
        self.XScaleFactor, self.YScaleFactor, self.ZScaleFactor = xs, ys, zs
        self.Rotation = rot

    def _xform(self, f, angle, scale, mirror_theta):
        self.InsertionPoint = f(self.InsertionPoint)
        self.XScaleFactor *= scale
        self.YScaleFactor *= scale
        self.ZScaleFactor *= scale
        if mirror_theta is not None:
            self.XScaleFactor = -self.XScaleFactor
            self.Rotation = 2 * mirror_theta - self.Rotation
        else:
            self.Rotation += angle

    def _paths(self):
        x, y = self.InsertionPoint[:2]
        return [[(x - 1, y), (x + 1, y)], [(x, y - 1), (x, y + 1)]]

    def _label(self):
        return self.InsertionPoint, f"[{self.Name}]", 2.5


_ENTITY_TYPES = {cls.__name__: cls for cls in
                 (_Line, _Circle, _Arc, _Ellipse, _Polyline, _Text, _Hatch, _Dimension, _BlockRef)}


# ==================== 图层 / 图块 / 集合 ====================

class _Layer:
    def __init__(self, name):
        self.Name = name
        self.color = 7
        self.Freeze = False
        self.Lock = False
        self.LayerOn = True


class _Block:
    def __init__(self, name, origin):
        self.Name = name
        self.Origin = origin
        self.Count = 0  # 假后端不支持向图块定义内添加实体


class _Collection:
    """Count/Item 只读视图（Item 接受索引或名称）"""

    def __init__(self, items):
        self._items = items

    @property
    def Count(self):
        return len(self._items)

    def Item(self, key):
        if isinstance(key, int):
            return self._items[key]
        for item in self._items:
            if item.Name.casefold() == str(key).casefold():
                return item
        raise com_error(f"未找到: {key}")


class _Layers(_Collection):
    def Add(self, name):
        for layer in self._items:
            if layer.Name.casefold() == name.casefold():
                return layer
        layer = _Layer(name)
        self._items.append(layer)
        return layer


class _Blocks(_Collection):
    def Add(self, origin, name):
        block = _Block(name, _p3(origin))
        self._items.append(block)
        return block


class _ModelSpace(_Collection):
    def __init__(self, doc):
        super().__init__(doc.state["entities"])
        self._doc = doc

    def _add(self, entity):
        self._items.append(entity)
        return entity

    def AddLine(self, start, end):
        return self._add(_Line(self._doc, _p3(start), _p3(end)))

    def AddCircle(self, center, radius):
        if radius <= 0:
            raise com_error("半径必须为正数")
        return self._add(_Circle(self._doc, _p3(center), float(radius)))

    def AddArc(self, center, radius, start, end):
        if radius <= 0:
            raise com_error("半径必须为正数")
        return self._add(_Arc(self._doc, _p3(center), float(radius), float(start), float(end)))

    def AddEllipse(self, center, major, ratio):
        if not 0 < ratio <= 1:
            raise com_error("RadiusRatio 必须在 (0, 1] 区间")
        return self._add(_Ellipse(self._doc, _p3(center), _p3(major), float(ratio)))

    def AddPolyline(self, coords):
        flat = [float(c) for c in _vals(coords)]
        pts = [tuple(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]
        if len(pts) < 2:
            raise com_error("多段线至少需要两个点")
        return self._add(_Polyline(self._doc, pts))

    def AddText(self, text, pos, height):
        return self._add(_Text(self._doc, str(text), _p3(pos), float(height)))

    def AddHatch(self, pattern_type, pattern_name, associative):
        return self._add(_Hatch(self._doc, str(pattern_name)))

    def AddDimAligned(self, p1, p2, text_pos):
        return self._add(_Dimension(self._doc, _p3(p1), _p3(p2), _p3(text_pos)))

    def InsertBlock(self, pos, name, xs, ys, zs, rot):
        if not any(b.Name.casefold() == str(name).casefold() for b in self._doc.state["blocks"]):
            raise com_error(f"图块不存在: {name}")
        return self._add(_BlockRef(self._doc, str(name), _p3(pos), float(xs), float(ys), float(zs), float(rot)))


# ==================== 文档 / 应用 ====================

def _new_state() -> Dict[str, Any]:
    return {
        "entities": [],
        "layers": [_Layer("0")],
        "blocks": [_Block("*Model_Space", (0, 0, 0)), _Block("*Paper_Space", (0, 0, 0))],
        "active_layer": "0",
    }


class Document:
    def __init__(self, app, name):
        self._app = app
        self.Name = name
        self.FullName = ""
        self.state = _new_state()
        self._next_handle = 0x100   # 句柄不随撤销回退，与真实CAD一致（永不复用）
        self._undo: List[dict] = []
        self._redo: List[dict] = []
        self._undo_depth = 0

    def _new_handle(self) -> str:
        self._next_handle += 1
        return format(self._next_handle, "X")

    def _snapshot(self) -> dict:
        return copy.deepcopy(self.state, {id(self): self})

    # --- 集合与属性 ---
    @property
    def ModelSpace(self):
        return _ModelSpace(self)

    @property
    def Layers(self):
        return _Layers(self.state["layers"])

    @property
    def Blocks(self):
        return _Blocks(self.state["blocks"])

    @property
    def ActiveLayer(self):
        return self.Layers.Item(self.state["active_layer"])

    @ActiveLayer.setter
    def ActiveLayer(self, layer):
        self.state["active_layer"] = layer.Name if isinstance(layer, _Layer) else str(layer)

    def HandleToObject(self, handle):
        for e in self.state["entities"]:
            if e.Handle.casefold() == str(handle).casefold():
                return e
        raise com_error(f"无效句柄: {handle}")

    # --- 撤销 ---
    def StartUndoMark(self):
        # 只在最外层标记处拍快照，嵌套组归入外层（与AutoCAD一致）
        if self._undo_depth == 0:
            self._undo.append(self._snapshot())
            self._redo.clear()
        self._undo_depth += 1

    def EndUndoMark(self):
        self._undo_depth = max(0, self._undo_depth - 1)

    def _undo_steps(self, n: int):
        for _ in range(n):
            if not self._undo:
                break
            self._redo.append(self._snapshot())
            self.state = self._undo.pop()

    def _redo_step(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self.state = self._redo.pop()

    def SendCommand(self, command: str):
        tokens = command.strip().split()
        name = tokens[0].lstrip("_.").upper() if tokens else ""
        if name in ("UNDO", "U"):
            arg = tokens[1] if len(tokens) > 1 else "1"
            self._undo_steps(int(arg) if arg.isdigit() else 1)
        elif name == "REDO":
            self._redo_step()
        elif name in ("ZOOM", "REGEN", "REGENALL", ""):
            pass
        else:
            logger.info(f"假后端不执行命令，仅记录: {command.strip()!r}")

    def Regen(self, which=1):
        pass

    # --- 文件 ---
    def SaveAs(self, path):
        data = {
            "format": _FORMAT_TAG,
            "active_layer": self.state["active_layer"],
            "layers": [vars(layer) for layer in self.state["layers"]],
            "blocks": [vars(block) for block in self.state["blocks"]],
            "entities": [{"type": type(e).__name__, **{k: v for k, v in vars(e).items() if k != "_doc"}}
                         for e in self.state["entities"]],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        self.FullName = path
        self.Name = os.path.basename(path)

    def _load(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("format") != _FORMAT_TAG:
            raise ValueError("不是假后端保存的图纸")
        state = _new_state()
        state["active_layer"] = data["active_layer"]
        state["layers"] = [self._restore(_Layer, d) for d in data["layers"]]
        state["blocks"] = [self._restore(_Block, d) for d in data["blocks"]]
        for d in data["entities"]:
            cls = _ENTITY_TYPES.get(d.pop("type", None))
            if cls is None:
                continue
            entity = self._restore(cls, d)
            entity._doc = self
            state["entities"].append(entity)
            self._next_handle = max(self._next_handle, int(entity.Handle, 16))
        self.state = state

    @staticmethod
    def _restore(cls, d):
        obj = cls.__new__(cls)
        obj.__dict__.update(d)
        return obj

    def Close(self, save=False):
        if save:
            self.SaveAs(self.FullName or os.path.abspath(self.Name))
        self._app._docs.remove(self)


class _Documents:
    def __init__(self, app):
        self._app = app

    @property
    def Count(self):
        return len(self._app._docs)

    def Item(self, i):
        return self._app._docs[i]

    def Add(self, template=None):
        self._app._counter += 1
        doc = Document(self._app, f"Drawing{self._app._counter}.dwg")
        self._app._docs.append(doc)
        return doc

    def Open(self, path):
        doc = Document(self._app, os.path.basename(path))
        doc.FullName = path
        try:
            doc._load(path)
        except Exception as e:
            logger.warning(f"假后端无法解析 {path}（{e}），以空白图纸打开")
        self._app._docs.append(doc)
        return doc


class Application:
    def __init__(self):
        self._docs: List[Document] = []
        self._counter = 0
        self.Visible = True
        self.HWND = 0
        self.Documents.Add()  # 与真实CAD一样，启动时带一个空白图纸

    @property
    def Documents(self):
        return _Documents(self)

    @property
    def ActiveDocument(self):
        if not self._docs:
            raise com_error("没有活动文档")
        return self._docs[-1]

    def ZoomExtents(self):
        pass


# ==================== 截图渲染 ====================

_ACI_RGB = {1: (255, 0, 0), 2: (255, 255, 0), 3: (0, 255, 0), 4: (0, 255, 255),
            5: (0, 0, 255), 6: (255, 0, 255), 7: (255, 255, 255), 8: (128, 128, 128), 9: (192, 192, 192)}


def render_png(doc: Document, size=(1024, 768)) -> bytes:
    """把当前图纸渲染为PNG示意图（替代真实CAD窗口截图）"""
    from PIL import Image, ImageDraw

    width, height = size
    img = Image.new("RGB", size, (33, 40, 48))
    draw = ImageDraw.Draw(img)
    layer_colors = {layer.Name: layer.color for layer in doc.state["layers"]}
    hidden = {layer.Name for layer in doc.state["layers"] if not layer.LayerOn or layer.Freeze}
    entities = [e for e in doc.state["entities"] if e.Layer not in hidden]

    pts = [p for e in entities for path in e._paths() for p in path]
    pts += [lbl[0][:2] for e in entities if (lbl := e._label())]
    if pts:
        min_x, max_x = min(p[0] for p in pts), max(p[0] for p in pts)
        min_y, max_y = min(p[1] for p in pts), max(p[1] for p in pts)
    else:
        min_x, max_x, min_y, max_y = 0, 100, 0, 100
    margin = 40
    k = min((width - 2 * margin) / max(max_x - min_x, 1e-9),
            (height - 2 * margin) / max(max_y - min_y, 1e-9))
    ox = (width - (max_x - min_x) * k) / 2
    oy = (height - (max_y - min_y) * k) / 2

    def px(p):
        return (ox + (p[0] - min_x) * k, height - (oy + (p[1] - min_y) * k))

    for e in entities:
        aci = layer_colors.get(e.Layer, 7) if e.color in (256, None) else e.color
        rgb = _ACI_RGB.get(aci, (170, 170, 170))
        for path in e._paths():
            screen = [px(p) for p in path]
            if isinstance(e, _Hatch) and len(screen) >= 3:
                draw.polygon(screen, fill=tuple(c // 2 for c in rgb), outline=rgb)
            elif len(screen) >= 2:
                draw.line(screen, fill=rgb, width=2)
        lbl = e._label()
        if lbl:
            try:
                draw.text(px(lbl[0][:2]), lbl[1], fill=rgb)
            except Exception:
                # 默认字体不含中文等字符时无法绘制，跳过文字（几何仍然渲染）
                pass

    try:
        draw.text((8, 8), f"{doc.Name} - fake CAD preview ({len(entities)} entities)", fill=(200, 200, 200))
    except Exception:
        pass
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
