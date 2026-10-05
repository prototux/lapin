"""
Ring animations. Design rules: few colors from one palette, soft gaussian
light (no hard pixel edges), everything eased, states crossfade into each
other, and motion carries meaning: light faces the talker while listening,
orbits while thinking, breathes with the voice while speaking.

Positions are in LED units (float, wrapping), LED 0 at the top (0 degrees),
clockwise. Colors are perceptual (r, g, b) in 0..1.
"""

import math

N = 12
TAU = 2 * math.pi

PALETTES = {
    "aurora": [(0.00, 0.80, 1.00), (0.20, 0.35, 1.00), (0.62, 0.22, 1.00), (1.00, 0.30, 0.62)],
    "google": [(0.26, 0.52, 0.96), (0.92, 0.26, 0.21), (0.98, 0.74, 0.02), (0.20, 0.66, 0.33)],
    "ocean": [(0.00, 0.95, 0.80), (0.00, 0.65, 1.00), (0.10, 0.30, 1.00), (0.35, 0.85, 1.00)],
    "sunset": [(1.00, 0.45, 0.10), (1.00, 0.20, 0.40), (0.70, 0.15, 0.85), (1.00, 0.70, 0.20)],
    "ember": [(1.00, 0.35, 0.05), (1.00, 0.12, 0.05), (1.00, 0.60, 0.15), (0.85, 0.20, 0.10)],
}

BLACK = (0.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)
RED = (1.0, 0.07, 0.04)
AMBER = (1.0, 0.50, 0.04)
WARM = (1.0, 0.38, 0.04)
GREEN = (0.10, 1.0, 0.45)


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def smooth(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def mix(a, b, k):
    return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k)


def scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def cdist(a, b):
    d = abs(a - b) % N
    return min(d, N - d)


def signed(a, b):
    """a - b along the ring, in (-N/2, N/2]."""
    d = (a - b) % N
    return d - N if d > N / 2 else d


def gauss(d, sigma):
    return math.exp(-0.5 * (d / sigma) ** 2)


def pal_at(pal, x):
    """Cyclic gradient through the palette, x in [0, 1)."""
    x = (x % 1.0) * len(pal)
    i = int(x)
    return mix(pal[i % len(pal)], pal[(i + 1) % len(pal)], smooth(x - i))


def blank():
    return [BLACK] * N


class Ctx:
    """Inputs shared by all animations, updated by the plugin."""

    def __init__(self):
        self.pal = PALETTES["aurora"]
        self.doa = 0.0          # talker direction, LED units (smoothed)
        self.level = 0.0        # voice level 0..1 (smoothed)
        self.out = 0.0          # playback level 0..1 (smoothed)
        self.volume = 60
        self.idle_mode = "off"
        self.top = 0.0          # LED position used as "top" for level displays


# ---------------------------------------------------------------- base states

def idle(t, c):
    if c.idle_mode == "ambient":
        return [scale(pal_at(c.pal, i / N + t * 0.02), 0.07 + 0.02 * math.sin(TAU * (t * 0.1 + i / N)))
                for i in range(N)]
    return blank()


def listening(t, c):
    """A soft lobe of light facing the talker. It breathes when nobody
    speaks, widens and brightens with the voice, and two small sparks drift
    along its edges."""
    breathe = 0.5 + 0.5 * math.sin(TAU * t * 0.35)
    sigma = 1.05 + 0.30 * breathe + 0.9 * c.level
    amp = 0.50 + 0.50 * c.level
    p0, p1 = c.pal[0], c.pal[1]
    px = []
    for i in range(N):
        d = cdist(i, c.doa)
        w = gauss(d, sigma)
        col = scale(mix(p0, p1, clamp(d / (2.6 * sigma))), amp * w)
        glow = scale(pal_at(c.pal, i / N + t * 0.04), 0.045 * (1 - w))
        px.append(add(col, glow))
    for side in (-1, 1):
        pos = c.doa + side * (sigma * 1.9 + 0.5 * math.sin(TAU * t * 0.45 + side))
        for i in range(N):
            k = gauss(cdist(i, pos), 0.45) * (0.20 + 0.15 * breathe)
            px[i] = add(px[i], scale(c.pal[2], k))
    return px


def thinking(t, c):
    """Four orbs from the palette chasing each other with eased, varying
    speed; they draw together and spread apart as they go."""
    phase = t * 0.62 + 0.16 * math.sin(TAU * t * 0.42)
    spread = 1.0 + 0.32 * math.sin(TAU * t * 0.55)
    px = blank()
    for k in range(4):
        pos = N * phase + (k - 1.5) * (N / 4) * spread
        col = c.pal[k % len(c.pal)]
        for i in range(N):
            d = signed(i, pos)
            w = gauss(d, 0.55)
            if d < 0:                       # tail behind the orb
                w = max(w, 0.55 * math.exp(d / 1.1))
            px[i] = add(px[i], scale(col, 0.85 * w))
    return px


def speaking(t, c):
    """The palette slowly turns around the ring and the whole ring breathes
    with the voice, a little brighter toward the listener."""
    e = c.out
    px = []
    for i in range(N):
        col = pal_at(c.pal, i / N + t * 0.11)
        face = gauss(cdist(i, c.doa), 2.4)
        amp = 0.16 + 0.84 * e * (0.62 + 0.38 * face) + 0.04 * math.sin(TAU * (t * 0.6 + i / N))
        px.append(scale(col, clamp(amp)))
    return px


def muted(t, c):
    k = 0.20 + 0.05 * math.sin(TAU * t / 4.0)
    return [scale(RED, k) for _ in range(N)]


def offline(t, c):
    """One amber spark circling slowly, easing in and out of each turn."""
    f = (t / 3.2) % 1.0
    pos = N * (f + 0.5 * (smooth(f) - f))
    px = blank()
    for i in range(N):
        d = signed(i, pos)
        w = gauss(d, 0.6) if d >= 0 else max(gauss(d, 0.6), 0.6 * math.exp(d / 1.4))
        px[i] = scale(AMBER, 0.45 * w)
    return px


def pending(t, c):
    """Waiting for the server to approve this device: two blue dots."""
    px = blank()
    k = 0.35 + 0.15 * math.sin(TAU * t * 0.5)
    for j in (0, 1):
        pos = N * (t * 0.22 + j * 0.5)
        for i in range(N):
            px[i] = add(px[i], scale(c.pal[1], k * gauss(cdist(i, pos), 0.7)))
    return px


def alarm(t, c):
    pulse = 0.5 + 0.5 * math.sin(TAU * t * 1.3)
    hi = N * t * 0.7
    return [add(scale(WARM, 0.25 + 0.55 * pulse),
                scale(WHITE, 0.35 * gauss(cdist(i, hi), 0.9))) for i in range(N)]


def notify(t, c):
    k = 0.15 + 0.45 * (0.5 + 0.5 * math.sin(TAU * t * 0.5))
    return [scale(c.pal[0], k * gauss(cdist(i, c.top), 1.8)) for i in range(N)]


BASES = {"idle": idle, "listening": listening, "thinking": thinking, "speaking": speaking,
         "muted": muted, "offline": offline, "pending": pending, "alarm": alarm, "notify": notify}


# ------------------------------------------------------------------ overlays
# Each returns (frame, alpha, mode) for time t since it started, or None when
# finished. mode "add" adds light on top, "over" crossfades by alpha.

def ov_wake(t, c, arg):
    """A flash where the talker is, rippling around the ring both ways."""
    if t > 0.8:
        return None
    front = (N / 2) * ease_out(t / 0.6)
    fade = 1 - smooth(t / 0.8)
    flash = max(0.0, 1 - t / 0.3)
    px = []
    for i in range(N):
        d = cdist(i, c.doa)
        wave = gauss(d - front, 0.75) * fade
        col = mix(WHITE, pal_at(c.pal, d / N), 0.35 + 0.65 * (d / (N / 2)))
        px.append(add(scale(col, 0.9 * wave), scale(WHITE, 0.8 * flash * gauss(d, 0.9))))
    return px, 1.0, "add"


def ov_volume(t, c, arg):
    """The volume as an arc from the top, a bright tip on its end."""
    if t > 2.0:
        return None
    v = clamp((arg if arg is not None else c.volume) / 100)
    alpha = 1 - smooth((t - 1.5) / 0.5)
    fill = v * N
    grow = ease_out(t / 0.25)
    px = []
    for i in range(N):
        pos = (i - c.top) % N
        k = clamp(fill * grow - pos)
        col = scale(mix(c.pal[1], c.pal[0], pos / N), 0.18 + 0.5 * k)
        if k <= 0:
            col = scale(c.pal[2], 0.04)
        tip = gauss(pos - (fill * grow - 0.5), 0.5) * 0.6
        px.append(add(col, scale(WHITE, tip if v > 0 else 0)))
    return px, alpha, "over"


def ov_mute(t, c, arg):
    """Muting: red sweeps around; unmuting: the red drains away."""
    if t > 0.7:
        return None
    on = bool(arg)
    f = ease_out(t / 0.5)
    px = []
    for i in range(N):
        pos = ((i - c.top) % N) / N
        lit = pos <= f if on else pos > f
        px.append(scale(RED, 0.6 if lit else 0.0))
    alpha = 1 - smooth((t - 0.5) / 0.2)
    return px, alpha, "over"


def ov_error(t, c, arg):
    if t > 0.8:
        return None
    a = max(0.0, math.sin(math.pi * t / 0.35)) if t < 0.35 else \
        max(0.0, math.sin(math.pi * (t - 0.42) / 0.35)) if t > 0.42 else 0.0
    return [scale(RED, 0.7) for _ in range(N)], a, "over"


def ov_success(t, c, arg):
    if t > 0.9:
        return None
    a = math.sin(math.pi * t / 0.9)
    return [scale(GREEN, 0.55) for _ in range(N)], a, "add"


def ov_boot(t, c, arg):
    """Light fills the ring through the palette, then lets go."""
    if t > 2.2:
        return None
    lit = ease_out(t / 1.1) * N
    px = []
    for i in range(N):
        pos = (i - c.top) % N
        k = clamp(lit - pos)
        edge = gauss(pos - lit, 0.7) * (1 - smooth(t / 1.2))
        px.append(add(scale(pal_at(c.pal, pos / N), 0.55 * k), scale(WHITE, 0.6 * edge)))
    alpha = 1 - smooth((t - 1.4) / 0.8)
    return px, alpha, "over"


OVERLAYS = {"wake": ov_wake, "volume": ov_volume, "mute": ov_mute, "error": ov_error,
            "success": ov_success, "boot": ov_boot}


class Animator:
    """Base state with crossfades, plus transient overlays."""

    FADE = 0.4

    def __init__(self, ctx):
        self.ctx = ctx
        self.base, self.base_t = "idle", 0.0
        self.prev, self.prev_t, self.fade = None, 0.0, 1.0
        self.overlays = []      # [name, t, arg]

    def set_base(self, name):
        if name == self.base or name not in BASES:
            return
        self.prev, self.prev_t = self.base, self.base_t
        self.base, self.base_t, self.fade = name, 0.0, 0.0

    def overlay(self, name, arg=None):
        if name not in OVERLAYS:
            return
        self.overlays = [o for o in self.overlays if o[0] != name]
        self.overlays.append([name, 0.0, arg])

    def render(self, dt):
        c = self.ctx
        self.base_t += dt
        frame = BASES[self.base](self.base_t, c)
        if self.fade < 1.0 and self.prev:
            self.prev_t += dt
            self.fade = min(1.0, self.fade + dt / self.FADE)
            old = BASES[self.prev](self.prev_t, c)
            k = smooth(self.fade)
            frame = [mix(o, n, k) for o, n in zip(old, frame)]
        keep = []
        for ov in self.overlays:
            ov[1] += dt
            r = OVERLAYS[ov[0]](ov[1], c, ov[2])
            if r is None:
                continue
            keep.append(ov)
            px, alpha, mode = r
            if mode == "add":
                frame = [add(f, scale(p, alpha)) for f, p in zip(frame, px)]
            else:
                frame = [mix(f, p, alpha) for f, p in zip(frame, px)]
        self.overlays = keep
        return [(clamp(r), clamp(g), clamp(b)) for r, g, b in frame]
