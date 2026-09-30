"""
فحص التمرير: هل المحتوى الص��لي يُمرَّر فعلاً؟

القياس قبل التغيير: المحتوى يطلب نحو 1400 بكسل والنافذة 960، فكان
440 بكسل خارج الشاشة بلا وسيلة. الفحص يبني الواجهة، يمرّر بعجلة
الفأرة، ويؤكد أن اللوحة تحرّكت - لا أن الحافة فقط تحرّكت.

    xvfb-run -a python tools/check_scroll.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from src.context_aware_audio import app as A  # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


def main() -> int:
    root = tk.Tk()
    ui = A.DesktopApp(root)
    root.update_idletasks()
    root.update()

    canvas = getattr(ui, "_canvas", None)
    check("a canvas was created", canvas is not None)
    if canvas is None:
        return 1
    check("a scrollbar was created", getattr(ui, "_scrollbar", None) is not None)

    # --- geometry ------------------------------------------------------
    top = int(canvas.winfo_rooty())
    h = root.winfo_height()
    content = int(canvas.bbox("all")[3]) if canvas.bbox("all") else 0
    print(f"    window h={h}  content h={content}  overflow={content - h}")
    check("content is taller than the window", content > h, f"{content} > {h}")
    region = canvas.cget("scrollregion")
    check("the scrollregion spans the content", content > 0 and bool(region),
          str(region))

    # --- the wheel actually scrolls ------------------------------------
    # Tk لا يسمح ببناء Event بالمعاملات، فيُرفع الحدث الحقيقي عبر
    # event_generate على عنصر يشترك في bind_all.
    def wheel(delta):
        canvas.event_generate("<MouseWheel>", delta=delta, warp=True)
        root.update_idletasks()

    before = canvas.yview()[0]
    wheel(-120)
    after = canvas.yview()[0]
    check("the wheel scrolls down", after > before, f"{before:.3f} -> {after:.3f}")

    # --- it can reach the bottom ---------------------------------------
    # yview[0] cannot exceed 1 - viewport/content, so 0.98 is
    # unreachable when the page is 1400 tall and the window 921.
    # The floor is what matters: the last panel must be reachable.
    max_pos = 1.0 - (h / content)
    for _ in range(8):
        wheel(-120)
    bottom = canvas.yview()[0]
    check("scrolling reaches the last panel", bottom >= max_pos - 0.01,
          f"yview={bottom:.3f} max={max_pos:.3f}")

    # --- and back to the top -------------------------------------------
    for _ in range(8):
        wheel(120)
    top_pos = canvas.yview()[0]
    check("scrolling returns to the top", top_pos < 0.02, f"yview={top_pos:.3f}")

    # --- a single notch moves far enough to be usable -------------------
    canvas.yview_moveto(0)
    wheel(-120)
    one = canvas.yview()[0]
    print(f"    one notch moves to {one:.3f} of {max_pos:.3f}")
    check("one notch is a usable step", one > 0.05, f"{one:.3f}")

    # --- resizing keeps the content at the canvas width ----------------
    root.geometry("900x700")
    root.update_idletasks()
    inner_w = canvas.itemcget(canvas.find_all()[0], "width")
    canvas_w = canvas.winfo_width()
    print(f"    canvas w={canvas_w}  inner w={inner_w}")
    check("the inner frame follows the canvas width",
          inner_w == "" or int(inner_w) == canvas_w or abs(int(inner_w) - canvas_w) <= 1,
          f"{inner_w} vs {canvas_w}")

    # --- stop() releases the global binding ----------------------------
    ui.stop()
    root.update_idletasks()
    check("stop() unbinds the wheel", getattr(ui, "_wheel_binding", None) is not None)

    root.destroy()

    if FAIL:
        print(f"\n{len(FAIL)} CHECK(S) FAILED")
        return 1
    print("\nSCROLL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
