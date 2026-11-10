"""Geometria pura (sem Qt) para snap e posicionamento do HUD."""

Rect = tuple[int, int, int, int]  # x, y, w, h


def _snap_axis(pos: int, size: int, lo: int, hi: int, threshold: int) -> int:
    if abs(pos - lo) <= threshold:
        return lo
    if abs(pos + size - hi) <= threshold:
        return hi - size
    return pos


def snap_to_edges(win: Rect, area: Rect, threshold: int = 16) -> tuple[int, int]:
    """Encosta a janela nas bordas da area quando a distancia <= threshold."""
    x, y, w, h = win
    ax, ay, aw, ah = area
    return (
        _snap_axis(x, w, ax, ax + aw, threshold),
        _snap_axis(y, h, ay, ay + ah, threshold),
    )


def _intersection(a: Rect, b: Rect) -> int:
    ix = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    iy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return ix * iy if ix > 0 and iy > 0 else 0


def pick_screen(win: Rect, areas: list[Rect]) -> Rect:
    """Area que contem o centro da janela; senao maior intersecao; senao areas[0]."""
    cx = win[0] + win[2] // 2
    cy = win[1] + win[3] // 2
    for a in areas:
        if a[0] <= cx < a[0] + a[2] and a[1] <= cy < a[1] + a[3]:
            return a
    best = max(areas, key=lambda a: _intersection(win, a))
    if _intersection(win, best) > 0:
        return best
    return areas[0]


def anchor_resize(win: Rect, new_size: tuple[int, int], area: Rect) -> tuple[int, int]:
    """Nova posicao ao redimensionar, mantendo a borda do lado da area onde a janela esta."""
    x, y, w, h = win
    ax, ay, aw, ah = area
    new_w, new_h = new_size
    if x + w / 2 > ax + aw / 2:
        x = x + w - new_w
    if y + h / 2 > ay + ah / 2:
        y = y + h - new_h
    return (x, y)


def clamp_to_screens(
    pos: tuple[int, int] | None,
    size: tuple[int, int],
    areas: list[Rect],
    margin: int = 16,
) -> tuple[int, int]:
    """Posicao valida dentro de alguma area; invalida/None -> canto sup. dir. da primaria."""
    w, h = size
    primary = areas[0]
    default = (primary[0] + primary[2] - w - margin, primary[1] + margin)
    if pos is None:
        return default
    win = (pos[0], pos[1], w, h)
    area = max(areas, key=lambda a: _intersection(win, a))
    if _intersection(win, area) * 2 < w * h:
        return default
    ax, ay, aw, ah = area
    x = max(ax, min(pos[0], ax + aw - w))
    y = max(ay, min(pos[1], ay + ah - h))
    return (x, y)
