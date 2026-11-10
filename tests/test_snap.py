from neuralmeter.ui.snap import clamp_to_screens, pick_screen, snap_to_edges

AREA = (0, 0, 1920, 1040)
W, H = 260, 72


def test_snap_left_top():
    assert snap_to_edges((10, 12, W, H), AREA) == (0, 0)


def test_snap_right_bottom():
    assert snap_to_edges((1920 - W - 5, 1040 - H - 16, W, H), AREA) == (1920 - W, 1040 - H)


def test_no_snap_outside_threshold():
    assert snap_to_edges((17, 500, W, H), AREA) == (17, 500)


def test_snap_corner_two_axes():
    assert snap_to_edges((1910 - W, 1030 - H, W, H), AREA) == (1920 - W, 1040 - H)


def test_snap_offset_area():
    area = (1920, 0, 1920, 1040)
    assert snap_to_edges((1930, 1030 - H, W, H), area) == (1920, 1040 - H)


def test_snap_pulls_in_when_outside():
    assert snap_to_edges((-8, 3, W, H), AREA) == (0, 0)


def test_pick_screen_center_in_monitor2():
    a = [(0, 0, 1920, 1040), (1920, 0, 1920, 1040)]
    assert pick_screen((2000, 100, W, H), a) == a[1]


def test_pick_screen_fallbacks():
    a = [(0, 0, 1920, 1040), (1920, 0, 1920, 1040)]
    assert pick_screen((1900, 100, 100, H), a) == a[1]  # centro em x=1950
    assert pick_screen((-5000, -5000, W, H), a) == a[0]


def test_clamp_none_top_right():
    assert clamp_to_screens(None, (W, H), [AREA]) == (1920 - W - 16, 16)


def test_clamp_missing_monitor_goes_primary():
    assert clamp_to_screens((5000, 100), (W, H), [AREA]) == (1920 - W - 16, 16)


def test_clamp_valid_preserved():
    assert clamp_to_screens((300, 200), (W, H), [AREA]) == (300, 200)


def test_clamp_partially_outside_clamped():
    x, y = clamp_to_screens((1700, 980), (W, H), [AREA])
