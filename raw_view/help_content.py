"""Help content: format explanation HTML + standalone keyboard shortcuts."""

# ── 快捷键（独立数据源，供 Help 菜单的 Keyboard Shortcuts 对话框展示）──
# (类别, [(按键, 说明), ...])；返回一个常量列表，测试直接引用它做文案校验，
# 不再依赖 HELP_HTML 里的 HTML 小节（0.4.1 把快捷键从 Format Help 独立出来）。
SHORTCUTS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Decode",
        [
            ("Ctrl+A", "apply current decode parameters"),
            ("Ctrl+N", "pin / unpin decode parameters"),
        ],
    ),
    (
        "Frame navigation",
        [
            ("Up / Left", "previous frame"),
            ("Down / Right", "next frame"),
            ("Home", "first frame"),
            ("End", "last frame"),
        ],
    ),
    (
        "File navigation",
        [
            ("Ctrl+Left", "previous file in the same folder"),
            ("Ctrl+Right", "next file in the same folder"),
        ],
    ),
    (
        "Zoom",
        [
            ("Ctrl++", "Zoom In"),
            ("Ctrl+-", "Zoom Out"),
            ("Ctrl+0", "fit to window"),
            ("Ctrl+1", "actual size (100%)"),
        ],
    ),
    (
        "View",
        [
            ("F11", "toggle fullscreen"),
            ("Escape", "exit fullscreen"),
        ],
    ),
    (
        "Transform",
        [
            ("Ctrl+R", "rotate clockwise"),
            ("Ctrl+Shift+R", "rotate counter-clockwise"),
            ("Ctrl+H", "flip horizontally"),
            ("Ctrl+Shift+V", "flip vertically"),
        ],
    ),
]

HELP_HTML = """
<h2>RAW/YUV Format Help</h2>
<h3>RAW aligned (LSB/MSB)</h3>
<ul>
<li>LSB aligned: valid n-bit data in low bits (e.g. RAW10 uses bit[9:0]).</li>
<li>MSB aligned: valid n-bit data in high bits (e.g. RAW10 uses bit[15:6]).</li>
</ul>
<h3>RAW8 Example (W=4,H=1)</h3>
<p>P0=0x0A, P1=0x14, P2=0x1E, P3=0x28 &rarr; bytes: <code>0A 14 1E 28</code></p>
<h3>RAW10 (16-bit aligned) Example</h3>
<ul>
<li>P0=0x001, P1=0x155</li>
<li>LSB: P0=0x0001 (&quot;01 00&quot;), P1=0x0155 (&quot;55 01&quot;)</li>
<li>MSB: P0=0x0040 (&quot;40 00&quot;), P1=0x5540 (&quot;40 55&quot; little-endian)</li>
</ul>
<h3>RAW12 Packed (2 pixels -&gt; 3 bytes)</h3>
<p>B0=P0[7:0], B1=(P0[11:8])|(P1[3:0]&lt;&lt;4), B2=P1[11:4]</p>
<h3>RAW10 Packed (MIPI)</h3>
<p>B0=P0[7:0], B1=P1[7:0], B2=P2[7:0], B3=P3[7:0], B4=P0[9:8]|(P1[9:8]&lt;&lt;2)|(P2[9:8]&lt;&lt;4)|(P3[9:8]&lt;&lt;6)</p>
<h3>YUV420 I420 (W=4,H=2)</h3>
<p>Layout: Y(8 bytes) + U(2 bytes) + V(2 bytes). Each U/V sample covers one 2x2 luma block.</p>
<h3>YUV Subformats</h3>
<ul>
<li>400 (YOnly): full-resolution grayscale, no chroma (any width/height).
    YOnly is a single format; pick a bit depth (8/10/12/14/16) in the
    Bit depth field — 8-bit = 1 byte/pixel, 10/12/14/16 = 16-bit storage
    (2 bytes/pixel, LSB/MSB alignment + little/big endian, same semantics
    as RAW10/12/16).</li>
<li>420: I420, YV12, NV12, NV21</li>
<li>422: YUYV, UYVY, YVYU, VYUY, NV16, NV61</li>
</ul>
<h3>Image Conversion Rules</h3>
<p>PNG/JPEG/BMP are loaded as BGR and optionally resized.</p>
<ul>
<li>RAW conversion supports Bayer (RGGB/GRBG/GBRG/BGGR) or gray source.</li>
<li>RAW viewing supports Bayer color preview (RGGB/GRBG/GBRG/BGGR) or grayscale preview.</li>
<li>YUV conversion uses RGB-to-YUV conversion with subformat-specific chroma sampling.</li>
</ul>
<h3>Sensor presets</h3>
<ul>
<li>The main <b>Preset</b> control is selection-only (no typing). It fills the panel fields;
click Apply to render. Its list shows recently used presets first, then unused names A-Z.</li>
<li>The funnel button browses <code>All</code>, <code>RAW</code>, <code>YUV</code>, or
<code>Standard images</code> (RAW includes Packed; YUV includes YOnly). A selected preset that is
filtered out shows <code>(Current preset hidden by filter)</code> and keeps the filled values.</li>
<li>Right-click empty panel space for <b>Save current as preset…</b> and
<b>Manage presets…</b>. Text/field controls keep their native context menus.</li>
<li>Name and structured search live only in the preset manager; its list is sorted A-Z
(pinyin for Chinese). Search accepts a plain name or bracket-only conditions:
<code>&lt;type:VALUE format:VALUE&gt;name</code>. Conditions are space-separated (the older
<code>+</code> separator also works). Supported keys are <code>type</code>/<code>t</code> and
<code>format</code>/<code>f</code>; they AND with the outside name. Examples:
<code>&lt;t:yuv f:yuyv&gt;</code>,
<code>&lt;type:raw format:raw10 packed&gt;name</code>.</li>
<li>Search is case-insensitive; format comparison ignores whitespace, so
<code>raw10 packed</code> and <code>raw10packed</code> match alike. Bare <code>type:yuv</code>
is only a name search. Invalid/unknown bracket requirements return no results.</li>
</ul>
<h3>Remembered parameters &amp; pinned parameters</h3>
<ul>
<li><b>Per-file memory</b>: only an explicit <b>Apply</b> that decodes <i>successfully</i> is
remembered, keyed by file name (base name only, case-insensitive as on Windows). Editing fields,
picking a preset, frame navigation, automatic/silent opens and remembered-parameter restores never
write the memory; <b>Apply these parameters to all items</b> records each successful item (it is a
bulk Apply). Re-opening a file with that name restores the parameters and decodes once; the status
bar shows <code>Restored remembered parameters (name)</code>. If the file no longer matches,
decoding fails silently with <code>Restored remembered parameters, but decoding failed</code> — no
dialog, and the record is kept. Up to 100 files are kept (least recently used dropped first).</li>
<li>Right-click empty panel space and choose <b>Forget remembered parameters for this file</b>
(enabled only when a record exists) to drop that one entry.</li>
<li><b>Pinned parameters</b> (pin button next to Preset, or <b>Ctrl+N</b>): the panel then always
shows the pinned set when you switch files, while each file keeps its own parameters untouched.
Press <b>Apply</b> to write the pinned set into the current file; a successful Apply also makes it
the new pinned set. Unpinning immediately restores the current file's own parameters. The pin
state and values are saved across restarts. Ctrl+N is ignored while a text field, spin box or combo
has focus, so it never interrupts typing.</li>
<li>Right-click → <b>Apply these parameters to all items</b> writes the current panel parameters
into every open file and decodes them one at a time (failures appear only in the final summary).
Each success is remembered per file name.</li>
</ul>
<h3>Productivity</h3>
<ul>
<li>Support dragging files into the main window to open quickly.</li>
<li>Conversion input supports drag-and-drop; output defaults to the folder configured in <b>Settings</b> (默认 <code>convert_out</code>，可自定义).</li>
<li>Saved image DPI can be configured in <b>Settings</b> for high-resolution exports.</li>
</ul>
"""
