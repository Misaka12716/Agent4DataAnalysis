# viz/nature_style.py — Nature 期刊出图规范（配色 / 字号 / 尺寸）
"""
参考：
- Nature Final guide to authors（单栏 89mm / 双栏 180mm，Arial/Helvetica，
  正文字 ~5–7pt，分图标签 8pt bold a/b/c，RGB，≥300 dpi，pdf.fonttype=42）
- Okabe–Ito 色盲安全定性色板（顶刊常用，避免红绿对）
- Nature/Cell 风格低饱和定性色（社区教程常见）
- 连续色：viridis；发散色：RdBu_r（相关/火山），避免 jet/rainbow
"""

from __future__ import annotations

from pathlib import Path
from typing import List

# Okabe–Ito（色盲友好，Nature/Science 投稿社区首选）
OKABE_ITO: List[str] = [
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#56B4E9",  # sky blue
    "#D55E00",  # vermillion
    "#F0E442",  # yellow
    "#000000",  # black
]

# Nature/Cell 低饱和定性（柱状/箱线多组）
NATURE_MUTED: List[str] = [
    "#4E79A7",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
]

# 默认分类色：Okabe–Ito（更适合发表）
JOURNAL_COLORS = OKABE_ITO

# 火山图：非显著 / 显著（蓝–橙，非红绿）
VOLCANO_NS = "#56B4E9"
VOLCANO_SIG = "#D55E00"

# 连续 / 发散
CMAP_SEQUENTIAL = "viridis"
CMAP_DIVERGING = "RdBu_r"
CMAP_MISSING = "Greys"

# 英寸：89mm ≈ 3.50"；120mm ≈ 4.72"；180mm ≈ 7.09"
FIGSIZE_SINGLE = (3.5, 2.6)
FIGSIZE_SINGLE_SQUARE = (3.5, 3.5)
FIGSIZE_1_5COL = (4.72, 3.2)
FIGSIZE_DOUBLE = (7.09, 4.2)
FIGSIZE_HEAT = (4.72, 4.0)

def _cjk_font_names() -> List[str]:
    """Prefer installed CJK fonts so Chinese titles/labels render."""
    candidates = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Noto Serif CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "AR PL UMing CN",
        "AR PL UKai CN",
        "SimHei",
        "Microsoft YaHei",
        "PingFang SC",
    ]
    found: List[str] = []
    try:
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
        for name in candidates:
            if name in available:
                found.append(name)
        # also register common TTC/TTF paths if present but not yet named
        for path in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        ):
            if Path(path).is_file():
                try:
                    font_manager.fontManager.addfont(path)
                except Exception:
                    pass
                try:
                    # Prefer Simplified Chinese subfamily when TTC exposes it
                    for sub in ("Noto Sans CJK SC", "Noto Sans CJK JP", None):
                        prop = (
                            font_manager.FontProperties(fname=path, family=sub)
                            if sub
                            else font_manager.FontProperties(fname=path)
                        )
                        n = prop.get_name()
                        if n and n not in found:
                            found.insert(0, n)
                            break
                except Exception:
                    pass
        # Prefer SC names at front
        found.sort(key=lambda n: (0 if "SC" in n or "CN" in n or "Hei" in n else 1, n))
    except Exception:
        pass
    return found


NATURE_RC = {
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "savefig.facecolor": "white",
    "figure.facecolor": "white",
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "font.size": 7,
    "axes.titlesize": 8,
    "axes.labelsize": 7,
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
    "legend.fontsize": 6,
    "axes.linewidth": 0.6,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#222222",
    "axes.labelcolor": "#222222",
    "xtick.color": "#222222",
    "ytick.color": "#222222",
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "lines.linewidth": 1.0,
    "patch.linewidth": 0.5,
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
    "axes.prop_cycle": None,  # filled at apply time
}


def color_at(i: int, palette: List[str] | None = None) -> str:
    pal = palette or JOURNAL_COLORS
    return pal[i % len(pal)]


def apply_nature_style(plt) -> None:
    """Apply Nature-like rcParams to a pyplot module."""
    from cycler import cycler

    rc = dict(NATURE_RC)
    cjk = _cjk_font_names()
    # Put CJK fonts first so Chinese glyphs render; keep Arial for Latin when available.
    sans = list(cjk) + ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"]
    # de-dup preserve order
    seen = set()
    sans_unique = []
    for n in sans:
        if n not in seen:
            seen.add(n)
            sans_unique.append(n)
    rc["font.sans-serif"] = sans_unique
    rc["axes.unicode_minus"] = False
    rc["axes.prop_cycle"] = cycler(color=JOURNAL_COLORS)
    plt.rcParams.update(rc)


def polish_axes(ax) -> None:
    """Tighten Nature-like axes: no top/right spines, light grid off."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(False)
    ax.tick_params(direction="out", length=2.5, width=0.5)
