"""Chart data for inline SVG rendering.

No chart library: a CDN is unreliable for our visitors and bundling one costs
more than these few shapes are worth. The views compute the geometry and the
templates draw it.
"""
from datetime import timedelta

from django.utils import timezone


def _nice_ceiling(value):
    """A round number a little above the largest value."""
    if value <= 0:
        return 1
    step = 10 ** (len(str(int(value))) - 1)
    return int((value // step + 1) * step)


def timeseries(rows, days, width=640, height=160, pad_top=8, pad_bottom=18):
    """Turn a daily series into an SVG path.

    Missing days are filled with zero so the line keeps its true shape.
    """
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)

    by_day = {}
    for row in rows:
        day = row['day']
        if hasattr(day, 'date'):
            day = day.date()
        by_day[day] = row['count']

    # Empty days must appear or the slope of the line lies.
    series = [
        {'day': start + timedelta(days=offset),
         'count': by_day.get(start + timedelta(days=offset), 0)}
        for offset in range(days)
    ]

    peak = _nice_ceiling(max((item['count'] for item in series), default=0))
    plot_height = height - pad_top - pad_bottom
    step = width / max(len(series) - 1, 1)

    points = []
    for index, item in enumerate(series):
        x = index * step
        y = pad_top + plot_height * (1 - item['count'] / peak)
        points.append({'x': round(x, 2), 'y': round(y, 2), **item})

    line = ' '.join(
        f'{"M" if i == 0 else "L"}{p["x"]},{p["y"]}' for i, p in enumerate(points)
    )
    area = (
        f'{line} L{points[-1]["x"]},{height - pad_bottom} '
        f'L{points[0]["x"]},{height - pad_bottom} Z'
    ) if points else ''

    # Horizontal guide lines.
    gridlines = [
        {'y': round(pad_top + plot_height * fraction, 2),
         'value': int(peak * (1 - fraction))}
        for fraction in (0, 0.5, 1)
    ]

    # Only a few date labels, or they overlap on a phone.
    label_every = max(len(points) // 5, 1)
    for index, point in enumerate(points):
        point['show_label'] = index % label_every == 0 or index == len(points) - 1

    return {
        'points': points,
        'line': line,
        'area': area,
        'gridlines': gridlines,
        'peak': peak,
        'width': width,
        'height': height,
        'total': sum(item['count'] for item in series),
        'has_data': any(item['count'] for item in series),
    }


def ring(value, total, size=120, stroke=10):
    """A ratio ring, returning circumference and arc length for
    stroke-dasharray.
    """
    radius = (size - stroke) / 2
    circumference = 2 * 3.141592653589793 * radius
    fraction = (value / total) if total else 0
    return {
        'size': size,
        'stroke': stroke,
        'radius': round(radius, 2),
        'center': size / 2,
        'circumference': round(circumference, 2),
        'dash': round(circumference * fraction, 2),
        'gap': round(circumference * (1 - fraction), 2),
        'percent': round(fraction * 100),
    }


def bars(items, height=120, max_bar=None):
    """A simple horizontal bar chart, scaled to the largest value."""
    peak = max_bar or max((item['value'] for item in items), default=0)
    for item in items:
        item['percent'] = round(item['value'] * 100 / peak) if peak else 0
    return items
